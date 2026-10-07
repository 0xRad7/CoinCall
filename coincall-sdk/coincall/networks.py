"""网络表与 env 解析（主网参数化）：缺省测试网 968，COINCALL_NETWORK=mainnet 切主网 677。

向后兼容：不设任何 COINCALL_NETWORK* env 时，解析结果与历史硬编码逐字段一致
（chainId 968 / rpc.bohr.life / USDT 0x75ed…9335 / PayVault 0xa6E8…）——现有调用方与单测零感知。
字段级 env（COINCALL_CHAIN_ID / COINCALL_RPC_URL / COINCALL_TOKEN_ADDRESS /
COINCALL_PAY_VAULT）作精确覆写（排障/私有端点），优先于网络表，但只覆写被设置的字段。
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from coincall.errors import CoinCallError

ENV_NETWORK = "COINCALL_NETWORK"  # testnet | mainnet（缺省 testnet）
ENV_CHAIN_ID = "COINCALL_CHAIN_ID"  # 精确覆写链 ID
ENV_RPC_URL = "COINCALL_RPC_URL"  # 精确覆写 RPC 端点
ENV_TOKEN_ADDRESS = "COINCALL_TOKEN_ADDRESS"  # noqa: S105 —— 公开 env 变量名，非凭据
ENV_PAY_VAULT = "COINCALL_PAY_VAULT"


@dataclass(frozen=True)
class Network:
    """一条计价链的最小参数四元组：链 ID / RPC / 计价 token（6 位精度）/ PayVault 金库。"""

    name: str
    chain_id: int
    rpc_url: str
    token_address: str
    pay_vault: str


TESTNET = Network(
    name="testnet",
    chain_id=968,  # Bohr 测试网（历史缺省，黄金向量 T17 锁定的 EIP-712 域）
    rpc_url="https://rpc.bohr.life/",
    token_address="0x75edC9335175Fc0552D51D48439F229c10420fe3",  # noqa: S106 —— 公开合约地址，非凭据
    # epoch2 金库（=signing.PAY_VAULT_ADDRESS，兼容锚）
    pay_vault="0xa6E82Fd6648F9Ea8f695c37Edf89f2E5FDb89ff0",
)
MAINNET = Network(
    name="mainnet",
    chain_id=677,  # BOT Chain 主网
    rpc_url="https://rpc.botchain.ai/",
    token_address="0xaBabc7Ddc03e501d190C676BF3d92ef0e6e87a3C",  # noqa: S106 —— 公开合约地址，非凭据
    # 事实源 contracts/deployments/mainnet-677.json
    pay_vault="0x39f9c91992BAfd1528ef87aFDf8B17b7b6cc1818",
)

NETWORKS: dict[str, Network] = {"testnet": TESTNET, "mainnet": MAINNET}
DEFAULT_NETWORK = "testnet"


def resolve_network(env: Mapping[str, str] | None = None) -> Network:
    """按 env 解析当前网络（缺省 testnet；字段级覆写优先；非法值给人话错误）。

    env=None 读 os.environ（调用时快照，非 import 时）；显式传 Mapping 供测试注入。
    """
    source: Mapping[str, str] = os.environ if env is None else env
    raw_name = source.get(ENV_NETWORK, "").strip().lower()
    try:
        base = NETWORKS[raw_name or DEFAULT_NETWORK]
    except KeyError:
        known = "|".join(NETWORKS)
        raise CoinCallError(
            f"{ENV_NETWORK}={source.get(ENV_NETWORK)!r} 不合法（可选 {known}，缺省 testnet）"
        ) from None

    chain_raw = source.get(ENV_CHAIN_ID, "").strip()
    if chain_raw:
        try:
            chain_id = int(chain_raw)
        except ValueError:
            raise CoinCallError(f"{ENV_CHAIN_ID}={chain_raw!r} 不是十进制整数") from None
    else:
        chain_id = base.chain_id
    rpc_url = source.get(ENV_RPC_URL, "").strip() or base.rpc_url
    token_address = source.get(ENV_TOKEN_ADDRESS, "").strip() or base.token_address
    pay_vault = source.get(ENV_PAY_VAULT, "").strip() or base.pay_vault
    return Network(
        name=base.name,
        chain_id=chain_id,
        rpc_url=rpc_url,
        token_address=token_address,
        pay_vault=pay_vault,
    )
