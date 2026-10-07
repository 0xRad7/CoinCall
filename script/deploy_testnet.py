"""PayVault 部署 + 冒烟（网络驱动；产物 deployments/<network>-<chainId>.json）。

用法：
    uv run python script/deploy_testnet.py                    # 测试网（默认）：部署+全量冒烟
    uv run python script/deploy_testnet.py --network mainnet  # 主网 dry-run（签名前 guard 退出）
    uv run python script/deploy_testnet.py --network mainnet --yes-i-will-deploy
        # ↑ 主网真部署（仅发起人手动执行）

网络表单一事实源：payvault/networks.py（testnet={rpc.bohr.life, 968, USDT 0x75edC9…0fe3}；
mainnet={rpc.botchain.ai, 677, USDT 0xaBabc7…87a3C}），--rpc/--chain-id/--token 可覆写
（RPC 仍受 bohr.life/botchain.ai 域白名单约束）。

签名者与 operator：
- 私钥按网络读取（load_private_key）：testnet=BOT_CHAIN_TEST_PRIVATE_KEY（缺省解析
  bot-chain-api/.env，现行行为）；mainnet=仅 env DEPLOYER_PRIVATE_KEY（0x-hex 或 0600
  文件路径）——绝不回退测试 .env，绝不用 anvil 助记词路径（mainnet-readiness.md §0 红线）；
- operator=PayVault 构造器第二参：--operator 覆写 > 主网默认 keystore 托管地址
  （0xb1ea…033A）> 测试网=署名者本人；
- 签名前断言链上 chainId == 目标网络链 ID（铁律保留，两网通用）。

主网红线（mainnet-readiness.md §0）：主网模式默认只 dry-run——连接 + chainId 断言 +
USDT/symbol/decimals 只读预检 + deployer 余额只读检查 + 构造 calldata（离线），
随后在签名前退出并打印待确认摘要；唯 "--yes-i-will-deploy" 双确认后才真部署
（由发起人手动执行，自动化永不携带该旗标）。主网不跑自动冒烟（anvil 垫付/批量
charge/提现仅测试网有）：真金最小冒烟由发起人按 README「主网部署（人工执行）」清单手动完成。

测试网流程（纪元 2，计价 token=测试网真 USDT，合约零改动仅重部署）：
① 连 rpc.bohr.life（POA 中间件 + 直连 + UA 伪装）→ **签名前断言 chainId==968**；
② USDT 预检（symbol/decimals/operator 余额）→ 部署 PayVault(token=USDT, operator=署名者)；
③ 冒烟：
   a) providerWithdraw(0 credits) → InsufficientCredits（规范行为，非任何管理锁）；
   b) 未授权 chargeWithSigBatch 单笔（operator 自签、无消费者签名）→ bad_signature 跳过；
   c) 真 USDT 全链路：operator 垫付 gas/USDT 给 anvil#1、anvil#2 → anvil#1 approve →
      自签 3 笔授权 → operator 批量上链 → 断言 3×Charged 与队列侧逐笔一致（金额/nonce）→
      同批重放 3×nonce_used（I2）→ anvil#2 providerWithdraw 到账 == credits →
      I4 审计（balanceOf == totalCredits，充值后与提现后各一次）；
   d) 新金库链上 DOMAIN_SEPARATOR 与黄金向量域（name/version/chainId）一致 +
      verifyingContract == 新地址（字节级重建比对）；
   e) 旧 MockUSDT 金库不动（totalCredits/余额前后一致）；
④ 写 deployments/testnet-968.json（双纪元结构：顶层=现纪元，epochs=退役纪元账本）。
"""

import argparse
import json
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import ChecksumAddress
from web3 import Web3
from web3.contract import Contract
from web3.exceptions import ContractLogicError

