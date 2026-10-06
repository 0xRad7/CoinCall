"""PayVault 测试网部署 + 冒烟（needs_funds 实跑，产物 deployments/testnet-968.json）。

当前纪元（epoch 2）：计价 token = 测试网真 USDT（0x75edC933…0fe3，decimals=6）。
PayVault 合约零改动（token 为构造器 immutable），仅重部署；MockUSDT 纪元退役存档。

流程：
① 连 rpc.bohr.life（POA 中间件 + bohr.life 直连 + UA 伪装）→ **签名前断言 chainId==968**；
② 私钥从环境变量 BOT_CHAIN_TEST_PRIVATE_KEY 读（无则解析 coincall-bot-chain-api/.env），
   永不打印私钥，日志只允许出现地址；
③ USDT 预检（symbol/decimals/operator 余额）→ 部署 PayVault(token=USDT, operator=测试 key)；
④ 冒烟：
   a) providerWithdraw(0 credits) → InsufficientCredits（规范行为，非任何管理锁）；
   b) 未授权 chargeWithSigBatch 单笔（operator 自签、无消费者签名）→ bad_signature 跳过；
   c) 真 USDT 全链路：operator 垫付 gas/USDT 给 anvil#1、anvil#2 → anvil#1 approve →
      自签 3 笔授权 → operator 批量上链 → 断言 3×Charged 与队列侧逐笔一致（金额/nonce）→
      同批重放 3×nonce_used（I2）→ anvil#2 providerWithdraw 到账 == credits →
      I4 审计（balanceOf == totalCredits，充值后与提现后各一次）；
   d) 新金库链上 DOMAIN_SEPARATOR 与黄金向量域（name/version/chainId）一致 +
      verifyingContract == 新地址（字节级重建比对）；
   e) 旧 MockUSDT 金库不动（totalCredits/余额前后一致）；
⑤ 写 deployments/testnet-968.json（双纪元结构：顶层=现纪元，epochs=退役纪元账本）。

用法：uv run python script/deploy_testnet.py
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eth_account import Account
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
    sign_send_wait,
)
from payvault.compile import load_artifact
from payvault.eip712 import (
    AUTHORIZATION_TYPE_STRING,
    DOMAIN_NAME,
    DOMAIN_VERSION,
    Authorization,
    domain_separator,
    nonce_from,
    sign_authorization,
)

ROOT = Path(__file__).resolve().parent.parent
DEPLOYMENT_PATH = ROOT / "deployments" / "testnet-968.json"
VECTORS_PATH = ROOT / "vectors" / "eip712_golden.json"
ENV_FALLBACK = Path("/Users/rad/Documents/S1&ETHwuhan/coincall-bot-chain-api/.env")
ENV_VAR_NAME = "BOT_CHAIN_TEST_PRIVATE_KEY"

# 测试网真 USDT（coincall-bot-chain-api app/core/chains.py 单一来源的镜像；decimals=6）
USDT_ADDRESS = "0x75edC9335175Fc0552D51D48439F229c10420fe3"
TOKEN_SYMBOL = "USDT"  # noqa: S105 - 代币符号字面量非密钥（ruff 误报）
TOKEN_DECIMALS = 6

# 公开 anvil 测试助记词派生（无私钥价值）：#1=消费者，#2=Provider
ANVIL_MNEMONIC = "test test test test test test test test test test test junk"
ANVIL_CONSUMER_PATH = "m/44'/60'/0'/0/1"
ANVIL_PROVIDER_PATH = "m/44'/60'/0'/0/2"

CONSUMER_GAS_BOT = Web3.to_wei("0.05", "ether")
PROVIDER_GAS_BOT = Web3.to_wei("0.02", "ether")
# 3 笔授权（金额, nonce）——队列侧唯一事实，Charged 事件逐笔与其比对
SMOKE_CHARGES: tuple[tuple[int, str], ...] = (
    (2 * 10**6, "0x000000000000000000000000000000000000000000000000000000000000aa01"),
    (3 * 10**6, "0x000000000000000000000000000000000000000000000000000000000000aa02"),
    (5 * 10**6, "0x000000000000000000000000000000000000000000000000000000000000aa03"),
)
SMOKE_TOTAL = sum(value for value, _ in SMOKE_CHARGES)
EPOCH_REASON = "token switch to real USDT"

USDT_ABI: list[dict[str, Any]] = [
    {
        "name": "transfer",
        "type": "function",
        "inputs": [{"name": "to", "type": "address"}, {"name": "value", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "approve",
        "type": "function",
        "inputs": [{"name": "spender", "type": "address"}, {"name": "value", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "symbol",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "string"}],
    },
    {
        "name": "decimals",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
    },
]

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
    signer: LocalAccount,
    vault: Contract,
    *,
    value: int,
    nonce: str | bytes,
    provider: str | None = None,
) -> dict[str, Any]:
    """signer 以消费者身份对部署合约域签一条授权并组装 batch 元素（nonce bytes32）。"""
    auth = Authorization(
        from_addr=signer.address,
        to=vault.address,
        value=value,
        valid_after=0,
        valid_before=4102444800,
        nonce=nonce_from(nonce),
    )
    signed = sign_authorization(signer, auth, TESTNET_CHAIN_ID, vault.address)
    return {
        "provider": Web3.to_checksum_address(provider or signer.address),
        "auth": signed.auth.as_typed_data(),
        "v": signed.v,
        "r": signed.r,
        "s": signed.s,
    }


def previous_epoch() -> dict[str, Any] | None:
    """读取现行 deployments 文件（若有）作为待退役纪元。"""
    if not DEPLOYMENT_PATH.exists():
        return None
    old = json.loads(DEPLOYMENT_PATH.read_text(encoding="utf-8"))
    if int(old.get("chainId", 0)) != TESTNET_CHAIN_ID:
        print("[FATAL] 现行 deployments 文件 chainId 异常，拒绝自动纪元迁移", file=sys.stderr)
        sys.exit(1)
    return old


def snapshot_old_vault(
    w3: Web3, old: dict[str, Any] | None
) -> tuple[tuple[int, int] | None, Contract | None, Contract | None]:
    """旧纪元金库状态快照（totalCredits, 旧 token 余额）。"""
    pay_vault_addr = (old or {}).get("payVault")
    if not pay_vault_addr:
        return None, None, None
    vault_abi = load_artifact("PayVault")["abi"]
    old_vault = w3.eth.contract(address=Web3.to_checksum_address(pay_vault_addr), abi=vault_abi)
    old_token_addr = (old or {}).get("token") or (old or {}).get("mockUsdt")
    if not isinstance(old_token_addr, str):
        return None, None, None
    old_token = w3.eth.contract(address=Web3.to_checksum_address(old_token_addr), abi=USDT_ABI)
    snapshot = (
        int(old_vault.functions.totalCredits().call()),
        int(old_token.functions.balanceOf(old_vault.address).call()),
    )
    return snapshot, old_vault, old_token


def main() -> None:
    w3 = connect_testnet()
    assert_chain_id(w3, TESTNET_CHAIN_ID)  # 签名前铁律：防 RPC 指错网络
    account = account_from_key(load_private_key())
    operator_address = Web3.to_checksum_address(account.address)
    balance = w3.eth.get_balance(operator_address)
    print(f"[1/7] 测试网 968 连接 OK，operator={operator_address}")
    print(f"[1/7] operator 余额 {Web3.from_wei(balance, 'ether')} BOT")
    if balance == 0:
        print("[FATAL] operator 无测试网 BOT 余额（需先领水）", file=sys.stderr)
        sys.exit(1)

    # ---- 真 USDT 预检 --------------------------------------------------------
    usdt = w3.eth.contract(address=Web3.to_checksum_address(USDT_ADDRESS), abi=USDT_ABI)
    symbol = str(usdt.functions.symbol().call())
    decimals = int(usdt.functions.decimals().call())
    operator_usdt = int(usdt.functions.balanceOf(operator_address).call())
    print(f"[2/7] USDT {USDT_ADDRESS} symbol={symbol} decimals={decimals}")
    print(f"[2/7] operator 持有 {operator_usdt / 10**decimals} USDT")
    if symbol != TOKEN_SYMBOL or decimals != TOKEN_DECIMALS:
        print(f"[FATAL] USDT 预检不符：期望 {TOKEN_SYMBOL}/{TOKEN_DECIMALS} 位", file=sys.stderr)
        sys.exit(1)
    if operator_usdt < SMOKE_TOTAL:
        print(f"[FATAL] operator USDT 不足冒烟所需 {SMOKE_TOTAL}", file=sys.stderr)
        sys.exit(1)

    old_epoch = previous_epoch()
    old_before, old_vault, old_token = snapshot_old_vault(w3, old_epoch)
    if old_epoch:
        print(f"[2/7] 退役纪元金库 {old_epoch['payVault']} 快照: {old_before}")

    # ---- 部署（合约零改动，token=真 USDT） ------------------------------------
    vault, vault_receipt = deploy_contract(
        w3, account, "PayVault", Web3.to_checksum_address(USDT_ADDRESS), operator_address
    )
    artifact = load_artifact("PayVault")
    print(f"[3/7] PayVault 部署于 {vault.address} (tx {vault_receipt['tx_hash']})")
    # bytecodeHash 以链上 eth_getCode 为权威（任何人可复算）；solc 产物 runtime 在
    # immutable 槽位是占位零，与链上 code 天然不同——不可直接比对（C-06）
    bytecode_hash = "0x" + Web3.keccak(bytes(w3.eth.get_code(vault.address))).hex()
    token_onchain = Web3.to_checksum_address(vault.functions.token().call())
    if token_onchain != Web3.to_checksum_address(USDT_ADDRESS):
        print(f"[FATAL] 新金库 token() != USDT: {token_onchain}", file=sys.stderr)
        sys.exit(1)

    smoke: dict[str, Any] = {}

    # ---- 冒烟 a：providerWithdraw(0 credits) → InsufficientCredits ------------
    revert_data = call_revert_data(
        vault.functions.providerWithdraw(operator_address, 1), operator_address
    )
    smoke["withdraw_zero_credits_reverts_insufficient_credits"] = bool(
        revert_data and revert_data.startswith("0x" + SELECTOR_INSUFFICIENT_CREDITS)
    )
    zero_ok = smoke["withdraw_zero_credits_reverts_insufficient_credits"]
    print(f"[4/7] providerWithdraw(0 credits) → InsufficientCredits: {zero_ok}")

    # ---- 冒烟 b：未授权 charge → bad_signature 跳过 ----------------------------
    other_consumer = Web3.to_checksum_address("0x0000000000000000000000000000000000001234")
    auth = Authorization(
        from_addr=other_consumer,  # operator 没有该消费者私钥
        to=vault.address,
        value=SMOKE_CHARGES[0][0],
        valid_after=0,
        valid_before=4102444800,
        nonce=nonce_from(  # 9527
            "0x0000000000000000000000000000000000000000000000000000000000002537"
        ),
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
    print(f"[4/7] 未授权 charge → bad_signature 跳过: {smoke['unauthorized_charge_failed']}")
    print(f"      tx {summary['tx_hash']}")

    # ---- 冒烟 c：真 USDT 全链路（anvil#1 消费者 / anvil#2 Provider） ------------
    Account.enable_unaudited_hdwallet_features()
    consumer = Account.from_mnemonic(ANVIL_MNEMONIC, account_path=ANVIL_CONSUMER_PATH)
    provider = Account.from_mnemonic(ANVIL_MNEMONIC, account_path=ANVIL_PROVIDER_PATH)
    smoke["consumer"] = Web3.to_checksum_address(consumer.address)
    smoke["provider"] = Web3.to_checksum_address(provider.address)

    # 垫付 gas + USDT
    sign_send_wait(w3, account, consumer.address, b"", value_wei=CONSUMER_GAS_BOT)
    sign_send_wait(w3, account, provider.address, b"", value_wei=PROVIDER_GAS_BOT)
    call_contract(w3, account, usdt.functions.transfer(consumer.address, SMOKE_TOTAL))
    print(f"[5/7] 已垫付 gas(BOT) 与 {SMOKE_TOTAL // 10**6} USDT 给 {smoke['consumer']}")

    # 消费者授权金库无限额度，并对 3 笔授权自签（provider=anvil#2）
    call_contract(w3, consumer, usdt.functions.approve(vault.address, 2**256 - 1))
    calls = [
        make_charge_call(consumer, vault, value=value, nonce=nonce, provider=provider.address)
        for value, nonce in SMOKE_CHARGES
    ]
    queue = [
        {"provider": provider.address, "from": consumer.address, "value": value, "nonce": nonce}
        for value, nonce in SMOKE_CHARGES
    ]

    summary = call_contract(w3, account, vault.functions.chargeWithSigBatch(calls))
    receipt = receipt_of(w3, summary)
    charged_events = events_of(vault, receipt, "Charged")
    smoke["charge_tx"] = summary["tx_hash"]
    smoke["charged_events"] = charged_events
    smoke["charged_matches_queue"] = charged_events == queue
    credits_after = int(vault.functions.credits(provider.address).call())
    smoke["credits_after_charge"] = credits_after
    vault_balance_after = int(usdt.functions.balanceOf(vault.address).call())
    smoke["i4_after_charge"] = (
        vault_balance_after == SMOKE_TOTAL == int(vault.functions.totalCredits().call())
    )
    print(f"[5/7] 3 笔 Charged == 队列: {smoke['charged_matches_queue']} (tx {summary['tx_hash']})")
    print(f"      credits={credits_after} I4={smoke['i4_after_charge']}")

    # 同批重放（I2）：3 笔应全部 nonce_used 且记账不变
    summary = call_contract(w3, account, vault.functions.chargeWithSigBatch(calls))
    replay_receipt = receipt_of(w3, summary)
    replay_reasons = [str(ev["reason"]) for ev in events_of(vault, replay_receipt, "ChargeFailed")]
    smoke["nonce_replay_rejected"] = (
        replay_reasons == ["nonce_used"] * len(SMOKE_CHARGES)
        and int(vault.functions.credits(provider.address).call()) == credits_after
    )
    smoke["replay_tx"] = summary["tx_hash"]
    print(f"[5/7] 同批重放 3×nonce_used: {smoke['nonce_replay_rejected']}")

    # Provider 提现（P8：路径恒开），到账 == credits
    provider_usdt_before = int(usdt.functions.balanceOf(provider.address).call())
    summary = call_contract(
        w3, provider, vault.functions.providerWithdraw(provider.address, credits_after)
    )
    withdraw_receipt = receipt_of(w3, summary)
    withdrawn = events_of(vault, withdraw_receipt, "Withdrawn")
    provider_usdt_after = int(usdt.functions.balanceOf(provider.address).call())
    smoke["withdraw_tx"] = summary["tx_hash"]
    smoke["withdrawn_event"] = withdrawn[0] if withdrawn else None
    smoke["withdraw_received"] = provider_usdt_after - provider_usdt_before
    smoke["withdraw_received_equals_credits"] = (
        provider_usdt_after - provider_usdt_before == credits_after
        and int(vault.functions.credits(provider.address).call()) == 0
    )
    print(
        f"[5/7] withdraw 到账 {smoke['withdraw_received']} == credits: "
        f"{smoke['withdraw_received_equals_credits']}"
    )

    # I4 终审计（提清后）
    smoke["i4_after_withdraw"] = int(usdt.functions.balanceOf(vault.address).call()) == int(
        vault.functions.totalCredits().call()
    )
    print(
        f"[5/7] I4 终审计: {usdt.functions.balanceOf(vault.address).call()} == "
        f"{vault.functions.totalCredits().call()}"
    )

    # ---- 冒烟 d：DOMAIN_SEPARATOR 与黄金向量域对账 -----------------------------
    vectors = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
    vdomain = vectors["domain"]
    expected_sep = domain_separator(TESTNET_CHAIN_ID, vault.address)
    onchain_sep = bytes(vault.functions.DOMAIN_SEPARATOR().call())
    smoke["domain_separator_matches_vector_domain"] = bool(
        vdomain["name"] == DOMAIN_NAME
        and vdomain["version"] == DOMAIN_VERSION
        and int(vdomain["chainId"]) == TESTNET_CHAIN_ID
        and onchain_sep == expected_sep
    )
    print(
        f"[6/7] DOMAIN_SEPARATOR 对账（向量域 name/version/chainId + 新地址）: "
        f"{smoke['domain_separator_matches_vector_domain']}"
    )

    # ---- 冒烟 e：旧 MockUSDT 金库不动 ------------------------------------------
    if (
        old_epoch is not None
        and old_vault is not None
        and old_token is not None
        and old_before is not None
    ):
        old_after = (
            int(old_vault.functions.totalCredits().call()),
            int(old_token.functions.balanceOf(old_vault.address).call()),
        )
        smoke["previous_epoch_vault_untouched"] = old_after == old_before
        retired_addr = old_epoch.get("payVault")
        print(f"[6/7] 旧金库 {retired_addr} 前后一致: {old_after == old_before}")

    ok = (
        smoke["withdraw_zero_credits_reverts_insufficient_credits"]
        and smoke["unauthorized_charge_failed"]
        and smoke["charged_matches_queue"]
        and len(charged_events) == len(SMOKE_CHARGES)
        and smoke["i4_after_charge"]
        and smoke["nonce_replay_rejected"]
        and smoke["withdraw_received_equals_credits"]
        and smoke["i4_after_withdraw"]
        and smoke["domain_separator_matches_vector_domain"]
        and smoke.get("previous_epoch_vault_untouched", True)
    )

    # ---- 部署事实落盘（双纪元结构） ---------------------------------------------
    epochs: list[dict[str, Any]] = []
    if old_epoch:
        epochs.append(
            {
                "epoch": int(old_epoch.get("epoch", 1)),
                "token": Web3.to_checksum_address(old_epoch.get("token") or old_epoch["mockUsdt"]),
                "tokenSymbol": "MockUSDT",
                "payVault": Web3.to_checksum_address(old_epoch["payVault"]),
                "deployTxHash": old_epoch.get("deployTxHash"),
                "retiredAt": datetime.now(UTC).isoformat(timespec="seconds"),
                "reason": EPOCH_REASON,
            }
        )
    deployment = {
        "epoch": (epochs[-1]["epoch"] + 1) if epochs else 1,
        "network": "botchain-testnet",
        "chainId": TESTNET_CHAIN_ID,
        "payVault": vault.address,
        "token": Web3.to_checksum_address(USDT_ADDRESS),
        "tokenSymbol": TOKEN_SYMBOL,
        "operator": operator_address,
        "deployTxHash": vault_receipt["tx_hash"],
        "blockNumber": int(vault_receipt["block_number"]),
        "compilerVersion": artifact["compilerVersion"],
        "deployTime": datetime.now(UTC).isoformat(timespec="seconds"),
        # 权威 runtime 哈希 = keccak(eth_getCode(payVault))；solc 产物 runtime 含 immutable
        # 占位零，与链上 code 不可直接比对（C-06）
        "bytecodeHash": bytecode_hash,
        "deployTxs": {"payVault": vault_receipt["tx_hash"]},
        "domainSeparator": "0x" + bytes(vault.functions.DOMAIN_SEPARATOR().call()).hex(),
        "eip712": {
            "domain": {"name": DOMAIN_NAME, "version": DOMAIN_VERSION, "chainId": TESTNET_CHAIN_ID},
            "structType": AUTHORIZATION_TYPE_STRING,
            "goldenVector": "vectors/eip712_golden.json",
        },
        "abiFiles": {"payVault": "artifacts/PayVault.json", "mockUsdt": "artifacts/MockUSDT.json"},
        "gasPriceWei": 20 * 10**9,
        "epochs": epochs,
        "smoke": smoke,
        "smokeAllPassed": ok,
    }
    DEPLOYMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(deployment, indent=2, ensure_ascii=False) + "\n"
    DEPLOYMENT_PATH.write_text(payload, encoding="utf-8")
    print(f"[7/7] 部署事实已写入 {DEPLOYMENT_PATH}")
    print(f"[7/7] 冒烟总判定: {'PASS' if ok else 'FAIL'}")
    if not ok:
        sys.exit(2)


if __name__ == "__main__":
    main()
