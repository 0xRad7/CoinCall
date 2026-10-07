"""链上提交层（模式移植自 coincall-bot-chain-api app/core/rpc.py + tx.py，只读复用）。

- BOT Chain 为 POA 链：必须注入 ExtraDataToPOAMiddleware，否则 get_block 全系失败；
- gas 恒定 20 gwei（baseFee=0，不做动态费用），legacy(type-0) 交易；
- 域白名单双网络：*.bohr.life（测试网 968）强制直连（trust_env=False 忽略环境代理），
  *.botchain.ai（主网 677）信任系统代理（trust_env=True，DNS 污染环境 export HTTPS_PROXY
  即可，代码不硬编码代理），其余域一律拒绝访问；
- 签名前断言 chainId == 目标网络链 ID（968/677，防 RPC 指错网络）。

同一 send/deploy 助手也服务于 eth-tester 本地测试（无网络）。
"""

from typing import Any, cast
from urllib.parse import urlsplit

from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexStr
from requests import Session
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractFunction
from web3.middleware import ExtraDataToPOAMiddleware
from web3.providers import HTTPProvider
from web3.providers.eth_tester import EthereumTesterProvider as EthereumProvider
from web3.types import TxParams

from payvault.compile import load_artifact

TESTNET_RPC_URL = "https://rpc.bohr.life/"
TESTNET_CHAIN_ID = 968
MAINNET_RPC_URL = "https://rpc.botchain.ai/"
MAINNET_CHAIN_ID = 677
# 链上访问只允许这两个域后缀（其余一律拒绝）；bohr.life 直连，botchain.ai 走系统代理
ALLOWED_DOMAIN_SUFFIXES = ("bohr.life", "botchain.ai")
DIRECT_DOMAIN_SUFFIX = "bohr.life"  # 该域强制直连（忽略环境代理）；botchain.ai 信任 env 代理

GAS_PRICE_WEI = 20 * 10**9  # 恒定 20 gwei
GAS_MARGIN_RATIO = 1.2
MIN_GAS = 21000
RECEIPT_TIMEOUT_S = 60

DEFAULT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def direct_session() -> Session:
    """bohr.life 专用直连会话：trust_env=False 忽略 http(s)_proxy 环境变量 + UA 伪装。

    python-urllib/requests 默认 UA 会被部分 WAF 拒绝（bot-chain-api 实测沉淀）。
    """
    session = Session()
    session.trust_env = False
    session.headers.update({"User-Agent": DEFAULT_UA, "Accept": "application/json"})
    return session


def proxied_session() -> Session:
    """botchain.ai 专用代理会话：trust_env=True 信任系统代理 + 同款 UA 伪装。

    rpc.botchain.ai 在 DNS 污染环境不可直连（mainnet-readiness.md §1）：requests 信任
    HTTPS_PROXY/https_proxy/ALL_PROXY 环境变量即可，代码不硬编码代理；部署机可直连时
    无需任何配置。
    """
    session = Session()
    session.trust_env = True
    session.headers.update({"User-Agent": DEFAULT_UA, "Accept": "application/json"})
    return session


def session_for(rpc_url: str) -> Session:
    """按域选择会话：*.bohr.life 直连，其余白名单域（botchain.ai）信任系统代理。"""
    host = (urlsplit(rpc_url).hostname or "").lower()
    return direct_session() if host.endswith(DIRECT_DOMAIN_SUFFIX) else proxied_session()


def connect(rpc_url: str) -> Web3:
    """连接 BOT Chain（测试网/主网通用）：域白名单 + 按域选择直连或系统代理 + POA 适配。

    白名单外的域直接 ValueError（防 RPC 拼写错误指向陌生链）。
    """
    host = (urlsplit(rpc_url).hostname or "").lower()
    if not any(host.endswith(suffix) for suffix in ALLOWED_DOMAIN_SUFFIXES):
        msg = f"链上访问只允许 {ALLOWED_DOMAIN_SUFFIXES} 域: {rpc_url}"
        raise ValueError(msg)
    provider = HTTPProvider(
        endpoint_uri=rpc_url,
        request_kwargs={"timeout": 15.0},
        session=session_for(rpc_url),
    )
    w3 = Web3(provider)
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def connect_testnet(rpc_url: str = TESTNET_RPC_URL) -> Web3:
    """连接 BOT Chain 测试网（bohr.life 直连 + UA 伪装 + POA 适配）。"""
    return connect(rpc_url)


def connect_local_tester() -> Web3:
    """eth-tester 本地 EVM（单测用；POA 中间件同注，行为与线上一致）。"""
    from eth_tester import EthereumTester  # noqa: PLC0415 - 延迟导入避免依赖热身

    w3 = Web3(EthereumProvider(EthereumTester()))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def assert_chain_id(w3: Web3, expected: int = TESTNET_CHAIN_ID) -> None:
    """签名前断言链 ID（防 RPC 指错网络；主网传 expected=MAINNET_CHAIN_ID）。"""
    actual = w3.eth.chain_id
    if actual != expected:
        msg = f"chainId 不符: 期望 {expected}, 实际 {actual}（拒绝签名）"
        raise RuntimeError(msg)


