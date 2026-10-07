"""应用配置：环境变量唯一入口（pydantic-settings）。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """coincall-core 管理面配置。

    env 前缀 COINCALL_CORE_；.env 文件可选。
    """

    model_config = SettingsConfigDict(env_prefix="COINCALL_CORE_", env_file=".env", extra="ignore")

    port: int = 8020
    duckdb_path: str = "data/core.duckdb"
    # ---- P1-2/P1-3：链上事实源（经 coincall-bot-chain-api，唯一链通道）----
    bot_chain_api_base_url: str = "http://127.0.0.1:8010"
    #: PayVault 合约（coincall-contracts/deployments/testnet-968.json 的事实源）
    pay_vault_address: str = (
        "0xa6E82Fd6648F9Ea8f695c37Edf89f2E5FDb89ff0"  # epoch2 金库（公开合约地址）
    )
    #: PayVault 部署块（eth_getCode 实证：25795947 无代码/25795948 起 6696B）；首跑回补起点
    pay_vault_deploy_block: int = 25_870_488  # epoch2 部署块（事实源 contracts/deployments）
    #: ERC-8004 身份存在性校验结果短缓存秒数（含 not_found 负缓存）
    identity_cache_ttl: float = 60.0
    # ---- P1-3：排行榜双源----
    gateway_base_url: str = "http://127.0.0.1:8030"
    #: Charged 增量同步的单次窗口（rpc.bohr.life getLogs 上限，同 bot-chain-api 口径）
    charged_sync_window: int = 5000
    #: 水位回补时的 reorg 安全余量（块）
    charged_sync_safety: int = 64
    #: 两次增量同步的最小间隔（秒）——防排行榜请求打爆链通道
    leaderboard_min_sync_interval: float = 3.0
    # ---- 决策层（10 篇）----
    #: 网关收据公钥缓存 TTL（秒，10 §2：启动拉取+5min 缓存）
    receipt_pubkey_ttl: float = 300.0
    #: 反馈权窗口限频的窗口长度（小时，CONSTRAINTS §E 实施级简化口径）
    feedback_window_hours: int = 168
    # 前端控制台（coincall-console，Vite 5173）跨域白名单；逗号分隔，空=关闭
    # 上游凭证静态加密密钥（Fernet 派生；生产从 env 注入）
    credential_secret: str = "coincall-dev-credential-secret"  # noqa: S105
    # 平台代管账户（认领三态分类用；事实源=bot-chain-api 出资账户）
    platform_custodian_address: str = "0xc37ffe97b4d2c3d0187b1ddedf273e52a461b63a"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
