"""M6 ERC-8004 Agent 身份：contracts/{tokenId}/registry/register✏/reputation/validations。"""

from typing import Any, cast

from eth_account import Account
from eth_typing import ChecksumAddress
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from web3 import Web3
from web3.exceptions import ContractLogicError, Web3Exception

from app.core.abis.erc8004 import (
    IDENTITY_REGISTRY_ABI,
    REPUTATION_REGISTRY_ABI,
    VALIDATION_REGISTRY_ABI,
)
from app.core.deps import ChainDep, ExplorerDep, SettingsDep, TxServiceDep, Web3Dep
from app.core.errors import ChainError, ServiceError, TxRevertedError
from app.core.rpc import checksum, contract_at

router = APIRouter(prefix="/agent-identity", tags=["M6 agent-identity"])


class ContractStatus(BaseModel):
    name: str
    address: str
    deployed: bool
    note: str = ""


class ContractsView(BaseModel):
    identity_registry: ContractStatus
    reputation_registry: ContractStatus
    validation_registry: ContractStatus


class IdentityView(BaseModel):
    token_id: int
    owner: str
    token_uri: str
    agent_wallet: str


class RegisterRequest(BaseModel):
    owner: str | None = Field(default=None, description="默认取环境变量出资账户")
    agent_uri: str = Field(default="https://bot-chain-api.local/agents/default")
    dry_run: bool = True


class ReputationView(BaseModel):
    token_id: int
    summary: dict[str, Any] | None = None
    note: str = ""


class ValidationsView(BaseModel):
    token_id: int
    validations: list[Any] = Field(default_factory=list)
    note: str = ""


@router.get("/contracts", response_model=ContractsView)
def erc8004_contracts(w3: Web3Dep, chain: ChainDep) -> ContractsView:
    def status(name: str, address: str) -> ContractStatus:
        return ContractStatus(
            name=name,
            address=address,
            deployed=w3.eth.get_code(cast(ChecksumAddress, checksum(address))) not in (b"", "0x"),
        )

    return ContractsView(
        identity_registry=status("IdentityRegistry", chain.contracts.identity_registry),
        reputation_registry=status("ReputationRegistry", chain.contracts.reputation_registry),
        validation_registry=status("ValidationRegistry", chain.contracts.validation_registry),
    )


@router.get("/registry")
def erc8004_registry(
    explorer: ExplorerDep,
    chain: ChainDep,
    items_count: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """已注册身份分页（Blockscout token transfers 视角；mint=Transfer from 0x0）。"""
    raw = explorer.token_transfers(chain.contracts.identity_registry)
    items = [
        {
            "token_id": item.get("total", {}).get("value"),
            "tx_hash": item.get("tx_hash"),
            "from": item.get("from", {}).get("hash"),
            "to": item.get("to", {}).get("hash"),
            "timestamp": item.get("timestamp"),
        }
        for item in raw.get("items", [])[:items_count]
    ]
    return {"items": items, "next_page_params": raw.get("next_page_params")}


@router.get("/{token_id}", response_model=IdentityView)
def erc8004_identity(w3: Web3Dep, chain: ChainDep, token_id: int) -> IdentityView:
    reg = contract_at(w3, chain.contracts.identity_registry, IDENTITY_REGISTRY_ABI)
    try:
        owner = reg.functions.ownerOf(token_id).call()
        uri = reg.functions.tokenURI(token_id).call()
        wallet = reg.functions.getAgentWallet(token_id).call()
    except ContractLogicError as exc:
        raise TxRevertedError(f"tokenId {token_id} 不存在或未注册: {exc}") from exc
    except Web3Exception as exc:
        msg = f"RPC 失败: {exc}"
        raise ChainError(msg) from exc
    return IdentityView(token_id=token_id, owner=owner, token_uri=uri, agent_wallet=wallet)


@router.post(
    "/register",
    description="注册新 agent 身份 register(agentURI)；dry_run=true 默认仅预览",
)
def erc8004_register(
    request: RegisterRequest,
    w3: Web3Dep,
    chain: ChainDep,
    settings: SettingsDep,
    tx_service: TxServiceDep,
) -> dict[str, Any]:
    owner = Web3.to_checksum_address(request.owner) if request.owner else None
    if owner is None:
        if not settings.funded_key:
            msg = "未提供 owner 且环境变量未配置 BOT_CHAIN_TEST_PRIVATE_KEY"
            raise ServiceError(msg, code="no_owner")
        owner = Account.from_key(settings.funded_key).address
    reg = contract_at(w3, chain.contracts.identity_registry, IDENTITY_REGISTRY_ABI)
    data = reg.encode_abi("register", args=[request.agent_uri])
    outcome = tx_service.execute(
        from_address=owner,
        to_address=chain.contracts.identity_registry,
        value_wei=0,
        data=data,
        dry_run=request.dry_run,
    )
    return outcome.model_dump()


@router.get("/{token_id}/reputation", response_model=ReputationView)
def erc8004_reputation(w3: Web3Dep, chain: ChainDep, token_id: int) -> ReputationView:
    rep = contract_at(w3, chain.contracts.reputation_registry, REPUTATION_REGISTRY_ABI)
    try:
        summary = rep.functions.getSummary(token_id, [], "total", "desc").call()
    except (ContractLogicError, Web3Exception):
        summary = None
    return ReputationView(
        token_id=token_id,
        summary={"raw": list(summary)} if summary else None,
        note="ReputationRegistry.getSummary（无数据时为空）",
    )


@router.get("/{token_id}/validations", response_model=ValidationsView)
def erc8004_validations(w3: Web3Dep, chain: ChainDep, token_id: int) -> ValidationsView:
    val = contract_at(w3, chain.contracts.validation_registry, VALIDATION_REGISTRY_ABI)
    try:
        items = list(val.functions.getAgentValidations(token_id).call())
    except (ContractLogicError, Web3Exception):
        items = []
    return ValidationsView(token_id=token_id, validations=items)
