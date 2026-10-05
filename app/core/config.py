"""应用配置：环境变量唯一入口（env 前缀 COINCALL_）。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """coincall-gateway 数据面配置。"""

    model_config = SettingsConfigDict(env_prefix="COINCALL_", env_file=".env", extra="ignore")

    port: int = 8030
    duckdb_path: str = "data/gateway.duckdb"
    core_base_url: str = "http://127.0.0.1:8020"
    shadow_k: int = 3  # 02 §5c：同 key 在途笔数上限
    # 接线事实源：coincall-contracts/deployments/testnet-968.json（tag d0-contracts-r1）
    pay_vault_address: str = "0xFe91F55C0e7Ccbc4A6619C67544Ab453cf79C471"
    chain_rpc_url: str = "https://rpc.bohr.life/"
    chain_id: int = 968
    # 计价 token = MockUSDT（PayVault 部署时锁定的 token；换真 USDT 需随合约重部署）
    payment_token_address: str = "0x4F8f2eaAA3988E9f59B72C93262DDC1084E540fb"  # noqa: S105 —— 公开合约地址，非凭据
    receipt_secret: str = "coincall-dev-receipt-secret"  # noqa: S105 —— dev 占位，生产从 env 注入
    manifest_cache_ttl: float = 60.0  # 01 §5：网关侧 manifest 内存缓存
    chain_cache_ttl: float = 30.0  # 09 P0-4：链上约束短缓存
    # 前端控制台（coincall-console，Vite 5173）跨域白名单；逗号分隔，空=关闭
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # ---- keeper 结算器（04 §3 / 09 P0-5）----
    keeper_enabled: bool = False  # 单测默认关（A2 零网络）；生产/演示 env 置 true
    keeper_batch_size: int = 3  # 攒批笔数阈值（演示故意取小，快速上屏）
    keeper_flush_interval: float = 30.0  # 攒批时间阈值（秒）
    keeper_operator_address: str = "0xC37fFE97B4D2C3D0187B1dDEDF273E52A461B63a"
    bot_chain_api_base_url: str = "http://127.0.0.1:8010"
    #: agent_id → agentWallet 静态覆盖（缺省走 core manifest 解析）
    keeper_provider_wallet_overrides: dict[str, str] = {}
