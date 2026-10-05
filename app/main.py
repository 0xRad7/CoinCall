"""coincall-core 应用工厂：管理面（端口 8020）。

依赖注入约定：create_app 的可选关键字参数即单测替身入口
（identity/chain/gateway 客户端），生产路径在 lifespan 装配真实实现
（httpx → 8010 bot-chain-api 与 8030 gateway；trust_env=False 防 C-07 代理劫持）。
"""

import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
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
from app.modules.identity import BotChainIdentityClient, IdentityClient
from app.modules.leaderboard import (
    BotChainClient,
    ChainSource,
    ChargedIndexer,
    GatewayStatsClient,
    GatewayStatsSource,
)
from app.modules.leaderboard import (
    router as leaderboard_router,
)
from app.modules.providers import router as providers_router
from app.storage.db import CoreStore


def create_app(
    settings: Settings | None = None,
    *,
    identity_client: IdentityClient | None = None,
    chain_client: ChainSource | None = None,
    gateway_client: GatewayStatsSource | None = None,
) -> FastAPI:
    """chain_client/gateway_client 即排行榜双源注入口（生产装配真实 8010/8030 客户端）。"""
    app_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.store = CoreStore(app_settings.duckdb_path)  # C-14：单进程单写者
        # trust_env=False：8010/8030 都是本机服务（gateway C-07 同源纪律）
        http = httpx.Client(timeout=10.0, trust_env=False)
        app.state.identities = identity_client or BotChainIdentityClient(
            http, app_settings.bot_chain_api_base_url, app_settings.identity_cache_ttl
        )
        app.state.chain = chain_client or BotChainClient(
            http=http,
            base_url=app_settings.bot_chain_api_base_url,
            pay_vault=app_settings.pay_vault_address,
        )
        app.state.gateway_stats = gateway_client or GatewayStatsClient(
            http, app_settings.gateway_base_url
        )
        app.state.charged_indexer = ChargedIndexer(
            store=app.state.store,
            chain=app.state.chain,
            deploy_block=app_settings.pay_vault_deploy_block,
            window=app_settings.charged_sync_window,
            safety=app_settings.charged_sync_safety,
        )
        app.state.leaderboard_sync = {
            "lock": threading.Lock(),
            "last_ok_ts": 0.0,
            "min_interval": app_settings.leaderboard_min_sync_interval,
        }
        yield
        http.close()
        app.state.store.close()

    app = FastAPI(
        title="coincall-core",
        description=(
            "CoinCall 管理面：ServiceManifest 目录 + api key 生命周期 + Provider 登记"
            " + 排行榜（链上 Charged 收入真相 + 网关活跃度双源）"
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    if app_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in app_settings.cors_origins.split(",") if o.strip()],
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials=False,
            expose_headers=["X-Receipt-Id", "X-Charged-Raw", "X-Receipt-Sig", "ETag"],
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
    app.include_router(providers_router)
    app.include_router(leaderboard_router)
    return app


app = create_app()
