"""ChainAdapter v1：BotChainAdapter——web3 只读 eth_call 查 USDT balanceOf/allowance。

链上约束检查（02 §5b）：rpc.bohr.life，结果 30s 短缓存（09 P0-4）；
web3 实例装配 POA 中间件（bot-chain-api C-04 教训：本链 extraData 277B）。
单测经 _raw_call 注入 mock，零网络；live 冒烟见 tests/test_chain.py::test_live_balanceof_once。
"""

import time
from typing import Any

from web3 import AsyncHTTPProvider, AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware
from web3.types import HexStr

_SELECTOR_BALANCEOF = "0x70a08231"  # balanceOf(address)
_SELECTOR_ALLOWANCE = "0xdd62ed3e"  # allowance(address,address)


class BotChainAdapter:
    """eth_call 只读适配器（V1；经 coincall-bot-chain-api 的路径属 W4 接线）。"""

    def __init__(self, rpc_url: str, cache_ttl: float = 30.0) -> None:
        self._rpc_url = rpc_url
        self._cache_ttl = cache_ttl
        self._w3: AsyncWeb3 | None = None
        self._cache: dict[tuple[str, ...], tuple[float, int]] = {}

    def _ensure_w3(self) -> AsyncWeb3:
        if self._w3 is None:
            w3 = AsyncWeb3(AsyncHTTPProvider(self._rpc_url))
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)  # C-04：POA 装配
            self._w3 = w3
        return self._w3

    async def _raw_call(self, to: str, data: str) -> str:
        """单点 eth_call（测试 override 此处即可零网络）。"""
        w3 = self._ensure_w3()
        result: Any = await w3.eth.call({"to": HexStr(to), "data": HexStr(data)})
        if isinstance(result, bytes):  # async 路径可能直接返回原始 bytes
            return "0x" + result.hex()
        return str(result)

    async def _cached_call(
        self, cache_key: tuple[str, ...], to: str, data: str, *, force: bool = False
    ) -> int:
        now = time.monotonic()
        if not force:
            hit = self._cache.get(cache_key)
            if hit is not None and now - hit[0] < self._cache_ttl:
                return hit[1]
        raw = await self._raw_call(to, data)
        value = int(raw, 16) if raw != "0x" else 0
        self._cache[cache_key] = (now, value)
        return value

    async def erc20_balance(self, wallet: str, token: str, *, force: bool = False) -> int:
        data = _SELECTOR_BALANCEOF + wallet[2:].lower().rjust(64, "0")
        return await self._cached_call(
            ("balance", wallet.lower(), token.lower()), token, data, force=force
        )

    async def erc20_allowance(
        self, owner: str, spender: str, token: str, *, force: bool = False
    ) -> int:
        data = (
            _SELECTOR_ALLOWANCE
            + owner[2:].lower().rjust(64, "0")
            + spender[2:].lower().rjust(64, "0")
        )
        return await self._cached_call(
            ("allowance", owner.lower(), spender.lower(), token.lower()), token, data, force=force
        )
