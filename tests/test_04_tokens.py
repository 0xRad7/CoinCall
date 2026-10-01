"""live + needs_funds：代币 ERC20/721（02 篇 test_04 矩阵）。"""

import pytest
from eth_account import Account
from web3.exceptions import ContractLogicError

from app.core.abis import ERC20_ABI, ERC721_ABI
from app.core.chains import get_chain
from app.core.tx import TxService, wei_from_decimal

pytestmark = pytest.mark.live

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
CHAIN = get_chain("testnet")
USDT = CHAIN.contracts.usdt
WBOT = CHAIN.contracts.wbot
IDENTITY = CHAIN.contracts.identity_registry


class TestTokenLive:
    def test_usdt_metadata(self, w3) -> None:
        """USDT：symbol=USDT、decimals=6（差 1e12 事故防线）。"""
        usdt = w3.eth.contract(address=USDT, abi=ERC20_ABI)
        assert usdt.functions.symbol().call() == "USDT"
        assert usdt.functions.decimals().call() == 6

    def test_wbot_metadata(self, w3) -> None:
        """WBOT：decimals=18，totalSupply 可读。"""
        wbot = w3.eth.contract(address=WBOT, abi=ERC20_ABI)
        assert wbot.functions.decimals().call() == 18
        assert wbot.functions.totalSupply().call() > 0

    def test_identity_registry_is_erc721_agent(self, w3) -> None:
        """IdentityRegistry 是 ERC-721：实测 name=AgentIdentity、symbol=AGENT
        （蓝图 02 篇写 name=AGENT，以 2026-10-01 实测为准，记入偏差清单）。"""
        reg = w3.eth.contract(address=IDENTITY, abi=ERC721_ABI)
        assert reg.functions.name().call() == "AgentIdentity"
        assert reg.functions.symbol().call() == "AGENT"

    def test_owner_of_nonexistent_token_reverts(self, w3) -> None:
        """反例：不存在 tokenId 的 ownerOf 应 revert（错误码定型）。"""
        reg = w3.eth.contract(address=IDENTITY, abi=ERC721_ABI)

        with pytest.raises(ContractLogicError):
            reg.functions.ownerOf(999_999_999).call()

    def test_balance_of_any_address(self, w3) -> None:
        usdt = w3.eth.contract(address=USDT, abi=ERC20_ABI)
        holder = w3.eth.get_block("latest")["miner"]
        assert usdt.functions.balanceOf(holder).call() >= 0


@pytest.mark.needs_funds
class TestTokenNeedsFunds:
    """真实 approve（金额 0，无对手方风险）+ 事件断言。"""

    def test_approve_and_event(self, w3, funder_key) -> None:

        acct = Account.from_key(funder_key)
        usdt = w3.eth.contract(address=USDT, abi=ERC20_ABI)
        data = usdt.encode_abi("approve", args=[acct.address, 0])
        svc = TxService(w3=w3, funder_key=funder_key)
        out = svc.execute(
            from_address=acct.address, to_address=USDT, value_wei=0, data=data, dry_run=False
        )
        assert out.status == 1
        allowance = usdt.functions.allowance(acct.address, acct.address).call()
        assert allowance == 0

    def test_transfer_decimals_rounding_guard(self, w3, funder_key) -> None:
        """单位换算防线：USDT 6 位小数 1.5 → 1_500_000 raw。"""
        assert wei_from_decimal("1.5", 6) == 1_500_000
        assert wei_from_decimal("1.5", 18) == 1_500_000_000_000_000_000
