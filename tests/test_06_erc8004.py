"""live + needs_funds：ERC-8004 Agent 身份（02 篇 test_06 矩阵）。

证据出处：results/d3_probes/（三合约 ABI 经 EIP-1967 implementation 取得）。
"""

import pytest
from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError

from app.core.abis.erc8004 import (
    IDENTITY_REGISTRY_ABI,
    REPUTATION_REGISTRY_ABI,
    VALIDATION_REGISTRY_ABI,
)
from app.core.chains import get_chain
from app.core.tx import TxService

pytestmark = pytest.mark.live

CHAIN = get_chain("testnet")
IDENTITY = CHAIN.contracts.identity_registry
REPUTATION = CHAIN.contracts.reputation_registry
VALIDATION = CHAIN.contracts.validation_registry


def _recent_token_id(w3, explorer) -> int:
    """从 Blockscout token transfers 取最近 mint 的 agentId（IdentityRegistry 是 ERC-721）。"""
    raw = explorer.token_transfers(IDENTITY, {"filter_type": "token_transfer"})
    for item in raw.get("items", []):
        tid = item.get("total", {}).get("token_id")
        if tid and str(tid).isdigit():
            return int(tid)
    raise AssertionError("explorer 未取到已注册 agentId")


class TestErc8004Live:
    def test_registry_name_symbol(self, w3) -> None:
        reg = w3.eth.contract(address=IDENTITY, abi=IDENTITY_REGISTRY_ABI)
        assert reg.functions.name().call() == "AgentIdentity"
        assert reg.functions.symbol().call() == "AGENT"

    def test_owner_of_nonexistent_reverts(self, w3) -> None:
        reg = w3.eth.contract(address=IDENTITY, abi=IDENTITY_REGISTRY_ABI)
        with pytest.raises(ContractLogicError):
            reg.functions.ownerOf(9_999_999).call()

    def test_reputation_and_validation_deployed_and_linked(self, w3) -> None:
        """三合约有代码，且 Reputation/Validation 都指向同一 IdentityRegistry。"""
        assert w3.eth.get_code(REPUTATION) not in (b"", "0x")
        assert w3.eth.get_code(VALIDATION) not in (b"", "0x")
        rep = w3.eth.contract(address=REPUTATION, abi=REPUTATION_REGISTRY_ABI)
        val = w3.eth.contract(address=VALIDATION, abi=VALIDATION_REGISTRY_ABI)
        assert rep.functions.getIdentityRegistry().call().lower() == IDENTITY.lower()
        assert val.functions.getIdentityRegistry().call().lower() == IDENTITY.lower()

    def test_registered_identity_aggregate_view(self, w3, explorer) -> None:
        """对已存在 agentId：ownerOf/tokenURI/getAgentWallet 三读聚合可读。"""
        token_id = _recent_token_id(w3, explorer)
        reg = w3.eth.contract(address=IDENTITY, abi=IDENTITY_REGISTRY_ABI)
        owner = reg.functions.ownerOf(token_id).call()
        assert Web3.is_address(owner)
        uri = reg.functions.tokenURI(token_id).call()
        assert isinstance(uri, str)
        wallet = reg.functions.getAgentWallet(token_id).call()
        assert Web3.is_address(wallet)  # 未设置时为零地址，也返回正常


@pytest.mark.needs_funds
class TestErc8004NeedsFunds:
    def test_register_and_verify(self, w3, funder_key) -> None:
        """注册一个测试身份：register(uri) → 回执解析 agentId → ownerOf==注册者。"""
        acct = Account.from_key(funder_key)
        reg = w3.eth.contract(address=IDENTITY, abi=IDENTITY_REGISTRY_ABI)
        data = reg.encode_abi("register", args=["https://bot-chain-api.local/agents/demo"])
        svc = TxService(w3=w3, funder_key=funder_key)
        out = svc.execute(
            from_address=acct.address,
            to_address=IDENTITY,
            value_wei=0,
            data=data,
            dry_run=False,
        )
        assert out.status == 1
        # 回执解析 agentId：event Transfer(to=owner, tokenId) 在 logs[0]
        receipt = w3.eth.get_transaction_receipt(out.tx_hash)
        transfer = reg.events.Transfer().process_receipt(receipt)
        agent_ids = [int(e.args.tokenId) for e in transfer if e.args.to == acct.address]
        assert agent_ids, f"未解析出 agentId: {receipt.logs}"
        agent_id = agent_ids[0]
        assert reg.functions.ownerOf(agent_id).call() == acct.address
