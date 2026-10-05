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
from app.core.eip712 import domain_separator, sign_agent_wallet_set
from app.core.errors import TxRevertedError
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

    def test_eip712_domain_onchain_matches_findings(self, w3) -> None:
        """C-23 定案对账：链上 eip712Domain()（IERC5267）与源码归档结论一致。"""
        reg = w3.eth.contract(address=IDENTITY, abi=IDENTITY_REGISTRY_ABI)
        domain = reg.functions.eip712Domain().call()
        _fields, name, version, chain_id, verifying, salt = domain[:6]
        assert name == "ERC8004IdentityRegistry"
        assert version == "1"
        assert chain_id == 968
        assert verifying.lower() == IDENTITY.lower()  # verifyingContract = 代理地址
        assert bytes(salt) == b"\x00" * 32
        # 链上声明字段经本地重算 == 黄金 domainSeparator（签名路径逐字节锁死，test_00 同值）
        assert Web3.to_hex(domain_separator(chain_id, verifying)) == (
            "0x2afa5c5221b1fc50a3423f69446f07999dafe2a83724420ab5a34586a29362cf"
        )

    def test_set_agent_wallet_full_loop(self, w3, funder_key) -> None:
        """P0-1 端点 A 链上回环：注册 → 新钱包 EIP-712 签名（服务侧代签语义）→ 绑定成功。"""
        owner = Account.from_key(funder_key)
        reg = w3.eth.contract(address=IDENTITY, abi=IDENTITY_REGISTRY_ABI)
        svc = TxService(w3=w3, funder_key=funder_key)
        register_data = reg.encode_abi(
            "register", args=["https://bot-chain-api.local/agents/wallet-bind"]
        )
        out = svc.execute(
            from_address=owner.address,
            to_address=IDENTITY,
            value_wei=0,
            data=register_data,
            dry_run=False,
        )
        receipt = w3.eth.get_transaction_receipt(out.tx_hash)
        agent_id = next(
            int(e.args.tokenId)
            for e in reg.events.Transfer().process_receipt(receipt)
            if e.args.to == owner.address
        )

        new_wallet = Account.create()
        deadline = int(w3.eth.get_block("latest")["timestamp"]) + 120
        signature = sign_agent_wallet_set(
            new_wallet,
            chain_id=968,
            registry=IDENTITY,
            agent_id=agent_id,
            new_wallet=new_wallet.address,
            owner=owner.address,
            deadline=deadline,
        )
        data = reg.encode_abi(
            "setAgentWallet", args=[agent_id, new_wallet.address, deadline, signature]
        )
        bound = svc.execute(
            from_address=owner.address,  # 交易发送者必须是 owner（源码 msg.sender 检查）
            to_address=IDENTITY,
            value_wei=0,
            data=data,
            dry_run=False,
        )
        assert bound.status == 1
        assert reg.functions.getAgentWallet(agent_id).call() == new_wallet.address

    def test_set_agent_wallet_rejects_non_wallet_signer(self, w3, funder_key) -> None:
        """负向实证：owner 签名（而非 newWallet 本人）必 revert（estimate 阶段拦截，不花 gas）。"""
        owner = Account.from_key(funder_key)
        reg = w3.eth.contract(address=IDENTITY, abi=IDENTITY_REGISTRY_ABI)
        svc = TxService(w3=w3, funder_key=funder_key)
        register_data = reg.encode_abi("register", args=["https://bot-chain-api.local/agents/neg"])
        out = svc.execute(
            from_address=owner.address,
            to_address=IDENTITY,
            value_wei=0,
            data=register_data,
            dry_run=False,
        )
        receipt = w3.eth.get_transaction_receipt(out.tx_hash)
        agent_id = next(
            int(e.args.tokenId)
            for e in reg.events.Transfer().process_receipt(receipt)
            if e.args.to == owner.address
        )
        fake_wallet = Account.create()
        deadline = int(w3.eth.get_block("latest")["timestamp"]) + 120
        wrong_sig = sign_agent_wallet_set(
            owner,  # 错签者：owner 而非 newWallet
            chain_id=968,
            registry=IDENTITY,
            agent_id=agent_id,
            new_wallet=fake_wallet.address,
            owner=owner.address,
            deadline=deadline,
        )
        data = reg.encode_abi(
            "setAgentWallet", args=[agent_id, fake_wallet.address, deadline, wrong_sig]
        )
        with pytest.raises(TxRevertedError):
            svc.execute(
                from_address=owner.address,
                to_address=IDENTITY,
                value_wei=0,
                data=data,
                dry_run=False,
            )
