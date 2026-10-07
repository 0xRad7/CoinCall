"""应用配置：环境变量唯一入口（env 前缀 COINCALL_）。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """coincall-gateway 数据面配置。"""

    model_config = SettingsConfigDict(env_prefix="COINCALL_", env_file=".env", extra="ignore")

    port: int = 8030
    duckdb_path: str = "data/gateway.duckdb"
    core_base_url: str = "http://127.0.0.1:8020"
    shadow_k: int = 3  # 02 §5c：同 key 在途笔数上限
    # 服务端咽喉卡口（agent-wallet-trust T4 缓解）：按消费者钱包的日累计扣款上限。
    # 绕过 SDK 直打网关也绕不过——一切扣款必经网关验签进 settle_queue。
    # 默认 50 USDT（50_000_000 raw）：演示量级宽松、灾难量级封顶；0=关闭。
    wallet_daily_cap_raw: int = 50_000_000
    # 接线事实源：coincall-contracts/deployments/testnet-968.json（tag d0-contracts-r1）
    pay_vault_address: str = "0xa6E82Fd6648F9Ea8f695c37Edf89f2E5FDb89ff0"
    chain_rpc_url: str = "https://rpc.bohr.life/"
    chain_id: int = 968
    # 计价 token = 测试网真 USDT（epoch2，PayVault 部署时锁定；事实源 contracts deployments）
    payment_token_address: str = "0x75edC9335175Fc0552D51D48439F229c10420fe3"  # noqa: S105 —— 公开合约地址，非凭据
    receipt_secret: str = "coincall-dev-receipt-secret"  # noqa: S105 —— dev 占位，生产从 env 注入
    #: Ed25519 收据密钥种子（32 字节 hex；10 §2）。缺省随机生成（重启轮换，日志提示）
    receipt_seed: str | None = None
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
    # ---- 决策摘要锚定任务（10 §1/§2；只在 keeper_enabled 时随 keeper 启动）----
    #: 锚定周期（秒）：每轮拉 core anchor-pending → setMetadata 上链 → 回执上报
    anchor_interval_s: float = 1800.0
    #: IdentityRegistry（ERC-8004，锚定目标合约）——事实源 bot-chain-api chains.py testnet 968
    identity_registry_address: str = "0xec8fFbC3c9A34AdDCbB3A14F91db2bd26A8b99c0"
