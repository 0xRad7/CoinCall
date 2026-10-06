"""manifests 目录 API：发布/更新、单读、机读目录（01 §5 的 P0 子集）。"""

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.errors import ApiError
from app.modules.manifest import ServiceManifest, manifest_hash
from app.storage.db import CoreStore

router = APIRouter(tags=["manifests"])


class ManifestAck(BaseModel):
    service_id: str
    status: str
    manifest_hash: str
    manifest: dict[str, object]


def _store(request: Request) -> CoreStore:
    store: CoreStore = request.app.state.store
    return store


def _redact_row(row: dict[str, object]) -> dict[str, object]:
    """公开面脱敏：抹去 endpoint.url——真实上游只经网关内部通道消费，
    消费者的调用端点恒为 POST {gateway}/call/{service_id}（防绕过付费直连）。"""
    out = dict(row)
    manifest = out.get("manifest")
    if isinstance(manifest, dict) and isinstance(manifest.get("endpoint"), dict):
        manifest = dict(manifest)
        endpoint = dict(manifest["endpoint"])
        endpoint["url"] = None
        manifest["endpoint"] = endpoint
        out["manifest"] = manifest
    return out


@router.post("/manifests", status_code=201, response_model=ManifestAck)
def publish_manifest(body: dict[str, object], request: Request) -> ManifestAck:
    """发布/更新 ServiceManifest（upsert by service_id）。

    非法 manifest → 422 三段错误（01 §7-3）。
    """
    existing = _store(request).get_service(str(body.get("service_id", "")))
    try:
        manifest = ServiceManifest.model_validate(body)
    except Exception as exc:
        raise ApiError(
            status_code=422,
            error="manifest_invalid",
            detail=f"manifest 校验失败: {exc}",
            code="manifest_invalid",
        ) from exc
    if existing is not None:
        manifest.created_at = ServiceManifest.model_validate(existing["manifest"]).created_at
    else:
        manifest.touch()
    digest = manifest_hash(manifest)
    payload = json.loads(manifest.model_dump_json())
    _store(request).upsert_service(manifest.service_id, payload, manifest.status.value, digest)
    return ManifestAck(
        service_id=manifest.service_id,
        status=manifest.status.value,
        manifest_hash=digest,
        manifest=payload,
    )


@router.get("/manifests/{service_id}")
def get_manifest(service_id: str, request: Request) -> dict[str, object]:
    row = _store(request).get_service(service_id)
    if row is None:
        raise ApiError(
            status_code=404,
            error="not_found",
            detail=f"service 不存在: {service_id}",
            code="service_not_found",
        )
    return _redact_row(row)


@router.get("/internal/manifests/{service_id}")
def get_manifest_internal(service_id: str, request: Request) -> dict[str, object]:
    """网关转发用全量 manifest（含 endpoint.url）；本机管理面 posture（同 /internal/*）。"""
    row = _store(request).get_service(service_id)
    if row is None:
        raise ApiError(
            status_code=404,
            error="not_found",
            detail=f"service 不存在: {service_id}",
            code="service_not_found",
        )
    return row


@router.get("/catalog")
def catalog(request: Request, status: str | None = None) -> JSONResponse:
    """机读目录（Agent 发现服务入口）；响应附 ETag（目录版本）。公开面已脱敏 url。"""
    rows = [_redact_row(r) for r in _store(request).list_services(status)]
    etag = _store(request).catalog_etag()
    return JSONResponse(
        content={"services": rows, "count": len(rows)},
        headers={"ETag": etag},
    )