from payvault.chain import (
    GAS_PRICE_WEI,
    account_from_key,
    assert_chain_id,
    bytecode_str,
    call_contract,
    connect,
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
from payvault.networks import (
    MAINNET_DEFAULT_OPERATOR,
    NETWORKS,
    Network,
    network_with_overrides,
)

ROOT = Path(__file__).resolve().parent.parent
DEPLOYMENTS_DIR = ROOT / "deployments"
VECTORS_PATH = ROOT / "vectors" / "eip712_golden.json"
ENV_FALLBACK = Path("/Users/rad/Documents/S1&ETHwuhan/coincall-bot-chain-api/.env")
ENV_VAR_NAME = "BOT_CHAIN_TEST_PRIVATE_KEY"
DEPLOYER_KEY_ENV = "DEPLOYER_PRIVATE_KEY"

# 公开 anvil 测试助记词派生（无私钥价值）：#1=消费者，#2=Provider——仅测试网冒烟使用
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """网络驱动 CLI：--network 选网，--rpc/--chain-id/--token 覆写，主网真部署需双确认旗标。"""
    parser = argparse.ArgumentParser(
        prog="deploy_testnet.py",
        description=(
            "PayVault 部署（网络驱动）：testnet=全量部署+冒烟；"
            "mainnet=默认 dry-run（签名前停止），真部署需 --yes-i-will-deploy（仅发起人手动）"
        ),
    )
    parser.add_argument(
        "--network",
        choices=sorted(NETWORKS),
        default="testnet",
        help="目标网络（默认 testnet；网络表见 payvault/networks.py）",
    )
    parser.add_argument("--rpc", help="覆写网络表 RPC（仍受 bohr.life/botchain.ai 域白名单约束）")
    parser.add_argument(
        "--chain-id", type=int, dest="chain_id", help="覆写链 ID（签名前 chainId 断言以此为准）"
    )
    parser.add_argument("--token", help="覆写计价 token 地址（PayVault 构造器第一参）")
    parser.add_argument(
        "--operator",
        help="PayVault 构造器第二参（主网默认 keystore 托管地址，测试网默认=署名者）",
    )
    parser.add_argument(
        "--yes-i-will-deploy",
        action="store_true",
        help="主网真实部署双重确认；缺省 mainnet 只 dry-run 到签名前停止",
    )
    return parser.parse_args(argv)


def resolve_deployer_key(raw: str) -> str:
    """DEPLOYER_PRIVATE_KEY 的值 → 私钥 hex：0x-hex 直读；否则视为 0600 权限文件路径。

    私钥永不回显（报错只含文件路径与权限位，不含内容）。
    """
    value = raw.strip()
    if value.lower().startswith("0x"):
        return value
    path = Path(value).expanduser()
    if not path.is_file():
        # 容错：64 位裸 hex（漏写 0x）按私钥归一
        if len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value):
            return "0x" + value.lower()
        print("[FATAL] DEPLOYER_PRIVATE_KEY 既非 0x-hex 也非存在的文件路径", file=sys.stderr)
        sys.exit(1)
    mode = path.stat().st_mode & 0o777
    if mode != 0o600:
        print(f"[FATAL] 私钥文件权限必须 0600，实际 {oct(mode)}: {path}", file=sys.stderr)
        sys.exit(1)
    content = path.read_text(encoding="utf-8").strip()
    if not content.lower().startswith("0x"):
        if len(content) == 64 and all(c in "0123456789abcdefABCDEF" for c in content):
            content = "0x" + content.lower()
        else:
            print(f"[FATAL] 私钥文件内容不是 0x-hex 私钥: {path}", file=sys.stderr)
            sys.exit(1)
    return content


def load_private_key(network: str, env: Mapping[str, str] | None = None) -> str:
    """按网络读署名私钥（永不打印，日志只允许出现地址）。

    - mainnet：仅 env DEPLOYER_PRIVATE_KEY（0x-hex 或 0600 文件路径）——绝不回退测试 .env、
      绝不用 anvil 助记词路径（mainnet-readiness.md §0 红线）；
    - testnet：env BOT_CHAIN_TEST_PRIVATE_KEY，缺省解析 bot-chain-api/.env（现行行为）。
    """
    env_map = os.environ if env is None else env
    if network == "mainnet":
        raw = env_map.get(DEPLOYER_KEY_ENV, "").strip()
        if not raw:
            print(
                f"[FATAL] 主网部署需要 env {DEPLOYER_KEY_ENV}（0x-hex 或 0600 文件路径）；"
                "不回退测试 .env / anvil 助记词",
                file=sys.stderr,
            )
            sys.exit(1)
        return resolve_deployer_key(raw)
    key = env_map.get(ENV_VAR_NAME, "").strip()
    if key:
        return key
    if ENV_FALLBACK.exists():
        for line in ENV_FALLBACK.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{ENV_VAR_NAME}="):
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    print(f"[FATAL] 未找到 {ENV_VAR_NAME}（环境变量或 {ENV_FALLBACK}）", file=sys.stderr)
    sys.exit(1)


