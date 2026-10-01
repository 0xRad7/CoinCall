"""FastAPI 入口：lifespan 构建客户端、路由注册、错误模型、trace_id 中间件、文档页。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import redis
from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app import __version__
from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.explorer import ExplorerClient
from app.core.idempotency import IdempotencyStore
from app.core.keystore import Keystore
from app.core.log import TraceIdMiddleware, get_logger, setup_logging
from app.core.rpc import make_http_client, make_web3, resolve_proxy
from app.core.tx import TxService
from app.modules import (
    accounts,
    bdex,
    chain_info,
    contracts,
    erc4337,
    erc8004,
    faucet,
    indexer,
    tokens,
    transactions,
)
from app.storage.duckdb import DuckStore
from app.storage.redis_store import RedisStore

logger = get_logger(__name__)

TAGS_METADATA: list[dict[str, str]] = [
    {"name": "meta", "description": "服务元信息"},
    {"name": "M1 chain", "description": "链信息: info/gas/stats/blocks/health"},
    {"name": "M2 accounts", "description": "账户: 生成/余额/nonce/历史"},
    {"name": "M3 tx", "description": "交易: 转账/裸交易/回执/事件. 写接口 dry_run=true 默认"},
    {"name": "M4 tokens", "description": "代币 ERC20/721/1155. 写接口 dry_run=true 默认"},
    {"name": "M5 aa", "description": "ERC-4337 账户抽象. 写接口 dry_run=true 默认"},
    {"name": "M6 agent-identity", "description": "ERC-8004 Agent 身份. 写接口 dry_run=true 默认"},
    {"name": "M7 bdex", "description": "BDEX V2/V3 交易. 写接口 dry_run=true 默认"},
    {
        "name": "M8 contracts",
        "description": "通用合约 call/send/deploy/decode. 写接口 dry_run=true 默认",
    },
    {"name": "M9 indexer", "description": "索引同步与订阅 (→DuckDB/Redis)"},
    {"name": "M10 faucet", "description": "水龙头探测与领水"},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    spec = settings.chain_spec
    app.state.settings = settings
    app.state.chain = spec
    app.state.w3 = make_web3(spec, proxy=settings.proxy)
    explorer_http = make_http_client(
        spec.explorer_api, proxy=resolve_proxy(spec.explorer_api, settings.proxy)
    )
    app.state.explorer_http = explorer_http
    app.state.explorer_api = ExplorerClient(explorer_http)
    app.state.bundler = make_http_client(
        spec.bundler_url, proxy=resolve_proxy(spec.bundler_url, settings.proxy)
    )
    app.state.faucet = make_http_client(
        spec.faucet_api_url, proxy=resolve_proxy(spec.faucet_api_url, settings.proxy)
    )
    app.state.store = DuckStore(settings.duckdb_path, network=spec.network.value)
    app.state.indexer = indexer.IndexerService(w3=app.state.w3, store=app.state.store, chain=spec)
    redis_client = None
    if settings.redis_url:
        redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.redis = RedisStore(redis_client) if redis_client else None
    app.state.idempotency = IdempotencyStore(app.state.redis)
    logger.info(
        "storage ready",
        extra={"duckdb": settings.duckdb_path, "redis": bool(redis_client)},
    )
    keystore_secret = (
        settings.bot_chain_keystore_secret.get_secret_value()
        if settings.bot_chain_keystore_secret
        else None
    )
    app.state.keystore = Keystore(Path("data/keystore"), keystore_secret)
    app.state.tx = TxService(
        w3=app.state.w3, funder_key=settings.funded_key, keystore=app.state.keystore
    )
    logger.info(
        "lifespan startup",
        extra={"network": spec.network.value, "chain_id": spec.chain_id, "rpc": spec.rpc_url},
    )
    yield
    explorer_http.close()
    app.state.bundler.close()
    app.state.faucet.close()
    app.state.store.close()
    if redis_client is not None:
        redis_client.close()
    logger.info("lifespan shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="BOT Chain API",
        version=__version__,
        description=(
            "BOT Chain 全功能链上服务 (ETH Wuhan 2026)。"
            "写接口默认 dry_run=true 返回预览; 真实发送需显式 dry_run=false。"
            "默认测试网 Chain ID 968。"
        ),
        openapi_tags=TAGS_METADATA,
        lifespan=lifespan,
    )
    install_error_handlers(app)
    app.add_middleware(TraceIdMiddleware)

    for module_router in (
        chain_info.router,
        accounts.router,
        transactions.router,
        tokens.router,
        contracts.router,
        erc4337.router,
        erc8004.router,
        bdex.router,
        faucet.router,
        indexer.router,
    ):
        app.include_router(module_router, prefix="/api/v1")

    @app.get("/", tags=["meta"])
    def root() -> dict[str, str]:
        return {
            "service": "bot_chain_api",
            "version": __version__,
            "docs": "/docs",
            "openapi": "/openapi.json",
        }

    @app.get("/playground", tags=["meta"], include_in_schema=False)
    def playground() -> RedirectResponse:
        """调试页：直接使用内置 Swagger UI（用户决策：不自研 playground）。"""
        return RedirectResponse(url="/docs")

    return app


app = create_app()
