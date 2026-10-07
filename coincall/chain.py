"""链连接：只读 ERC-20 查询 + 本地签名 raw tx 的发送（A1/A4）。

- 网络参数见 coincall/networks.py（缺省测试网 968 / rpc.bohr.life；
  COINCALL_NETWORK=mainnet 切 677 / rpc.botchain.ai，字段级 env 可精确覆写）；
  POA 链 gas 恒 20 gwei；
- web3 的 requests Session 显式 trust_env=False（继承 gateway C-07：系统代理不得劫持）；
  DNS 污染等场景经 COINCALL_RPC_PROXY 显式逐用途开启（只作用于链 RPC，不影响其他客户端）；
- 签名永远不在本模块发生：只构建 tx 字典（chainId==期望链 ID 断言先于任何签名）
  与发送已签名 raw bytes。
"""

import os
from typing import Any, Protocol

import requests
from eth_typing import HexStr
from eth_utils import keccak, to_checksum_address
from web3 import Web3

from coincall.errors import WalletError
from coincall.networks import TESTNET, resolve_network

RPC_URL = TESTNET.rpc_url  # 向后兼容快照（import 时值）；运行期缺省走 resolve_network()
#: 链 RPC 专用显式代理（C-07：系统代理不劫持；DNS 污染机器上按需开启，如 http://127.0.0.1:7890）
ENV_RPC_PROXY = "COINCALL_RPC_PROXY"
GAS_PRICE_GWEI = 20
TX_TIMEOUT_S = 30
RECEIPT_TIMEOUT_S = 90

# 计价 token：测试网真 USDT（epoch2；= PayVault 0xa6E8… 的 token()，链上核验一致，6 位精度）。
# 旧 MockUSDT 0x4F8f… 仅历史（epoch1 退役）；真 USDT 的 mint 受 MINTER_ROLE 门禁，消费者不能自铸。
# 主网 USDT 0xaBabc7…87a3C 经 COINCALL_NETWORK=mainnet 或 COINCALL_TOKEN_ADDRESS 切换。
TOKEN_ADDRESS = TESTNET.token_address  # 公开合约地址，非凭据
TOKEN_DECIMALS = 6

APPROVE_GAS = 100_000  # approve(address,uint256) 实测 ≈46k，留余量（POA 链 gas 价格恒定）
MINT_GAS = 100_000

_SELECTORS = {
    "approve(address,uint256)": keccak(b"approve(address,uint256)")[:4],
    "balanceOf(address)": keccak(b"balanceOf(address)")[:4],
    "allowance(address,address)": keccak(b"allowance(address,address)")[:4],
    "mint(address,uint256)": keccak(b"mint(address,uint256)")[:4],
}


def _pad_uint(n: int) -> bytes:
    return n.to_bytes(32, "big")


def _pad_address(addr: str) -> bytes:
    return int(addr, 16).to_bytes(32, "big")


def erc20_approve_data(spender: str, amount_raw: int) -> bytes:
    """approve(spender, amount_raw) calldata（USDT 无 ABI 依赖的手工编码）。"""
    return _SELECTORS["approve(address,uint256)"] + _pad_address(spender) + _pad_uint(amount_raw)


def erc20_mint_data(to: str, amount_raw: int) -> bytes:
    """mint(to, amount_raw) calldata（仅限持有 MINTER_ROLE 的账户；普通消费者钱包会 revert）。"""
    return _SELECTORS["mint(address,uint256)"] + _pad_address(to) + _pad_uint(amount_raw)


def erc20_balance_data(wallet: str) -> bytes:
    return _SELECTORS["balanceOf(address)"] + _pad_address(wallet)


def erc20_allowance_data(owner: str, spender: str) -> bytes:
    return _SELECTORS["allowance(address,address)"] + _pad_address(owner) + _pad_address(spender)


class ChainGateway(Protocol):
    """钱包所需的链网关最小面（ChainConnection 与测试 FakeChain 共同实现）。"""

    def build_tx(self, from_addr: str, to: str, data: bytes, gas: int) -> dict[str, Any]: ...

    def send_raw(self, raw: bytes) -> str: ...

    def wait_receipt(self, tx_hash: str, timeout: int = RECEIPT_TIMEOUT_S) -> dict[str, Any]: ...

    def erc20_balance(self, wallet: str, token: str) -> int: ...

    def erc20_allowance(self, owner: str, spender: str, token: str) -> int: ...


class ChainConnection:
    """RPC 直连（只读查询 + raw tx 广播；POA 恒 20 gwei；缺省网络经 env 解析）。"""

    def __init__(
        self,
        rpc_url: str | None = None,
        expected_chain_id: int | None = None,
    ) -> None:
        network = resolve_network()  # 调用时快照：env 后设置同样生效
        self.expected_chain_id = (
            expected_chain_id if expected_chain_id is not None else network.chain_id
        )
        session = requests.Session()
        session.trust_env = False  # C-07：系统代理不得劫持链 RPC
        rpc_proxy = os.environ.get(ENV_RPC_PROXY, "").strip()
        if rpc_proxy:
            session.proxies = {"http": rpc_proxy, "https": rpc_proxy}
        url = rpc_url or network.rpc_url
        self._w3 = Web3(
            Web3.HTTPProvider(url, session=session, request_kwargs={"timeout": TX_TIMEOUT_S})
        )

    @property
    def chain_id(self) -> int:
        return int(self._w3.eth.chain_id)

    def build_tx(self, from_addr: str, to: str, data: bytes, gas: int) -> dict[str, Any]:
        """组装未签名 tx（A4：链上 chainId==期望链 ID 断言先于任何签名，防错链）。"""
        onchain_id = int(self._w3.eth.chain_id)
        if onchain_id != self.expected_chain_id:
            raise WalletError(
                f"RPC 链 ID {onchain_id} != {self.expected_chain_id}，拒绝构建交易（防错链）"
            )
        return {
            "chainId": onchain_id,
            "from": from_addr,
            "to": to,
            "value": 0,
            "gas": gas,
            "gasPrice": Web3.to_wei(GAS_PRICE_GWEI, "gwei"),
            "nonce": self._w3.eth.get_transaction_count(to_checksum_address(from_addr), "pending"),
            "data": data,
        }

    def send_raw(self, raw: bytes) -> str:
        """广播本地已签名 raw tx（网络只见签名产物，不见私钥）。"""
        return self._w3.eth.send_raw_transaction(raw).hex()

    def wait_receipt(self, tx_hash: str, timeout: int = RECEIPT_TIMEOUT_S) -> dict[str, Any]:
        receipt = self._w3.eth.wait_for_transaction_receipt(
            HexStr(tx_hash), timeout=timeout, poll_latency=2
        )
        return {
            "transactionHash": receipt["transactionHash"].hex(),
            "status": int(receipt["status"]),
            "gasUsed": int(receipt["gasUsed"]),
        }

    def erc20_balance(self, wallet: str, token: str) -> int:
        raw = self._w3.eth.call({"to": token, "data": erc20_balance_data(wallet)})
        return int.from_bytes(raw, "big")

    def erc20_allowance(self, owner: str, spender: str, token: str) -> int:
        raw = self._w3.eth.call({"to": token, "data": erc20_allowance_data(owner, spender)})
        return int.from_bytes(raw, "big")
