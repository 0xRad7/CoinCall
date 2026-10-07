"""部署网络表（单一事实源）：BOT Chain 测试网 968 / 主网 677。

事实来源对齐：
- RPC/chainId：bot_chain_scripts/config.py（BOT_MAINNET_RPC/BOT_TESTNET_RPC）；
- 两网 USDT：同上 BDEX 表（mainnet 0xaBabc7…87a3C / testnet 0x75edC9…0fe3，均实测 checksummed）；
- 主网 operator 默认值：coincall-docs/research/mainnet-readiness.md §2.1（keystore 托管地址）。

部署产物按网络分文件（``deployments/<name>-<chainId>.json``）：testnet-968.json 与
mainnet-677.json 互不覆盖。主网地址均为官方既有合约（USDT 无需自部署，MockUSDT 绝不上主网）。
"""

from dataclasses import dataclass, replace
from typing import Any

from web3 import Web3

from payvault.chain import (
    MAINNET_CHAIN_ID,
    MAINNET_RPC_URL,
    TESTNET_CHAIN_ID,
    TESTNET_RPC_URL,
)


@dataclass(frozen=True)
class Network:
    """一个部署目标网络的全部事实（不可变；CLI 覆写经 dataclasses.replace 派生新实例）。"""

    name: str  # CLI --network 取值："testnet" | "mainnet"
    network_label: str  # deployments JSON 的 network 字段（沿用现行 "botchain-testnet" 口径）
    rpc_url: str
    chain_id: int
    token: str  # 计价 USDT（checksum 地址，PayVault 构造器第一参）
    token_symbol: str
    token_decimals: int
    explorer_url: str  # 源码验证浏览器（人工核验用）

    @property
    def deployment_file(self) -> str:
        """deployments/<name>-<chainId>.json：网络（含链 ID）分文件，绝不互覆。"""
        return f"{self.name}-{self.chain_id}.json"


NETWORKS: dict[str, Network] = {
    "testnet": Network(
        name="testnet",
        network_label="botchain-testnet",
        rpc_url=TESTNET_RPC_URL,
        chain_id=TESTNET_CHAIN_ID,
        token="0x75edC9335175Fc0552D51D48439F229c10420fe3",  # noqa: S106 - 合约地址非密钥（ruff 误报）
        token_symbol="USDT",  # noqa: S106 - 代币符号非密钥（ruff 误报）
        token_decimals=6,
        explorer_url="https://scan.bohr.life",
    ),
    "mainnet": Network(
        name="mainnet",
        network_label="botchain-mainnet",
        rpc_url=MAINNET_RPC_URL,
        chain_id=MAINNET_CHAIN_ID,
        token="0xaBabc7Ddc03e501d190C676BF3d92ef0e6e87a3C",  # noqa: S106 - 合约地址非密钥（ruff 误报）
        token_symbol="USDT",  # noqa: S106 - 代币符号非密钥（ruff 误报）
        token_decimals=6,
        explorer_url="https://scan.botchain.ai",
    ),
}

# 主网默认 operator：keystore 托管地址（mainnet-readiness.md §2.1——bot-chain-api keystore
# 只能新建不能 import，operator 必须是 keystore 账户；--operator 可覆写）
MAINNET_DEFAULT_OPERATOR = "0xb1ea3EA94e2Fd7Cb9244cD460dA863FC4b61033A"


def get_network(name: str) -> Network:
    """按名取网络；未知名字直接抛 KeyError（CLI 层用 choices 先行拦截）。"""
    if name not in NETWORKS:
        msg = f"未知网络: {name}（可选: {sorted(NETWORKS)}）"
        raise KeyError(msg)
    return NETWORKS[name]


def network_with_overrides(
    name: str,
    *,
    rpc_url: str | None = None,
    chain_id: int | None = None,
    token: str | None = None,
) -> Network:
    """网络表条目 + CLI 覆写（--rpc/--chain-id/--token）→ 派生 Network。

    覆写后的 RPC 仍受 chain.connect 的域白名单约束（bohr.life/botchain.ai 之外拒绝）；
    token 归一为 checksum 地址。None 的覆写项保持网络表原值。
    """
    net = get_network(name)
    changes: dict[str, Any] = {}
    if rpc_url:
        changes["rpc_url"] = rpc_url
    if chain_id is not None:
        changes["chain_id"] = chain_id
    if token:
        changes["token"] = Web3.to_checksum_address(token)
    return replace(net, **changes) if changes else net


__all__ = [
    "MAINNET_DEFAULT_OPERATOR",
    "NETWORKS",
    "Network",
    "get_network",
    "network_with_overrides",
]