_ZERO_ADDRESS = "0x" + "0" * 40


def assert_nonzero_deploy_params(token: str, operator: str, deployer: str) -> None:
    """审计 F-02 缓解（2026-10-08）：合约 constructor 无零地址校验且 immutable——
    部署前脚本侧断言 token/operator/deployer 均非零地址，零值直接拒部署。"""
    for name, addr in (("token", token), ("operator", operator), ("deployer", deployer)):
        if addr.lower() == _ZERO_ADDRESS:
            msg = f"部署参数 {name} 为零地址（0x0）——拒绝部署（审计 F-02 缓解）"
            raise SystemExit(f"[FATAL] {msg}")


def resolve_operator(net: Network, args: argparse.Namespace, deployer: str) -> ChecksumAddress:
    """operator=PayVault 构造器第二参：--operator 覆写 > 主网 keystore 默认 > 测试网=署名者。"""
    if args.operator:
        return Web3.to_checksum_address(args.operator)
    if net.name == "mainnet":
        return Web3.to_checksum_address(MAINNET_DEFAULT_OPERATOR)
    return Web3.to_checksum_address(deployer)


def mainnet_dry_run_guard(confirmed: bool) -> None:
    """主网签名前守卫：未显式双确认即在签名前退出（exit 0=dry-run 按预期停止，非错误）。"""
    if confirmed:
        print(
            "[GUARD] --yes-i-will-deploy 已显式确认：继续真实部署（主网真金，仅限发起人手动执行）"
        )
        return
    print("[GUARD] 未传 --yes-i-will-deploy：已在签名前停止——未签名任何交易、未发送任何交易。")
    print(
        "[GUARD] 人工真部署命令: "
        "uv run python script/deploy_testnet.py --network mainnet --yes-i-will-deploy"
    )
    sys.exit(0)


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
    chain_id: int,
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
    signed = sign_authorization(signer, auth, chain_id, vault.address)
    return {
        "provider": Web3.to_checksum_address(provider or signer.address),
        "auth": signed.auth.as_typed_data(),
        "v": signed.v,
        "r": signed.r,
        "s": signed.s,
    }


def previous_epoch(deployment_path: Path, chain_id: int) -> dict[str, Any] | None:
    """读取现行 deployments 文件（若有）作为待退役纪元。"""
    if not deployment_path.exists():
        return None
    old = json.loads(deployment_path.read_text(encoding="utf-8"))
    if int(old.get("chainId", 0)) != chain_id:
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


# ============================== 主网（dry-run / 人工真部署） ==============================


