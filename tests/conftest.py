"""共享 fixtures：链配置(968)、web3 实例、Blockscout 客户端、资金账户。

约定（02 篇测试计划）：
- live 用例打真实测试网 968，只读；
- needs_funds 用例读 BOT_CHAIN_TEST_PRIVATE_KEY，未设置时显式 skip（附领水指引），skip 不算红。
"""

import pytest

from app.core.chains import ChainSpec
from app.core.config import Settings, get_settings
from app.core.rpc import make_http_client, make_web3, resolve_proxy

FAUCET_URL = "https://faucet.bohr.life/basic"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="session")
def chain(settings: Settings) -> ChainSpec:
    return settings.chain_spec


@pytest.fixture(scope="session")
def w3(chain: ChainSpec, settings: Settings):
    """真实测试网 968 的 web3 实例（UA 伪装 + 重试由 core/rpc.py 统一装配）。"""
    return make_web3(chain, proxy=settings.proxy)


@pytest.fixture(scope="session")
def explorer(chain: ChainSpec, settings: Settings):
    """Blockscout /api/v2 客户端（免 key）。"""
    client = make_http_client(
        chain.explorer_api,
        proxy=resolve_proxy(chain.explorer_api, settings.proxy),
    )
    yield client
    client.close()


@pytest.fixture(scope="session")
def funder_key(settings: Settings) -> str:
    """needs_funds 出资私钥；未配置则显式 skip（领水指引）。"""
    key = settings.funded_key
    if not key:
        msg = (
            f"needs_funds: 未设置 BOT_CHAIN_TEST_PRIVATE_KEY. "
            f"请到 {FAUCET_URL} 领水后写入 .env 再跑"
        )
        pytest.skip(msg)
    return key
