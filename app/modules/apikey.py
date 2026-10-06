"""api key 签发与网关侧校验（03 节凭证表的 P0 骨架子集）。

纪律：明文 key 只在签发响应回显一次；落库只存 sha256 hash。
"""

import hashlib
import secrets
import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.errors import ApiError
from app.modules.manifest import ADDRESS_PATTERN
from app.storage.db import CoreStore

router = APIRouter(tags=["apikey"])

API_KEY_PREFIX = "cck_"


class ApiKeyIssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consumer_wallet: str = Field(description="该 key 绑定的消费者钱包（X-PAYMENT.from 必须等于它）")
    quota_raw: int | None = Field(
        default=None, ge=1, description="单笔授权上限（最小单位，空=不限）"
    )

    @field_validator("consumer_wallet")
    @classmethod
    def _wallet(cls, v: str) -> str:
        if not ADDRESS_PATTERN.match(v):
            raise ValueError(f"consumer_wallet 不是合法 EVM 地址: {v!r}")
        return v


class ApiKeyIssueResponse(BaseModel):
    key_id: str
    api_key: str = Field(description="明文 key：仅此一次回显，落库为 hash")
    consumer_wallet: str
    quota_raw: int | None
    created_at: str


class ApiKeyValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=8)


class ApiKeyValidateResponse(BaseModel):
    key_id: str
    consumer_wallet: str
    quota_raw: int | None
    status: str


class ApiKeyRow(BaseModel):
    """列表行（03 §2：不回显 secret；hash 亦不外露）。"""

    key_id: str
    consumer_wallet: str
    quota_raw: int | None
    status: str
    created_at: str


class ApiKeyListResponse(BaseModel):
    keys: list[ApiKeyRow]


class ApiKeyRevokeResponse(BaseModel):
    key_id: str
    status: str


class ApiKeyWalletRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consumer_wallet: str

    @field_validator("consumer_wallet")
    @classmethod
    def _wallet(cls, v: str) -> str:
        if not ADDRESS_PATTERN.match(v):
            raise ValueError(f"consumer_wallet 不是合法 EVM 地址: {v!r}")
        return v


class ApiKeyWalletResponse(BaseModel):
    key_id: str
    consumer_wallet: str
    status: str
    note: str = Field(default="网关 /call 认证实时调 validate，换绑即时生效；无网关侧缓存 TTL 影响")


def _hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def _store(request: Request) -> CoreStore:
    store: CoreStore = request.app.state.store
    return store


@router.post("/apikeys", status_code=201, response_model=ApiKeyIssueResponse)
def issue_api_key(body: ApiKeyIssueRequest, request: Request) -> ApiKeyIssueResponse:
    key_id = f"key_{uuid.uuid4().hex[:12]}"
    api_key = API_KEY_PREFIX + secrets.token_hex(20)
    store = _store(request)
    store.insert_api_key(key_id, _hash_key(api_key), body.consumer_wallet, body.quota_raw)
    return ApiKeyIssueResponse(
        key_id=key_id,
        api_key=api_key,
        consumer_wallet=body.consumer_wallet,
        quota_raw=body.quota_raw,
        created_at=store.api_key_created_at(key_id),
    )


@router.post("/internal/apikeys/validate", response_model=ApiKeyValidateResponse)
def validate_api_key(body: ApiKeyValidateRequest, request: Request) -> ApiKeyValidateResponse:
    """网关调用：api_key → 绑定 wallet + quota（03 凭证表的内部读路径）。"""
    row = _store(request).find_api_key_by_hash(_hash_key(body.api_key))
    if row is None:
        raise ApiError(
            status_code=401,
            error="unauthorized",
            detail="api key 不存在",
            code="apikey_unknown",
        )
    if row["status"] != "active":
        raise ApiError(
            status_code=401,
            error="unauthorized",
            detail=f"api key 已{row['status']}",
            code="apikey_revoked",
        )
    return ApiKeyValidateResponse(
        key_id=row["key_id"],
        consumer_wallet=row["consumer_wallet"],
        quota_raw=row["quota_raw"],
        status=row["status"],
    )


@router.get("/apikeys", response_model=ApiKeyListResponse)
def list_api_keys(request: Request, wallet: str | None = None) -> ApiKeyListResponse:
    """列表（03 §2）：不回显 secret——明文只在签发响应出现一次，hash 亦不外露。

    ?wallet= 按绑定钱包过滤（小写比较）——换机场景查"我的钱包名下有哪些 key"。
    """
    rows = _store(request).list_api_keys()
    if wallet is not None:
        rows = [r for r in rows if str(r.get("consumer_wallet", "")).lower() == wallet.lower()]
    return ApiKeyListResponse(keys=[ApiKeyRow(**row) for row in rows])


@router.delete("/apikeys/{key_id}", response_model=ApiKeyRevokeResponse)
def revoke_api_key(key_id: str, request: Request) -> ApiKeyRevokeResponse:
    """吊销（立即生效）：此后 validate → 401 apikey_revoked，网关侧即拒；幂等。"""
    store = _store(request)
    if store.get_api_key(key_id) is None:
        raise ApiError(
            status_code=404,
            error="not_found",
            detail=f"api key 不存在: {key_id}",
            code="apikey_not_found",
        )
    store.set_api_key_status(key_id, "revoked")
    return ApiKeyRevokeResponse(key_id=key_id, status="revoked")


@router.put("/apikeys/{key_id}/wallet", response_model=ApiKeyWalletResponse)
def rebind_api_key_wallet(
    key_id: str, body: ApiKeyWalletRequest, request: Request
) -> ApiKeyWalletResponse:
    """换绑消费者钱包（X-PAYMENT.from 校验对象随之更新）。

    网关 /call 每次实时调 /internal/apikeys/validate（无缓存），换绑即时生效；
    若未来网关侧引入缓存，需按其 TTL 评估生效延迟（届时改本字段文案）。
    """
    store = _store(request)
    existing = store.get_api_key(key_id)
    if existing is None:
        raise ApiError(
            status_code=404,
            error="not_found",
            detail=f"api key 不存在: {key_id}",
            code="apikey_not_found",
        )
    store.update_api_key_wallet(key_id, body.consumer_wallet)
    return ApiKeyWalletResponse(
        key_id=key_id,
        consumer_wallet=body.consumer_wallet,
        status=existing["status"],
    )