def run_mainnet(w3: Web3, net: Network, args: argparse.Namespace) -> None:
    """主网流程：默认 dry-run（只读 + 离线构造 calldata，签名前 guard 退出）。

    --yes-i-will-deploy 后才真部署（发起人手动执行）：只部署 PayVault 与链上核验，
    不跑任何自动冒烟（anvil 垫付/批量 charge/提现是测试网专属；真金最小冒烟人工执行，
    清单见 README「主网部署（人工执行）」）。
    """
    account = account_from_key(load_private_key("mainnet"))
    deployer = Web3.to_checksum_address(account.address)
    operator = resolve_operator(net, args, deployer)
    assert_nonzero_deploy_params(token=net.token_address, operator=operator, deployer=deployer)
    artifact = load_artifact("PayVault")

    # ---- 只读预检（不签名、不发交易） ------------------------------------------
    balance = w3.eth.get_balance(deployer)
    print(f"[1/4] 主网 chainId={net.chain_id} 连接 OK（{net.rpc_url}）")
    print(f"[1/4] deployer={deployer} 余额 {Web3.from_wei(balance, 'ether')} BOT")
    print(f"[1/4] operator={operator}（keystore 托管地址）")

    usdt = w3.eth.contract(address=Web3.to_checksum_address(net.token), abi=USDT_ABI)
    symbol = str(usdt.functions.symbol().call())
    decimals = int(usdt.functions.decimals().call())
    operator_usdt = int(usdt.functions.balanceOf(operator).call())
    print(f"[2/4] 主网 USDT {net.token} symbol={symbol} decimals={decimals}")
    print(f"[2/4] operator 持有 {operator_usdt / 10**decimals} {symbol}")
    if symbol != net.token_symbol or decimals != net.token_decimals:
        print(
            f"[FATAL] 主网 USDT 预检不符：期望 {net.token_symbol}/{net.token_decimals} 位",
            file=sys.stderr,
        )
        sys.exit(1)

    # ---- 离线构造部署 calldata（不签名） ---------------------------------------
    factory = w3.eth.contract(abi=artifact["abi"], bytecode=bytecode_str(artifact["bytecode"]))
    ctor_calldata = factory.constructor(
        Web3.to_checksum_address(net.token), operator
    ).data_in_transaction
    print(f"[3/4] 构造 calldata（离线，未签名）: PayVault(token={net.token}, operator={operator})")
    print(
        f"      calldata={len(ctor_calldata)} 字节; gas 口径 {GAS_PRICE_WEI // 10**9} gwei"
        f"（链上 eth_gasPrice={Web3.from_wei(w3.eth.gas_price, 'gwei')} gwei）"
    )

    # ---- 待确认摘要 + 签名前守卫 ------------------------------------------------
    print("[4/4] === 主网部署待确认摘要（dry-run） ===")
    print(f"      network={net.name} chainId={net.chain_id} rpc={net.rpc_url}")
    print(f"      deployer={deployer}（余额 {Web3.from_wei(balance, 'ether')} BOT）")
    print(f"      PayVault(token={net.token}, operator={operator})")
    print(f"      产物 deployments/{net.deployment_file}（网络分文件，不触碰其他网络产物）")
    print(f"      源码验证 {net.explorer_url}（Blockscout，solc {artifact['compilerVersion']}）")
    if balance == 0:
        print("[WARN] deployer 主网 BOT 余额为 0：真部署会因无 gas 失败（经桥/DEX 入金后再执行）")

    mainnet_dry_run_guard(args.yes_i_will_deploy)

    # ---- 真部署（以下仅发起人手动带 --yes-i-will-deploy 才会到达） ----------------
    if balance == 0:
        print("[FATAL] deployer 无主网 BOT 余额，拒绝部署", file=sys.stderr)
        sys.exit(1)
    vault, vault_receipt = deploy_contract(
        w3, account, "PayVault", Web3.to_checksum_address(net.token), operator
    )
    print(f"[DEPLOY] PayVault 部署于 {vault.address} (tx {vault_receipt['tx_hash']})")
    bytecode_hash = "0x" + Web3.keccak(bytes(w3.eth.get_code(vault.address))).hex()
    onchain_sep = bytes(vault.functions.DOMAIN_SEPARATOR().call())
    checks = {
        "token_matches": Web3.to_checksum_address(vault.functions.token().call())
        == Web3.to_checksum_address(net.token),
        "operator_matches": Web3.to_checksum_address(vault.functions.operator().call()) == operator,
        # EIP-712 域含 chainId+verifyingContract：主网必须 677+新地址重建（黄金向量不含地址可复用）
        "domain_separator_matches_rebuild": onchain_sep
        == domain_separator(net.chain_id, vault.address),
    }
    for name, ok in checks.items():
        print(f"[DEPLOY] {name}: {ok}")
    if not all(checks.values()):
        print("[FATAL] 主网部署链上核验未全部通过", file=sys.stderr)
        sys.exit(2)

    deployment = {
        "epoch": 1,  # 主网新纪元文件，从 1 起（与 testnet-968.json 互不覆盖）
        "network": net.network_label,
        "chainId": net.chain_id,
        "payVault": vault.address,
        "token": Web3.to_checksum_address(net.token),
        "tokenSymbol": net.token_symbol,
        "operator": operator,
        "deployTxHash": vault_receipt["tx_hash"],
        "blockNumber": int(vault_receipt["block_number"]),
        "compilerVersion": artifact["compilerVersion"],
        "deployTime": datetime.now(UTC).isoformat(timespec="seconds"),
        "bytecodeHash": bytecode_hash,
        "deployTxs": {"payVault": vault_receipt["tx_hash"]},
        "domainSeparator": "0x" + onchain_sep.hex(),
        "eip712": {
            "domain": {"name": DOMAIN_NAME, "version": DOMAIN_VERSION, "chainId": net.chain_id},
            "structType": AUTHORIZATION_TYPE_STRING,
            "goldenVector": "vectors/eip712_golden.json",
        },
        "abiFiles": {"payVault": "artifacts/PayVault.json"},  # MockUSDT 绝不上主网
        "gasPriceWei": GAS_PRICE_WEI,
        "epochs": [],
        # 主网无自动冒烟（真金）：mode=manual + 部署时链上核验；人工最小冒烟见 README
        "smoke": {"mode": "manual", **checks},
        "smokeAllPassed": all(checks.values()),
    }
    deployment_path = DEPLOYMENTS_DIR / net.deployment_file
    deployment_path.parent.mkdir(parents=True, exist_ok=True)
    deployment_path.write_text(
        json.dumps(deployment, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"[DEPLOY] 部署事实已写入 {deployment_path}")
    print("[NEXT] 人工最小冒烟清单（发起人手动执行，mainnet-readiness.md §6 阶段 1）：")
    print(
        f"       1) {net.explorer_url} 验证合约源码（solc {artifact['compilerVersion']}, shanghai）"
    )
    print("       2) 未授权 charge 拒绝路径（坏签名 eth_call 只读复核 bad_signature）")
    print("       3) 1 笔最小额真金 Charged + I4 对账（balanceOf(PayVault) == totalCredits()）")
    print("       4) 复核主网 USDT 源码 blacklist 语义（mainnet-readiness.md §5-3）")


# ============================== 测试网（部署 + 全量冒烟） ==============================


def run_testnet(w3: Web3, net: Network, args: argparse.Namespace) -> None:
    """测试网流程（现行纪元：计价 token=测试网真 USDT，needs_funds 全量冒烟）。"""
    deployment_path = DEPLOYMENTS_DIR / net.deployment_file
    account = account_from_key(load_private_key("testnet"))
    operator_address = resolve_operator(net, args, account.address)
    assert_nonzero_deploy_params(
        token=net.token_address, operator=operator_address, deployer=account.address
    )
    balance = w3.eth.get_balance(operator_address)
    print(f"[1/7] {net.network_label} chainId={net.chain_id} 连接 OK")
    print(f"[1/7] operator={operator_address}")
    print(f"[1/7] operator 余额 {Web3.from_wei(balance, 'ether')} BOT")
    if balance == 0:
        print("[FATAL] operator 无测试网 BOT 余额（需先领水）", file=sys.stderr)
        sys.exit(1)

    # ---- 真 USDT 预检 --------------------------------------------------------
    usdt = w3.eth.contract(address=Web3.to_checksum_address(net.token), abi=USDT_ABI)
    symbol = str(usdt.functions.symbol().call())
    decimals = int(usdt.functions.decimals().call())
    operator_usdt = int(usdt.functions.balanceOf(operator_address).call())
    print(f"[2/7] USDT {net.token} symbol={symbol} decimals={decimals}")
    print(f"[2/7] operator 持有 {operator_usdt / 10**decimals} USDT")
    if symbol != net.token_symbol or decimals != net.token_decimals:
        print(
            f"[FATAL] USDT 预检不符：期望 {net.token_symbol}/{net.token_decimals} 位",
            file=sys.stderr,
        )
        sys.exit(1)
    if operator_usdt < SMOKE_TOTAL:
        print(f"[FATAL] operator USDT 不足冒烟所需 {SMOKE_TOTAL}", file=sys.stderr)
        sys.exit(1)

    old_epoch = previous_epoch(deployment_path, net.chain_id)
    old_before, old_vault, old_token = snapshot_old_vault(w3, old_epoch)
    if old_epoch:
        print(f"[2/7] 退役纪元金库 {old_epoch['payVault']} 快照: {old_before}")

    # ---- 部署（合约零改动，token=真 USDT） ------------------------------------
    vault, vault_receipt = deploy_contract(
        w3, account, "PayVault", Web3.to_checksum_address(net.token), operator_address
    )
    artifact = load_artifact("PayVault")
    print(f"[3/7] PayVault 部署于 {vault.address} (tx {vault_receipt['tx_hash']})")
    # bytecodeHash 以链上 eth_getCode 为权威（任何人可复算）；solc 产物 runtime 在
    # immutable 槽位是占位零，与链上 code 天然不同——不可直接比对（C-06）
    bytecode_hash = "0x" + Web3.keccak(bytes(w3.eth.get_code(vault.address))).hex()
    token_onchain = Web3.to_checksum_address(vault.functions.token().call())
    if token_onchain != Web3.to_checksum_address(net.token):
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
    forged = sign_authorization(account, auth, net.chain_id, vault.address)
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
        make_charge_call(
            consumer,
            vault,
            value=value,
            nonce=nonce,
            chain_id=net.chain_id,
            provider=provider.address,
        )
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
    expected_sep = domain_separator(net.chain_id, vault.address)
    onchain_sep = bytes(vault.functions.DOMAIN_SEPARATOR().call())
    smoke["domain_separator_matches_vector_domain"] = bool(
        vdomain["name"] == DOMAIN_NAME
        and vdomain["version"] == DOMAIN_VERSION
        and int(vdomain["chainId"]) == net.chain_id
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
        "network": net.network_label,
        "chainId": net.chain_id,
        "payVault": vault.address,
        "token": Web3.to_checksum_address(net.token),
        "tokenSymbol": net.token_symbol,
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
            "domain": {"name": DOMAIN_NAME, "version": DOMAIN_VERSION, "chainId": net.chain_id},
            "structType": AUTHORIZATION_TYPE_STRING,
            "goldenVector": "vectors/eip712_golden.json",
        },
        "abiFiles": {"payVault": "artifacts/PayVault.json", "mockUsdt": "artifacts/MockUSDT.json"},
        "gasPriceWei": GAS_PRICE_WEI,
        "epochs": epochs,
        "smoke": smoke,
        "smokeAllPassed": ok,
    }
    deployment_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(deployment, indent=2, ensure_ascii=False) + "\n"
    deployment_path.write_text(payload, encoding="utf-8")
    print(f"[7/7] 部署事实已写入 {deployment_path}")
    print(f"[7/7] 冒烟总判定: {'PASS' if ok else 'FAIL'}")
    if not ok:
        sys.exit(2)


def main() -> None:
    args = parse_args()
    net = network_with_overrides(
        args.network, rpc_url=args.rpc, chain_id=args.chain_id, token=args.token
    )
    try:
        w3 = connect(net.rpc_url)
        assert_chain_id(w3, net.chain_id)  # 签名前铁律：防 RPC 指错网络（两网通用）
    except Exception as exc:
        print(
            f"[FATAL] 连接 {net.rpc_url} / 断言 chainId={net.chain_id} 失败: {exc}",
            file=sys.stderr,
        )
        if net.rpc_url == NETWORKS["mainnet"].rpc_url:
            print(
                "[HINT] rpc.botchain.ai 在 DNS 污染环境需走系统代理："
                "export HTTPS_PROXY=http://<代理地址>（代码不硬编码代理，部署机可直连则无需配置）",
                file=sys.stderr,
            )
        sys.exit(1)
    if net.name == "mainnet":
        run_mainnet(w3, net, args)
    else:
        run_testnet(w3, net, args)


if __name__ == "__main__":
    main()
