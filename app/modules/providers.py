"""Provider 登记（01 §2 步骤③/§5）：先校验 ERC-8004 身份在链上存在，再落库。"""

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ApiError
from app.modules.identity import IdentityClient
from app.storage.db import CoreStore

router = APIRouter(tags=["providers"])


class ProviderRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: int = Field(ge=1, description="ERC-8004 tokenId（链上须已注册）")
    display_name: str = Field(min_length=1, max_length=128)


class ProviderRow(BaseModel):
    agent_id: int
    display_name: str
    wallet: str = Field(description="链上 agentWallet（登记时经 8010 回读；小写归一）")
    created_at: str


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
    info = _identities(request).get(body.agent_id)
    if info is None:
        raise ApiError(
            status_code=422,
            error="identity_not_found",
            detail=f"agent_id={body.agent_id} 在链上不存在（ERC-8004 tokenId 未注册）",
            code="identity_not_found",
        )
    store = _store(request)
    store.upsert_provider(body.agent_id, body.display_name, info.agent_wallet.lower())
    row = store.get_provider(body.agent_id)
    if row is None:  # upsert 后必存在，防御分支
        raise ApiError(
            status_code=500,
            error="internal_error",
            detail="provider 落库失败",
            code="persist_failed",
        )
    return ProviderRow(**row)


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
