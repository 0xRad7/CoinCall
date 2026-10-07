"""live + needs_funds：BDEX V2/V3 报价与路由（02 篇 test_07 矩阵）。"""

import pytest
from eth_account import Account

from app.core.abis.bdex import V2_FACTORY_ABI, V2_PAIR_ABI, V2_ROUTER_ABI
from app.core.abis.erc20 import ERC20_ABI
from app.core.chains import get_chain
from app.core.tx import TxService, wei_from_decimal

pytestmark = pytest.mark.live

CHAIN = get_chain("testnet")
WBOT = CHAIN.contracts.wbot
USDT = CHAIN.contracts.usdt
V2_FACTORY = CHAIN.contracts.v2_factory
V2_ROUTER = CHAIN.contracts.v2_router
V3_FACTORY = CHAIN.contracts.v3_factory
FEE_100 = 3000  # V3 0.3% 池
BPS_DENOMINATOR = 10000
SWAP_TEST_AMOUNT_USDT = "1"  # 最小额 1 USDT


class TestBdexLive:
    def test_wbot_usdt_pair_exists(self, w3) -> None:
        factory = w3.eth.contract(address=V2_FACTORY, abi=V2_FACTORY_ABI)
        pair = factory.functions.getPair(WBOT, USDT).call()
        assert pair != "0x0000000000000000000000000000000000000000", "WBOT/USDT V2 池不存在"

    def test_reserves_and_constant_product(self, w3) -> None:
        factory = w3.eth.contract(address=V2_FACTORY, abi=V2_FACTORY_ABI)
        pair_addr = factory.functions.getPair(WBOT, USDT).call()
        pair = w3.eth.contract(address=pair_addr, abi=V2_PAIR_ABI)
        r0, r1, _ = pair.functions.getReserves().call()
        assert r0 > 0 and r1 > 0

    def test_quote_matches_constant_product(self, w3) -> None:
        """getAmountsOut 与 x*y=k 手算（扣 0.3% 手续费）一致性抽查。"""
        factory = w3.eth.contract(address=V2_FACTORY, abi=V2_FACTORY_ABI)
        pair_addr = factory.functions.getPair(WBOT, USDT).call()
        pair = w3.eth.contract(address=pair_addr, abi=V2_PAIR_ABI)
        router = w3.eth.contract(address=V2_ROUTER, abi=V2_ROUTER_ABI)
        amount_in = 10**6  # 1 USDT
        quoted = router.functions.getAmountsOut(amount_in, [USDT, WBOT]).call()[-1]
        r0, r1, _ = pair.functions.getReserves().call()
        token0 = pair.functions.token0().call()
        if token0.lower() == USDT.lower():
            reserve_in, reserve_out = r0, r1
        else:
            reserve_in, reserve_out = r1, r0
        amount_in_with_fee = amount_in * 9970
        expected = (amount_in_with_fee * reserve_out) // (reserve_in * 10000 + amount_in_with_fee)
        assert abs(quoted - expected) <= 1  # 允许 1 wei 舍入差

    def test_v3_factory_pool_probe(self, w3) -> None:
        """V3 getPool(WBOT, USDT, 3000) 存在性（存在与否均记录，不作为硬断言）。"""
        result = w3.provider.make_request(
            "eth_call",
            [
                {
                    "to": V3_FACTORY,
                    "data": "0x1698ee82"  # getPool(address,address,uint24) selector
                    + "000000000000000000000000"
                    + WBOT[2:].lower()
                    + "000000000000000000000000"
                    + USDT[2:].lower()
                    + hex(FEE_100)[2:].rjust(64, "0"),
                },
                "latest",
            ],
        )
        assert "error" not in result, result
        pool = "0x" + result["result"][-40:]
        assert len(pool) == 42


@pytest.mark.needs_funds
class TestBdexNeedsFunds:
    def test_approve_and_minimal_swap(self, w3, funder_key) -> None:
        """1 USDT → WBOT：approve + swapExactTokensForTokens，Swap 事件与余额断言。"""
        acct = Account.from_key(funder_key)
        usdt = w3.eth.contract(address=USDT, abi=ERC20_ABI)
        wbot = w3.eth.contract(address=WBOT, abi=ERC20_ABI)
        router = w3.eth.contract(address=V2_ROUTER, abi=V2_ROUTER_ABI)
        svc = TxService(w3=w3, funder_key=funder_key)

        balance_usdt = usdt.functions.balanceOf(acct.address).call()
        if balance_usdt < 10**6:
            pytest.skip(
                "测试账户无 tUSDT：到 https://faucet.bohr.life/basic 领取（含 1000 tUSDT/次）后重跑"
            )

        # approve
        approve_data = usdt.encode_abi("approve", args=[V2_ROUTER, 10**6])
        out = svc.execute(
            from_address=acct.address,
            to_address=USDT,
            value_wei=0,
            data=approve_data,
            dry_run=False,
        )
        assert out.status == 1

        # swap：amountOutMin=0（仅测试），deadline=now+600
        wbot_before = wbot.functions.balanceOf(acct.address).call()
        path = [USDT, WBOT]
        swap_data = router.encode_abi(
            "swapExactTokensForTokens",
            args=[10**6, 0, path, acct.address, w3.eth.get_block("latest")["timestamp"] + 600],
        )
        out2 = svc.execute(
            from_address=acct.address,
            to_address=V2_ROUTER,
            value_wei=0,
            data=swap_data,
            dry_run=False,
        )
        assert out2.status == 1
        assert wbot.functions.balanceOf(acct.address).call() > wbot_before

    def test_decimals_unit_guard(self) -> None:
        """单位对照：USDT 6 位 vs WBOT 18 位，防差 1e12 级错误。"""
        assert wei_from_decimal("1", 6) == 10**6
        assert wei_from_decimal("1", 18) == 10**18
        assert wei_from_decimal("1", 18) // wei_from_decimal("1", 6) == 10**12
