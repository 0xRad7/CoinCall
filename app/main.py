"""coincall-core 应用工厂：管理面（端口 8020）。"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.core.config import Settings
from app.core.errors import (
    ApiError,
    api_error_handler,
    error_response,
    pydantic_error_handler,
    validation_error_handler,
)
from app.modules.apikey import router as apikey_router
from app.modules.catalog import router as catalog_router
from app.storage.db import CoreStore


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.store = CoreStore(app_settings.duckdb_path)  # C-14：单进程单写者
        yield
        app.state.store.close()

    app = FastAPI(
        title="coincall-core",
        description="CoinCall 管理面：ServiceManifest 目录 + api key 签发/校验",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def trace_middleware(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.trace_id = uuid.uuid4().hex
        return await call_next(request)

    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type] # 注册处签名按异常子类收窄
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ValidationError, pydantic_error_handler)  # type: ignore[arg-type]

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "coincall-core"}

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        return error_response(request, 500, "internal_error", "未处理异常", "internal_error")

    app.include_router(catalog_router)
    app.include_router(apikey_router)
    return app


app = create_app()
