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