def build_tx(
    w3: Web3,
    from_address: str,
    to_address: str | None,
    data: bytes,
    gas: int | None = None,
    *,
    value_wei: int = 0,
) -> TxParams:
    """恒定 20 gwei 的 legacy 交易模板（gas = estimate * 1.2；value_wei 可带原生币转账）。"""
    from_addr = Web3.to_checksum_address(from_address)
    call = {"from": from_addr, "to": to_address, "data": data, "value": value_wei}
    estimate = gas or max(
        MIN_GAS, int(w3.eth.estimate_gas(cast("TxParams", call)) * GAS_MARGIN_RATIO)
    )
    return cast(
        TxParams,
        {
            "from": from_addr,
            "to": to_address,
            "value": value_wei,
            "gas": estimate,
            "gasPrice": GAS_PRICE_WEI,
            "nonce": w3.eth.get_transaction_count(from_addr, "pending"),
            "chainId": w3.eth.chain_id,
            "data": data,
        },
    )


def sign_send_wait(
    w3: Web3,
    account: LocalAccount,
    to_address: str | None,
    data: bytes,
    gas: int | None = None,
    *,
    value_wei: int = 0,
) -> dict[str, Any]:
    """签名 → 发送 → 等回执；status=0 视为失败抛错。日志安全：只含地址与哈希。"""
    tx = build_tx(w3, account.address, to_address, data, gas=gas, value_wei=value_wei)
    signed = account.sign_transaction(cast("dict[str, Any]", dict(tx)))
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=RECEIPT_TIMEOUT_S)
    if int(receipt["status"]) != 1:
        msg = f"交易上链但执行失败: {Web3.to_hex(tx_hash)}"
        raise RuntimeError(msg)
    return {
        "tx_hash": Web3.to_hex(tx_hash),
        "block_number": int(receipt["blockNumber"]),
        "gas_used": int(receipt["gasUsed"]),
        "contract_address": receipt.get("contractAddress"),
    }


def deploy_contract(
    w3: Web3,
    account: LocalAccount,
    contract_name: str,
    *constructor_args: Any,  # noqa: ANN401 - 构造参数随合约而变
) -> tuple[Contract, dict[str, Any]]:
    """从入库产物部署合约：PayVault/MockUSDT 的统一入口。"""
    artifact = load_artifact(contract_name)
    factory = w3.eth.contract(abi=artifact["abi"], bytecode=bytecode_str(artifact["bytecode"]))
    data = factory.constructor(*constructor_args).data_in_transaction
    receipt = sign_send_wait(w3, account, None, data)
    address = receipt["contract_address"]
    if not address:
        msg = f"{contract_name} 部署回执缺合约地址: {receipt['tx_hash']}"
        raise RuntimeError(msg)
    contract = w3.eth.contract(address=Web3.to_checksum_address(address), abi=artifact["abi"])
    return contract, receipt


def calldata(func: ContractFunction, from_address: str) -> bytes:
    """合约函数调用 → calldata 字节（build_transaction 公开路径，不触发 RPC）。"""
    tx = func.build_transaction(
        cast(
            "TxParams",
            {
                "from": Web3.to_checksum_address(from_address),
                "gas": MIN_GAS,
                "gasPrice": GAS_PRICE_WEI,
                "nonce": 0,
                "chainId": 1,
                "value": 0,
            },
        )
    )
    data = tx["data"]
    return data if isinstance(data, bytes) else Web3.to_bytes(hexstr=cast("HexStr", data))


def call_contract(
    w3: Web3, account: LocalAccount, func: ContractFunction, gas: int | None = None
) -> dict[str, Any]:
    """签名提交一次合约调用（20 gwei legacy），返回回执摘要。"""
    return sign_send_wait(w3, account, func.address, calldata(func, account.address), gas=gas)


def bytecode_str(bytecode: str) -> str:
    """校验 0x 前缀 bytecode 字符串。"""
    if not bytecode.startswith("0x"):
        msg = "bytecode 缺 0x 前缀"
        raise ValueError(msg)
    return bytecode


def account_from_key(private_key: str) -> LocalAccount:
    """私钥 → 账户；调用方保证不打印私钥（铁律：日志只允许出现地址）。"""
    return Account.from_key(private_key)


__all__ = [
    "MAINNET_CHAIN_ID",
    "MAINNET_RPC_URL",
    "TESTNET_CHAIN_ID",
    "TESTNET_RPC_URL",
    "account_from_key",
    "assert_chain_id",
    "build_tx",
    "call_contract",
    "calldata",
    "connect",
    "connect_local_tester",
    "connect_testnet",
    "deploy_contract",
    "proxied_session",
    "sign_send_wait",
]
