"""T15 needs_funds 实跑（09 P0-5 验收）：真实 PayVault 批量结算 + I4 对账。

前置：coincall-bot-chain-api 在 8010 在线（operator 由其 env 密钥签名）。
资金路径（全部经 bot-chain-api，本测试不引入任何生产私钥，A9：只用 anvil 测试助记词）：
  ① operator → consumer（anvil 助记词 #1）转 0.05 BOT 付 gas（POST /tx/transfer）；
  ② MockUSDT.mint(consumer, 1 USDT)（POST /contracts/send，from=operator）；
  ③ consumer 对 PayVault 无限 approve（本地签名 → POST /tx/send-raw）；
  ④ consumer 签 3 笔新 EIP-712 授权（coincall-contracts 的向量 nonce 已被合约冒烟
     用过，不可复用）→ 入 settle_queue → keeper.run_cycle 真实上链。

断言（I4 对账口径）：链上 Charged 事件数与金额 == 队列置 done 行数与金额；
credits(provider) 增量一致；token.balanceOf(vault) == totalCredits。
"""

import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from eth_account import Account
from eth_keys import keys as eth_keys
from web3 import Web3

from app.core.abis import view_abi
from app.core.payment import Authorization, XPayment, eip712_digest
from app.modules.calls import CallRecord, CallStatus, CallStore
from app.modules.keeper import BotChainSettleChain, Keeper, decode_charge_events

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.needs_funds

#: 事实源：coincall-contracts/deployments/testnet-968.json
PAY_VAULT = "0xFe91F55C0e7Ccbc4A6619C67544Ab453cf79C471"
MOCK_USDT = "0x4F8f2eaAA3988E9f59B72C93262DDC1084E540fb"
OPERATOR = "0xC37fFE97B4D2C3D0187B1dDEDF273E52A461B63a"
CHAIN_ID = 968
RPC = "https://rpc.bohr.life/"
BOTCHAIN = os.environ.get("COINCALL_BOT_CHAIN_API_BASE_URL", "http://127.0.0.1:8010")

#: anvil 公开测试助记词（与 coincall-contracts 冒烟同源口径；A9：仅测试助记词）
TEST_MNEMONIC = "test test test test test test test test test test test junk"
CHARGE_VALUE = 10_000  # 0.01 USDT ×3
MINT_AMOUNT = 1_000_000  # 1 USDT

