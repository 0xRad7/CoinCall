"""上游凭证（endpoint credentials，01 §endpoint-credentials）。

公开 manifest 与凭证分离：三方接口的认证头（如 X-API-KEY）只存在本模块的
加密表里，**永不**出现在 catalog/manifest 响应；网关经 internal resolve 取用后
在转发时注入。消费者入站头（X-Api-Key/X-PAYMENT）不上透传（02 §2 既有语义）。

信任模型（诚实边界）：Provider 的上游密钥必然交给代理方（Kong/Apigee 同款
API 网关模型），与消费者资金钥匙是两个信任域，P7 不受影响；不想交的 Provider
可自包一层薄适配服务（internal 端点型）再上架。
"""

import base64
import hashlib
import json
import re

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.storage.db import CoreStore

router = APIRouter(tags=["credentials"])

_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


class CredentialsPutRequest(BaseModel):
    """整体替换式写入（幂等）。"""

    model_config = ConfigDict(extra="forbid")

    headers: dict[str, str] = Field(
        description="转发时注入上游的头（如 X-API-KEY: sk-…）；全量替换，空对象=清空"
    )


def build_fernet(secret: str) -> Fernet:
    """main lifespan 构建一次挂 state（credentials 与 main 共用，避免每请求派生）。"""
    return _fernet(secret)


def _fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return Fernet(key)


def _store(request: Request) -> CoreStore:
    return request.app.state.store


@router.put("/services/{service_id}/credentials")
def put_credentials(service_id: str, body: CredentialsPutRequest, request: Request) -> dict:
    """写入/替换某服务的上游凭证头。值加密落盘；响应只回名字。"""
    store = _store(request)
    if store.get_service(service_id) is None:
        raise HTTPException(status_code=404, detail="service_not_found")
    for name in body.headers:
        if not _HEADER_NAME_RE.match(name):
            raise HTTPException(status_code=422, detail=f"非法头名: {name!r}")
    cipher = request.app.state.credential_fernet.encrypt(_canonical_json(body.headers).encode())
    store.upsert_service_credentials(service_id, cipher, sorted(body.headers))
    return {"service_id": service_id, "header_names": sorted(body.headers), "updated": True}


@router.get("/services/{service_id}/credentials")
def get_credentials_masked(service_id: str, request: Request) -> dict:
    """公开面：只返回头名列表与更新时间，值永不回显。"""
    row = _store(request).get_service_credentials_cipher(service_id)
    if row is None:
        return {"service_id": service_id, "header_names": []}
    return {"service_id": service_id, "header_names": row[1]}


@router.delete("/services/{service_id}/credentials")
def delete_credentials(service_id: str, request: Request) -> dict:
    _store(request).delete_service_credentials(service_id)
    return {"service_id": service_id, "deleted": True}


@router.get("/internal/services/{service_id}/credentials")
def resolve_credentials(service_id: str, request: Request) -> dict:
    """internal：网关取用（本机管理面，P0 无鉴权 posture 与 /internal/apikeys 一致）。"""
    row = _store(request).get_service_credentials_cipher(service_id)
    if row is None:
        return {"service_id": service_id, "headers": {}}
    cipher, _names = row
    try:
        headers = _loads(request.app.state.credential_fernet.decrypt(cipher).decode())
    except InvalidToken as exc:  # pragma: no cover - 密钥轮换场景
        raise HTTPException(status_code=500, detail="credential_decrypt_failed") from exc
    return {"service_id": service_id, "headers": headers}


def _canonical_json(headers: dict[str, str]) -> str:
    return json.dumps(headers, sort_keys=True, separators=(",", ":"))


def _loads(raw: str) -> dict[str, str]:
    return {str(k): str(v) for k, v in json.loads(raw).items()}
