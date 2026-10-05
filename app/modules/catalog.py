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
    return row


@router.get("/catalog")
def catalog(request: Request, status: str | None = None) -> JSONResponse:
    """机读目录（Agent 发现服务入口）；响应附 ETag（目录版本）。"""
    rows = _store(request).list_services(status)
    etag = _store(request).catalog_etag()
    return JSONResponse(
        content={"services": rows, "count": len(rows)},
        headers={"ETag": etag},
    )
