"""coincall-gateway 应用工厂：数据面（端口 8030）。

依赖注入约定：create_app 的可选参数即单测替身入口
（chain_adapter / auth_client / manifest_client / providers），
生产路径在 lifespan 装配真实实现（httpx → core、web3 → rpc.bohr.life）。
"""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.core.chain import BotChainAdapter
from app.core.config import Settings
from app.core.errors import (
    ApiError,
    PaymentRequiredError,
    api_error_handler,
    payment_required_handler,
    trace_middleware,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.schemes import ChainAdapter, PaymentScheme, PayVaultScheme, register_scheme
from app.core.shadow_gate import ShadowGate
from app.modules.auth import CoreAuthClient
from app.modules.call_route import router as call_router
from app.modules.calls import CallStore
from app.modules.internal_services import InternalServicesProvider
from app.modules.keeper import BotChainSettleChain, Keeper, provider_wallet_resolver
from app.modules.keeper_route import router as keeper_router
from app.modules.manifest_client import ManifestClient
from app.modules.providers import HttpJsonProvider, ProviderAdapter
from app.modules.stats_route import router as stats_router


def create_app(
    settings: Settings | None = None,
    *,
    chain_adapter: ChainAdapter | None = None,
    auth_client: CoreAuthClient | None = None,
    manifest_client: ManifestClient | None = None,
    providers: Mapping[str, ProviderAdapter] | None = None,
    keeper: Keeper | None = None,
) -> FastAPI:
    app_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = app_settings
        app.state.store = CallStore(app_settings.duckdb_path)  # C-14：单进程单写者
        # trust_env=False：core(8020)/keeper 通道都是直连本机地址，C-07——系统代理劫持 502
        app.state.http = httpx.AsyncClient(timeout=10.0, trust_env=False)
        app.state.auth = auth_client or CoreAuthClient(
            base_url=app_settings.core_base_url, http=app.state.http
        )
        app.state.manifests = manifest_client or ManifestClient(
            base_url=app_settings.core_base_url,
            http=app.state.http,
            cache_ttl_seconds=app_settings.manifest_cache_ttl,
        )
        chain = chain_adapter or BotChainAdapter(
            app_settings.chain_rpc_url, cache_ttl=app_settings.chain_cache_ttl
        )
        app.state.chain = chain
        scheme: PaymentScheme = PayVaultScheme(
            chain=chain,
            store=app.state.store,
            token_address=app_settings.payment_token_address,
            verifying_contract=app_settings.pay_vault_address,
            chain_id=app_settings.chain_id,
        )
        app.state.scheme = scheme
        register_scheme(scheme)
        app.state.shadow_gate = ShadowGate(k=app_settings.shadow_k)
        # internal 适配器：internal://<name> → demo handler（09 P1-4）；未知名/无 url → 回显兜底
        default_providers: dict[str, ProviderAdapter] = {
            "internal": InternalServicesProvider(
                http=app.state.http,
                bot_chain_base_url=app_settings.bot_chain_api_base_url,
                core_base_url=app_settings.core_base_url,
                pay_vault=app_settings.pay_vault_address,
            ),
            "http_json": HttpJsonProvider(app.state.http),
        }
        app.state.providers = dict(providers) if providers else default_providers
        idempotency: dict[tuple[str, str], dict[str, object]] = {}
        app.state.idempotency = idempotency
        # keeper：常驻结算任务（04 §3）；注入实例优先，否则按 keeper_enabled 装配真实通道
        built_chain: BotChainSettleChain | None = None
        if keeper is not None:
            app.state.keeper = keeper
        else:
            if app_settings.keeper_enabled:
                built_chain = BotChainSettleChain(
                    base_url=app_settings.bot_chain_api_base_url,
                    rpc_url=app_settings.chain_rpc_url,
                    pay_vault=app_settings.pay_vault_address,
                    operator_address=app_settings.keeper_operator_address,
                )
            app.state.keeper = Keeper(
                store=app.state.store,
                chain=built_chain,
                wallet_for=(
                    provider_wallet_resolver(
                        app_settings.keeper_provider_wallet_overrides,
                        app.state.store,
                        app.state.manifests,
                    )
                    if built_chain is not None
                    else None
                ),
                batch_size=app_settings.keeper_batch_size,
                flush_interval=app_settings.keeper_flush_interval,
            )
        if app_settings.keeper_enabled:
            app.state.keeper.start()
        yield
        await app.state.keeper.stop()
        if built_chain is not None:
            await built_chain.aclose()
        await app.state.http.aclose()
        app.state.store.close()

    app = FastAPI(
        title="coincall-gateway",
        description="CoinCall 数据面：402 付费网关（X-PAYMENT 验签 + 影子闸门 + settle 队列）",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.middleware("http")(trace_middleware)
    app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type] # 注册处签名按异常子类收窄
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(PaymentRequiredError, payment_required_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "coincall-gateway"}

    app.include_router(call_router)
    app.include_router(keeper_router)
    app.include_router(stats_router)
    return app


app = create_app()