MOCKUSDT_MINT_ABI = [
    {
        "type": "function",
        "name": "mint",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [],
    }
]
MOCKUSDT_ABI = [
    *MOCKUSDT_MINT_ABI,
    {
        "type": "function",
        "name": "approve",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "value", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "balanceOf",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]


async def _botchain_post(
    http: httpx.AsyncClient, path: str, payload: dict[str, Any]
) -> dict[str, Any]:
    resp = await http.post(f"{BOTCHAIN}/api/v1/{path}", json=payload)
    body = resp.json()
    assert resp.status_code == 200, f"{path} 失败({resp.status_code}): {body}"
    return dict(body)


async def _botchain_call(
    http: httpx.AsyncClient, to: str, method: str, args: list[Any], abi: list[Any]
) -> Any:
    resp = await http.post(
        f"{BOTCHAIN}/api/v1/contracts/call",
        json={"to": to, "abi": abi, "method": method, "args": args},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


def _sign_auth(private: eth_keys.PrivateKey, from_addr: str, nonce: bytes) -> XPayment:
    """用本仓冻结 digest 构造签 1 笔新授权（与 PayVault 域逐字节一致）。"""
    auth = Authorization(
        from_=from_addr,
        to=PAY_VAULT,
        value=str(CHARGE_VALUE),
        valid_after=int(time.time()) - 60,
        valid_before=int(time.time()) + 3600,
        nonce="0x" + nonce.hex(),
    )
    digest = eip712_digest(auth, PAY_VAULT, CHAIN_ID)
    sig = private.sign_msg_hash(digest)
    return XPayment(
        **auth.model_dump(by_alias=True),
        v=sig.v + 27,
        r="0x" + sig.r.to_bytes(32, "big").hex(),
        s="0x" + sig.s.to_bytes(32, "big").hex(),
    )


async def test_real_payvault_batch_settlement_and_reconciliation(tmp_path: Path) -> None:
    Account.enable_unaudited_hdwallet_features()
    consumer = Account.from_mnemonic(TEST_MNEMONIC, account_path="m/44'/60'/0'/0/1")
    provider_wallet = Account.from_mnemonic(TEST_MNEMONIC, account_path="m/44'/60'/0'/0/2").address
    consumer_key = eth_keys.PrivateKey(bytes(consumer.key))  # HexBytes → 32 字节

    async with httpx.AsyncClient(timeout=90.0, trust_env=False) as http:  # C-07：绕开系统代理
        # ① gas 供给（operator 经 bot-chain-api 签名）
        funded = await _botchain_post(
            http,
            "tx/transfer",
            {
                "from_address": OPERATOR,
                "to_address": consumer.address,
                "value_bot": "0.05",
                "dry_run": False,
            },
        )
        assert funded["status"] == 1
        # ② 消费者 USDT 供给（MockUSDT.mint 开放，from=operator 代付 gas）
        minted = await _botchain_post(
            http,
            "contracts/send",
            {
                "from_address": OPERATOR,
                "to": MOCK_USDT,
                "abi": MOCKUSDT_MINT_ABI,
                "method": "mint",
                "args": [consumer.address, MINT_AMOUNT],
                "value_wei": "0",
                "dry_run": False,
            },
        )
        assert minted["status"] == 1
        # ③ approve（consumer 本地签名 → send-raw；A9：测试助记词账户）
        w3 = Web3(Web3.HTTPProvider(RPC))
        usdt = w3.eth.contract(address=Web3.to_checksum_address(MOCK_USDT), abi=MOCKUSDT_ABI)
        approve_tx = usdt.functions.approve(
            Web3.to_checksum_address(PAY_VAULT), 2**256 - 1
        ).build_transaction(
            {
                "from": consumer.address,
                "nonce": w3.eth.get_transaction_count(consumer.address, "pending"),
                "gas": 60_000,
                "gasPrice": 20 * 10**9,
                "chainId": CHAIN_ID,
            }
        )
        raw = Account.sign_transaction(approve_tx, consumer.key).raw_transaction
        approved = await _botchain_post(
            http, "tx/send-raw", {"raw_tx": Web3.to_hex(raw), "wait": True}
        )
        assert approved["status"] == 1

        credits_before = int(
            await _botchain_call(http, PAY_VAULT, "credits", [provider_wallet], view_abi())
        )

        # ④ 3 笔新授权入队 → keeper 真实上链
        store = CallStore(str(tmp_path / "live.duckdb"))
        nonces = [os.urandom(32) for _ in range(3)]
        for i, nonce in enumerate(nonces, start=1):
            payment = _sign_auth(consumer_key, consumer.address, nonce)
            store.insert_call(
                CallRecord(
                    call_id=f"call_live_{i}",
                    service_id="svc_live",
                    provider_agent_id=137,
                    consumer_key_id=f"key_live_{i}",
                    consumer_wallet=consumer.address,
                    amount_raw=CHARGE_VALUE,
                    payment_nonce=payment.nonce,
                )
            )
            store.mark_call(f"call_live_{i}", CallStatus.SUCCESS)
            store.insert_settle(
                call_id=f"call_live_{i}",
                provider_token_id=137,
                auth=payment.model_dump(by_alias=True),
            )

        chain = BotChainSettleChain(
            base_url=BOTCHAIN,
            rpc_url=RPC,
            pay_vault=PAY_VAULT,
            operator_address=OPERATOR,
            http=http,
        )

        async def wallet_for(_row: dict[str, Any]) -> str:
            return provider_wallet

        keeper = Keeper(
            store=store,
            chain=chain,
            wallet_for=wallet_for,
            batch_size=3,  # 攒满即上链
        )
        report = await keeper.run_cycle()
        tx_hash = str(report["tx_hash"])

        # ---- I4 对账：链上 Charged == 队列 done ----
        assert report["charged"] == 3, report
        assert report["charged_raw"] == 3 * CHARGE_VALUE
        logs = await chain.receipt_logs(tx_hash)
        events = decode_charge_events(logs, PAY_VAULT)
        charged = [e for e in events if e.kind == "charged"]
        assert len(charged) == 3
        assert {str(e.nonce) for e in charged} == {"0x" + n.hex() for n in nonces}
        assert sum(int(e.value) for e in charged) == 3 * CHARGE_VALUE

        done = store.conn.execute(
            "SELECT count(*), sum(CAST(json_extract(auth_json, '$.value') AS BIGINT)) "
            "FROM settle_queue WHERE status = 'done'"
        ).fetchone()
        assert done[0] == 3 and int(done[1]) == 3 * CHARGE_VALUE
        assert set(store.conn.execute("SELECT status FROM calls").fetchall()) == {("settled",)}

        credits_after = int(
            await _botchain_call(http, PAY_VAULT, "credits", [provider_wallet], view_abi())
        )
        assert credits_after - credits_before == 3 * CHARGE_VALUE

        vault_balance = int(
            await _botchain_call(http, MOCK_USDT, "balanceOf", [PAY_VAULT], MOCKUSDT_ABI)
        )
        total_credits = int(await _botchain_call(http, PAY_VAULT, "totalCredits", [], view_abi()))
        assert vault_balance == total_credits  # I4：链上即对账

        logger.info(
            "needs_funds 实跑: tx=%s charged=3 sum=%d credits(+%d) I4=%s",
            tx_hash,
            3 * CHARGE_VALUE,
            credits_after - credits_before,
            vault_balance == total_credits,
        )
        store.close()
