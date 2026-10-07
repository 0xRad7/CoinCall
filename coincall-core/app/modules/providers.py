"""Provider 登记（01 §2 步骤③/§5）：先校验 ERC-8004 身份在链上存在，再落库。"""

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ApiError
from app.modules.identity import IdentityClient
from app.storage.db import CoreStore

router = APIRouter(tags=["providers"])


CUSTODIAN_DEFAULT = "0xc37ffe97b4d2c3d0187b1ddedf273e52a461b63a"


class ProviderRegisterRequest(BaseModel):
    """认领式登记（认证先行）：claim_wallet 必须等于链上 agentWallet（绑定完成后方可登记）。"""

    model_config = ConfigDict(extra="forbid")

    agent_id: int = Field(ge=1, description="ERC-8004 tokenId（链上须已注册）")
    display_name: str = Field(min_length=1, max_length=128)
    claim_wallet: str = Field(description="认领钱包（=连接的钱包；须等于链上 agentWallet）")


class ProviderRow(BaseModel):
    agent_id: int
    display_name: str
    wallet: str = Field(description="链上 agentWallet（登记时经 8010 回读；小写归一）")
    claim_wallet: str | None = Field(
        default=None, description="认领钱包（first-claim-wins；None=旧数据未认领）"
    )
    created_at: str


class ClaimStateResponse(BaseModel):
    agent_id: int
    identity_found: bool
    agent_wallet: str | None = None
    claimed_by_wallet: str | None = None
    platform_custodian: str


class ProviderListResponse(BaseModel):
    providers: list[ProviderRow]


class ProviderServicesResponse(BaseModel):
    agent_id: int
    services: list[dict[str, object]]


def _store(request: Request) -> CoreStore:
    store: CoreStore = request.app.state.store
    return store


def _identities(request: Request) -> IdentityClient:
    client: IdentityClient = request.app.state.identities
    return client


@router.post("/providers", status_code=201, response_model=ProviderRow)
def register_provider(body: ProviderRegisterRequest, request: Request) -> ProviderRow:
    """登记 provider（01 §5）：agent_id 经 8010 校验 ERC-8004 身份存在，404→422。

    登记时顺带回读链上 agentWallet 冗余缓存（发布 manifest 时的绑定校验以链上为准）。
    """
    info = _identities(request).get(body.agent_id, force=True)
    if info is None:
        raise ApiError(
            status_code=422,
            error="identity_not_found",
            detail=f"agent_id={body.agent_id} 在链上不存在（ERC-8004 tokenId 未注册）",
            code="identity_not_found",
        )
    claim = body.claim_wallet.lower()
    store = _store(request)
    existing = store.get_provider(body.agent_id)
    if existing is not None and existing.get("claim_wallet") not in (None, claim):
        raise ApiError(
            status_code=409,
            error="identity_already_claimed",
            detail=(
                f"agent_id={body.agent_id} 已被钱包 {existing['claim_wallet']} 认领"
                "（一身份一认领；如为身份主人请先用认领钱包操作）"
            ),
            code="identity_already_claimed",
        )
    if info.agent_wallet.lower() != claim:
        raise ApiError(
            status_code=422,
            error="claim_requires_binding",
            detail=(
                f"链上 agentWallet={info.agent_wallet} ≠ 认领钱包 {claim}："
                "请先在控制台完成身份钱包绑定（连接钱包签名 AgentWalletSet）再登记"
            ),
            code="claim_requires_binding",
        )
    store.upsert_provider(body.agent_id, body.display_name, info.agent_wallet.lower(), claim)
    row = store.get_provider(body.agent_id)
    if row is None:  # upsert 后必存在，防御分支
        raise ApiError(
            status_code=500,
            error="internal_error",
            detail="provider 落库失败",
            code="persist_failed",
        )
    return ProviderRow(**row)


@router.get("/providers/{agent_id}/claim-state", response_model=ClaimStateResponse)
def provider_claim_state(agent_id: int, request: Request) -> ClaimStateResponse:
    """认领三态预检：identity_found / agent_wallet / claimed_by_wallet / 平台托管地址。"""
    info = _identities(request).get(agent_id)
    row = _store(request).get_provider(agent_id)
    custodian = request.app.state.app_settings.platform_custodian_address
    return ClaimStateResponse(
        agent_id=agent_id,
        identity_found=info is not None,
        agent_wallet=info.agent_wallet.lower() if info else None,
        claimed_by_wallet=(row or {}).get("claim_wallet"),
        platform_custodian=custodian,
    )


@router.get("/providers", response_model=ProviderListResponse)
def list_providers(request: Request) -> ProviderListResponse:
    rows = _store(request).list_providers()
    return ProviderListResponse(providers=[ProviderRow(**row) for row in rows])


@router.get("/providers/{agent_id}/services", response_model=ProviderServicesResponse)
def provider_services(agent_id: int, request: Request) -> ProviderServicesResponse:
    """providers ↔ manifests 关联：该 provider 名下已发布服务（01 §5）。"""
    store = _store(request)
    if store.get_provider(agent_id) is None:
        raise ApiError(
            status_code=404,
            error="not_found",
            detail=f"provider 不存在: {agent_id}",
            code="provider_not_found",
        )
    services = store.find_services_by_agent(agent_id)
    return ProviderServicesResponse(agent_id=agent_id, services=services)
