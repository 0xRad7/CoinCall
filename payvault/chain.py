"""链上提交层（模式移植自 coincall-bot-chain-api app/core/rpc.py + tx.py，只读复用）。

- BOT Chain 为 POA 链：必须注入 ExtraDataToPOAMiddleware，否则 get_block 全系失败；
- gas 恒定 20 gwei（baseFee=0，不做动态费用），legacy(type-0) 交易；
- *.bohr.life 域强制直连（trust_env=False 忽略环境代理），其他域拒绝访问；
- 签名前断言 chainId == 968（防 RPC 指错网络）。

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
ALLOWED_DOMAIN_SUFFIX = "bohr.life"  # 链上访问只允许该域（其余一律拒绝）

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


def connect_testnet(rpc_url: str = TESTNET_RPC_URL) -> Web3:
    """连接 BOT Chain 测试网：域白名单 + UA 伪装 + 忽略环境代理（bohr.life 直连）+ POA 适配。"""
    host = (urlsplit(rpc_url).hostname or "").lower()
    if not host.endswith(ALLOWED_DOMAIN_SUFFIX):
        msg = f"链上访问只允许 *.{ALLOWED_DOMAIN_SUFFIX} 域: {rpc_url}"
        raise ValueError(msg)
    provider = HTTPProvider(
        endpoint_uri=rpc_url,
        request_kwargs={"timeout": 15.0},
        session=direct_session(),
    )
    w3 = Web3(provider)
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def connect_local_tester() -> Web3:
    """eth-tester 本地 EVM（单测用；POA 中间件同注，行为与线上一致）。"""
    from eth_tester import EthereumTester  # noqa: PLC0415 - 延迟导入避免依赖热身

    w3 = Web3(EthereumProvider(EthereumTester()))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def assert_chain_id(w3: Web3, expected: int = TESTNET_CHAIN_ID) -> None:
    """签名前断言链 ID（防 RPC 指错网络）。"""
    actual = w3.eth.chain_id
    if actual != expected:
        msg = f"chainId 不符: 期望 {expected}, 实际 {actual}（拒绝签名）"
        raise RuntimeError(msg)


def build_tx(
    w3: Web3, from_address: str, to_address: str | None, data: bytes, gas: int | None = None
) -> TxParams:
    """恒定 20 gwei 的 legacy 交易模板（gas = estimate * 1.2）。"""
    from_addr = Web3.to_checksum_address(from_address)
    call = {"from": from_addr, "to": to_address, "data": data}
    estimate = gas or max(
        MIN_GAS, int(w3.eth.estimate_gas(cast("TxParams", call)) * GAS_MARGIN_RATIO)
    )
    return cast(
        TxParams,
        {
            "from": from_addr,
            "to": to_address,
            "value": 0,
            "gas": estimate,
            "gasPrice": GAS_PRICE_WEI,
            "nonce": w3.eth.get_transaction_count(from_addr, "pending"),
            "chainId": w3.eth.chain_id,
            "data": data,
        },
    )


def sign_send_wait(
    w3: Web3, account: LocalAccount, to_address: str | None, data: bytes, gas: int | None = None
) -> dict[str, Any]:
    """签名 → 发送 → 等回执；status=0 视为失败抛错。日志安全：只含地址与哈希。"""
    tx = build_tx(w3, account.address, to_address, data, gas=gas)
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
    "TESTNET_CHAIN_ID",
    "TESTNET_RPC_URL",
    "account_from_key",
    "assert_chain_id",
    "build_tx",
    "call_contract",
    "calldata",
    "connect_local_tester",
    "connect_testnet",
    "deploy_contract",
    "sign_send_wait",
]
