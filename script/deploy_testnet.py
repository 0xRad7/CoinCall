"""PayVault 测试网部署 + 冒烟（needs_funds 实跑，产物 deployments/testnet-968.json）。

流程：
① 连 rpc.bohr.life（POA 中间件 + bohr.life 直连 + UA 伪装）→ **签名前断言 chainId==968**；
② 私钥从环境变量 BOT_CHAIN_TEST_PRIVATE_KEY 读（无则解析 coincall-bot-chain-api/.env），
   永不打印私钥，日志只允许出现地址；
③ 部署顺序：MockUSDT（给测试 key mint 足量）→ PayVault(token=MockUSDT, operator=测试 key)；
④ 冒烟（04 §5 验收 + 09 P0-2）：
   a) providerWithdraw(0 credits) 行为符合规范（InsufficientCredits revert，非任何管理锁）；
   b) 未授权 chargeWithSigBatch 单笔（operator 自签、无消费者签名）必须失败（bad_signature 跳过）；
   c) 全链路回环：approve → 单笔 charge → credits 断言 → 同 nonce 重放失败(I2) → withdraw 到账
      → I4 审计（合约 token 余额 == totalCredits）；
⑤ 写 deployments/testnet-968.json（其他仓接线唯一事实来源）。

用法：uv run python script/deploy_testnet.py
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ContractLogicError

from payvault.chain import (
    TESTNET_CHAIN_ID,
    account_from_key,
    assert_chain_id,
    call_contract,
    connect_testnet,
    deploy_contract,
)
from payvault.compile import load_artifact
from payvault.eip712 import AUTHORIZATION_TYPE_STRING, Authorization, sign_authorization

ROOT = Path(__file__).resolve().parent.parent
DEPLOYMENT_PATH = ROOT / "deployments" / "testnet-968.json"
ENV_FALLBACK = Path("/Users/rad/Documents/S1&ETHwuhan/coincall-bot-chain-api/.env")
ENV_VAR_NAME = "BOT_CHAIN_TEST_PRIVATE_KEY"

MINT_AMOUNT = 1_000_000 * 10**6  # 100 万 MockUSDT
SMOKE_AMOUNT = 10 * 10**6  # 冒烟扣款 10 USDT
SELECTOR_INSUFFICIENT_CREDITS = Web3.keccak(text="InsufficientCredits(address,uint256,uint256)")[
    :4
].hex()


def load_private_key() -> str:
    """环境变量优先，缺省解析 bot-chain-api 的 .env；找不到即退出。私钥永不回显。"""
    key = __import__("os").environ.get(ENV_VAR_NAME, "").strip()
    if key:
        return key
    if ENV_FALLBACK.exists():
        for line in ENV_FALLBACK.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{ENV_VAR_NAME}="):
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    print(f"[FATAL] 未找到 {ENV_VAR_NAME}（环境变量或 {ENV_FALLBACK}）", file=sys.stderr)
    sys.exit(1)


def events_of(contract: Contract, receipt: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """解码回执指定事件（只喂本合约地址的日志）。"""
    event = getattr(contract.events, name)()
    only_self = dict(receipt)
    only_self["logs"] = [log for log in receipt["logs"] if log["address"] == contract.address]
    from web3._utils.events import EventLogErrorFlags  # noqa: PLC0415 - web3 内部枚举

    return [
        {
            key: (Web3.to_hex(value) if isinstance(value, bytes) else value)
            for key, value in ev["args"].items()
        }
        for ev in event.process_receipt(only_self, errors=EventLogErrorFlags.Discard)
        if not ev.get("removed")
    ]


def receipt_of(w3: Web3, summary: dict[str, Any]) -> dict[str, Any]:
    return dict(w3.eth.get_transaction_receipt(summary["tx_hash"]))


def call_revert_data(func: Any, from_address: str) -> str | None:  # noqa: ANN401
    """eth_call 的 revert 数据（hexstr）；调用成功返回 None。"""
    try:
        func.call({"from": Web3.to_checksum_address(from_address)})
    except ContractLogicError as exc:
        return str(exc.data) if exc.data else ""
    return None


def make_charge_call(
    signer: LocalAccount, vault: Contract, *, value: int, nonce: int
) -> dict[str, Any]:
    """signer 以消费者身份对部署合约域签一条授权并组装 batch 元素。"""
    auth = Authorization(
        from_addr=signer.address,
        to=vault.address,
        value=value,
        valid_after=0,
        valid_before=4102444800,
        nonce=nonce,
    )
    signed = sign_authorization(signer, auth, TESTNET_CHAIN_ID, vault.address)
    return {
        "provider": signer.address,
        "auth": signed.auth.as_typed_data(),
        "v": signed.v,
        "r": signed.r,
        "s": signed.s,
    }


def main() -> None:
    w3 = connect_testnet()
    assert_chain_id(w3, TESTNET_CHAIN_ID)  # 签名前铁律：防 RPC 指错网络
    account = account_from_key(load_private_key())
    operator_address = Web3.to_checksum_address(account.address)
    balance = w3.eth.get_balance(operator_address)
    print(f"[1/6] 测试网 968 连接 OK，operator={operator_address}")
    print(f"[1/6] operator 余额 {Web3.from_wei(balance, 'ether')} BOT")
    if balance == 0:
        print("[FATAL] operator 无测试网 BOT 余额（需先领水）", file=sys.stderr)
        sys.exit(1)

    # ---- 部署 ---------------------------------------------------------------
    mock_usdt, mock_receipt = deploy_contract(w3, account, "MockUSDT")
    print(f"[2/6] MockUSDT 部署于 {mock_usdt.address} (tx {mock_receipt['tx_hash']})")
    call_contract(w3, account, mock_usdt.functions.mint(operator_address, MINT_AMOUNT))
    print(f"[2/6] 已 mint {MINT_AMOUNT // 10**6} MockUSDT 给 {operator_address}")

    vault, vault_receipt = deploy_contract(
        w3, account, "PayVault", mock_usdt.address, operator_address
    )
    print(f"[3/6] PayVault 部署于 {vault.address} (tx {vault_receipt['tx_hash']})")
    artifact = load_artifact("PayVault")
    # bytecodeHash 以链上 eth_getCode 为权威（任何人可复算）。
    # 注意：PayVault 含 immutable（token/operator/DOMAIN_SEPARATOR），solc 产物 runtime
    # 在 immutable 槽位是占位零，与链上 code 天然不同——两者不可直接比对。
    bytecode_hash = "0x" + Web3.keccak(bytes(w3.eth.get_code(vault.address))).hex()
    mock_code_hash = "0x" + Web3.keccak(bytes(w3.eth.get_code(mock_usdt.address))).hex()
    mock_artifact = load_artifact("MockUSDT")
    if mock_code_hash != mock_artifact["deployedBytecodeHash"]:
        print("[WARN] MockUSDT（无 immutable）链上 code 与产物不一致，需人工排查", file=sys.stderr)

    smoke: dict[str, Any] = {}

    # ---- 冒烟 a：providerWithdraw(0 credits) 符合规范（P8：无锁，只是没账可提） ----
    revert_data = call_revert_data(
        vault.functions.providerWithdraw(operator_address, 1), operator_address
    )
    smoke["withdraw_zero_credits_reverts_insufficient_credits"] = bool(
        revert_data and revert_data.startswith("0x" + SELECTOR_INSUFFICIENT_CREDITS)
    )
    zero_ok = smoke["withdraw_zero_credits_reverts_insufficient_credits"]
    print(f"[4/6] providerWithdraw(0 credits) → InsufficientCredits: {zero_ok}")

    # ---- 冒烟 b：未授权 charge（operator 自签，无消费者签名）必须失败 -----------
    other_consumer = Web3.to_checksum_address("0x0000000000000000000000000000000000001234")
    auth = Authorization(
        from_addr=other_consumer,  # operator 没有该消费者私钥
        to=vault.address,
        value=SMOKE_AMOUNT,
        valid_after=0,
        valid_before=4102444800,
        nonce=9527,
    )
    forged = sign_authorization(account, auth, TESTNET_CHAIN_ID, vault.address)
    unauthorized = {
        "provider": operator_address,
        "auth": forged.auth.as_typed_data(),
        "v": forged.v,
        "r": forged.r,
        "s": forged.s,
    }
    summary = call_contract(w3, account, vault.functions.chargeWithSigBatch([unauthorized]))
    receipt = receipt_of(w3, summary)
    failures = [str(ev["reason"]) for ev in events_of(vault, receipt, "ChargeFailed")]
    charged = events_of(vault, receipt, "Charged")
    smoke["unauthorized_charge_failed"] = failures == ["bad_signature"] and not charged
    smoke["unauthorized_charge_tx"] = summary["tx_hash"]
    print(f"[4/6] 未授权 charge → bad_signature 跳过: {smoke['unauthorized_charge_failed']}")
    print(f"      tx {summary['tx_hash']}")

    # ---- 冒烟 c：全链路回环 approve→charge→重放→withdraw→I4 --------------------
    call_contract(w3, account, mock_usdt.functions.approve(vault.address, 10 * SMOKE_AMOUNT))
    charge_call = make_charge_call(account, vault, value=SMOKE_AMOUNT, nonce=1)

    summary = call_contract(w3, account, vault.functions.chargeWithSigBatch([charge_call]))
    receipt = receipt_of(w3, summary)
    charged_events = events_of(vault, receipt, "Charged")
    credits_after_charge = int(vault.functions.credits(operator_address).call())
    smoke["charge_tx"] = summary["tx_hash"]
    smoke["charged_event"] = charged_events[0] if charged_events else None
    smoke["credits_after_charge"] = credits_after_charge
    print(f"[5/6] charge 10 USDT → credits={credits_after_charge}")
    print(f"      Charged 事件: {bool(charged_events)}")

    # 同 nonce 重放（I2）
    summary = call_contract(w3, account, vault.functions.chargeWithSigBatch([charge_call]))
    replay_receipt = receipt_of(w3, summary)
    replay_reasons = [str(ev["reason"]) for ev in events_of(vault, replay_receipt, "ChargeFailed")]
    smoke["nonce_replay_rejected"] = (
        replay_reasons == ["nonce_used"]
        and int(vault.functions.credits(operator_address).call()) == credits_after_charge
    )
    smoke["replay_tx"] = summary["tx_hash"]
    print(f"[5/6] 同 nonce 重放 → nonce_used: {smoke['nonce_replay_rejected']}")

    # 提现（P8：路径恒开）
    summary = call_contract(
        w3, account, vault.functions.providerWithdraw(operator_address, credits_after_charge)
    )
    withdraw_receipt = receipt_of(w3, summary)
    withdrawn = events_of(vault, withdraw_receipt, "Withdrawn")
    balance_after = int(mock_usdt.functions.balanceOf(operator_address).call())
    smoke["withdraw_tx"] = summary["tx_hash"]
    smoke["withdrawn_event"] = withdrawn[0] if withdrawn else None
    smoke["balance_after_withdraw"] = balance_after
    print(f"[5/6] withdraw → 到账 {balance_after / 10**6} USDT")

    # I4 审计
    vault_token_balance = int(mock_usdt.functions.balanceOf(vault.address).call())
    total_credits = int(vault.functions.totalCredits().call())
    smoke["i4_balance_equals_credits"] = vault_token_balance == total_credits
    print(f"[5/6] I4 审计: 合约余额 {vault_token_balance} == Σ未提现 credits {total_credits}")

    ok = (
        smoke["withdraw_zero_credits_reverts_insufficient_credits"]
        and smoke["unauthorized_charge_failed"]
        and bool(charged_events)
        and smoke["nonce_replay_rejected"]
        and bool(withdrawn)
        and smoke["i4_balance_equals_credits"]
    )

    # ---- 部署事实落盘 ---------------------------------------------------------
    deployment = {
        "network": "botchain-testnet",
        "chainId": TESTNET_CHAIN_ID,
        "payVault": vault.address,
        "mockUsdt": mock_usdt.address,
        "operator": operator_address,
        "deployTxHash": vault_receipt["tx_hash"],
        "blockNumber": int(vault_receipt["block_number"]),
        "compilerVersion": artifact["compilerVersion"],
        "deployTime": datetime.now(UTC).isoformat(timespec="seconds"),
        # 权威 runtime 哈希 = keccak(eth_getCode(payVault))；solc 产物 runtime 含 immutable
        # 占位零（token/operator/DOMAIN_SEPARATOR 三个槽位），与链上 code 不可直接比对
        "bytecodeHash": bytecode_hash,
        "mockUsdtBytecodeHash": mock_code_hash,
        "deployTxs": {
            "mockUsdt": mock_receipt["tx_hash"],
            "payVault": vault_receipt["tx_hash"],
        },
        "domainSeparator": "0x" + bytes(vault.functions.DOMAIN_SEPARATOR().call()).hex(),
        "eip712": {
            "domain": {"name": "PayVault", "version": "1", "chainId": TESTNET_CHAIN_ID},
            "structType": AUTHORIZATION_TYPE_STRING,
            "goldenVector": "vectors/eip712_golden.json",
        },
        "abiFiles": {"payVault": "artifacts/PayVault.json", "mockUsdt": "artifacts/MockUSDT.json"},
        "gasPriceWei": 20 * 10**9,
        "smoke": smoke,
        "smokeAllPassed": ok,
    }
    DEPLOYMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(deployment, indent=2, ensure_ascii=False) + "\n"
    DEPLOYMENT_PATH.write_text(payload, encoding="utf-8")
    print(f"[6/6] 部署事实已写入 {DEPLOYMENT_PATH}")
    print(f"[6/6] 冒烟总判定: {'PASS' if ok else 'FAIL'}")
    if not ok:
        sys.exit(2)


if __name__ == "__main__":
    main()
