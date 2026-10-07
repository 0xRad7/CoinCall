"""链常量单一来源（铁律 A3）。

全部合约地址/端点迁自 bot_chain_scripts/config.py（2026-09-29 实测尽调），
禁止在本文件之外硬编码任何地址或 RPC URL。
主网（botchain.ai 域）默认禁用：见 core/config.py 双重锁。
"""

from enum import StrEnum

from pydantic import BaseModel, field_validator
from web3 import Web3


class Network(StrEnum):
    TESTNET = "testnet"
    MAINNET = "mainnet"


class ChainContracts(BaseModel):
    """两网已知合约地址（checksum 规范化）。"""

    # 4337 / 账户抽象
    entry_point: str  # EntryPoint v0.7，以太坊标准单例，两网同址
    simple_account_factory: str
    kernel_v033: str  # Agent Wallet 账户实现（备用）
    # ERC-8004 Agent Identity 三合约
    identity_registry: str  # ERC-721 "AGENT"
    reputation_registry: str
    validation_registry: str
    # BDEX（Uniswap V2/V3 fork）
    v2_factory: str
    v2_router: str
    v3_factory: str
    v3_swap_router: str
    multicall3: str
    # 代币
    wbot: str  # 18 decimals，两网同址
    usdt: str  # 6 decimals

    @field_validator("*", mode="before")
    @classmethod
    def _to_checksum(cls, v: object) -> object:
        if isinstance(v, str):
            if not Web3.is_address(v):
                msg = f"非法合约地址: {v}"
                raise ValueError(msg)
            return Web3.to_checksum_address(v)
        return v


class ChainSpec(BaseModel):
    """单网络端点 + 合约全集。"""

    network: Network
    chain_id: int
    rpc_url: str
    bundler_url: str
    explorer_url: str
    explorer_api: str
    faucet_url: str
    faucet_api_url: str  # api-faucet 直连端点（探测结论，Turnstile 强制）
    contracts: ChainContracts


SHARED_CONTRACTS = {
    "entry_point": "0x0000000071727De22E5E9d8BAf0edAc6f37da032",
    "simple_account_factory": "0xBC88d6012b3bf8426C2851d3798cEB5257658332",
    "kernel_v033": "0xDE15Ab00C52FF37eCe01C384eB42C802721db823",
    "v3_factory": "0x1C51c173323ec11BB4e3C4fD2314c225Dc4b5419",  # 两网同址
    "v3_swap_router": "0x07032d47A1b9f8460cBeE9dC17c1d3E438693929",  # 两网同址
    "wbot": "0xD5452816194a3784dBa983426cCe7c122F4abd30",  # 两网同址
}

CHAINS: dict[str, ChainSpec] = {
    "testnet": ChainSpec(
        network=Network.TESTNET,
        chain_id=968,
        rpc_url="https://rpc.bohr.life/",
        bundler_url="https://bundler.bohr.life/rpc/",
        explorer_url="https://scan.bohr.life",
        explorer_api="https://scan.bohr.life/api/v2",
        faucet_url="https://faucet.bohr.life/basic",
        faucet_api_url="https://api-faucet.bohr.life/botchain/api/v1/faucet",
        contracts=ChainContracts(
            **SHARED_CONTRACTS,
            identity_registry="0xec8fFbC3c9A34AdDCbB3A14F91db2bd26A8b99c0",
            reputation_registry="0xD4084c9397adca7fD0c24617681a2eA4C286402A",
            validation_registry="0x0ff64E68C0e5fB2c57E6B3e1D7F3c65FBD0eEA33",
            v2_factory="0x65b8e98ceA190d8c28B3e4716402027f634d15a3",
            v2_router="0xD6425a02f0845B8D99e349C34D2E7A576E177345",
            multicall3="0x8247F49Ea29fd9251466006812f5D0aF2050AC44",
            usdt="0x75edC9335175Fc0552D51D48439F229c10420fe3",
        ),
    ),
    "mainnet": ChainSpec(
        network=Network.MAINNET,
        chain_id=677,
        rpc_url="https://rpc.botchain.ai/",  # DNS 污染，仅代理可达（铁律 A7）
        bundler_url="https://bundler.botchain.ai/rpc/",
        explorer_url="https://scan.botchain.ai",
        explorer_api="https://scan.botchain.ai/api/v2",
        faucet_url="https://faucet.botchain.ai/basic",
        faucet_api_url="https://api-faucet.botchain.ai/botchain/api/v1/faucet",
        contracts=ChainContracts(
            **SHARED_CONTRACTS,
            identity_registry="0xB43Edfb9C7609cF645e932B2fF20f26F0d4488dE",
            reputation_registry="0xB3f7061354193a424856Cc63028cE3556262dAc9",
            validation_registry="0x85Bf081D1492393C3Fb2742b379b6ADbC7dbC79b",
            v2_factory="0x117115f3B72C8d1989178089A67D0C26f8EE0AA3",
            v2_router="0x1414eD29FdFD322c3c0a830330ed982E2D629e76",
            multicall3="0x47FA21f684bBAD707A53a0f9BE59F1422F46C265",
            usdt="0xaBabc7Ddc03e501d190C676BF3d92ef0e6e87a3C",
        ),
    ),
}


def get_chain(network: str) -> ChainSpec:
    """按名称取网络规格；未知网络抛 KeyError。"""
    return CHAINS[network]
