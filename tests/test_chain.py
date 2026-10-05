"""BotChainAdapter：eth_call calldata/解码/短缓存（_raw_call 注入 mock，零网络）+ live 冒烟。"""

import pytest
from app.core.chain import BotChainAdapter

pytestmark = pytest.mark.unit

TOKEN = "0x75edC9335175Fc0552D51D48439F229c10420fe3"
VAULT = "0x000000000000000000000000000000000000dEaD"
WALLET = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"


class ScriptedChain(BotChainAdapter):
    """override _raw_call：记录 calldata，返回编码 uint256。"""

    def __init__(self, value: int) -> None:
        super().__init__(rpc_url="http://mock")
        self.value = value
        self.raw_calls: list[tuple[str, str]] = []

    async def _raw_call(self, to: str, data: str) -> str:
        self.raw_calls.append((to, data))
        return "0x" + self.value.to_bytes(32, "big").hex()


async def test_balanceof_calldata_and_decode() -> None:
    chain = ScriptedChain(1_234_567)
    balance = await chain.erc20_balance(WALLET, TOKEN)
    assert balance == 1_234_567
    to, data = chain.raw_calls[0]
    assert to.lower() == TOKEN.lower()
    assert data.startswith("0x70a08231")  # balanceOf(address) selector
    assert data[10:].lower() == WALLET[2:].lower().rjust(64, "0")


async def test_allowance_calldata_and_decode() -> None:
    chain = ScriptedChain(9_999)
    allowance = await chain.erc20_allowance(WALLET, VAULT, TOKEN)
    assert allowance == 9_999
    _, data = chain.raw_calls[0]
    assert data.startswith("0xdd62ed3e")  # allowance(address,address) selector
    assert WALLET[2:].lower().rjust(64, "0") in data.lower()
    assert VAULT[2:].lower().rjust(64, "0") in data.lower()


async def test_short_cache_dedupes_calls() -> None:
    chain = ScriptedChain(7)
    await chain.erc20_balance(WALLET, TOKEN)
    await chain.erc20_balance(WALLET, TOKEN)
    assert len(chain.raw_calls) == 1  # 30s 短缓存
    await chain.erc20_allowance(WALLET, VAULT, TOKEN)
    assert len(chain.raw_calls) == 2


async def test_cache_key_separates_wallets() -> None:
    chain = ScriptedChain(7)
    await chain.erc20_balance(WALLET, TOKEN)
    await chain.erc20_balance(VAULT, TOKEN)
    assert len(chain.raw_calls) == 2


@pytest.mark.live
async def test_live_balanceof_once() -> None:
    """唯一 live 冒烟：对 rpc.bohr.life 一次 eth_call（USDT.balanceOf）。"""
    chain = BotChainAdapter(rpc_url="https://rpc.bohr.life/")
    balance = await chain.erc20_balance(VAULT, TOKEN)
    assert balance >= 0  # 只读冒烟，值不做断言口径
