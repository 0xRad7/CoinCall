"""应用配置：环境变量唯一入口（env 前缀 COINCALL_）。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """coincall-gateway 数据面配置。"""

    model_config = SettingsConfigDict(env_prefix="COINCALL_", env_file=".env", extra="ignore")

    port: int = 8030
    duckdb_path: str = "data/gateway.duckdb"
    core_base_url: str = "http://127.0.0.1:8020"
    shadow_k: int = 3  # 02 §5c：同 key 在途笔数上限
    pay_vault_address: str = "0x000000000000000000000000000000000000dEaD"  # W4 接线真实地址
    chain_rpc_url: str = "https://rpc.bohr.life/"
    chain_id: int = 968
    # 计价 token 合约地址（BOT 968 USDT），非凭据：
    payment_token_address: str = "0x75edC9335175Fc0552D51D48439F229c10420fe3"  # noqa: S105
    topup_deposit_address: str = "0x000000000000000000000000000000000000dEaD"
    receipt_secret: str = "coincall-dev-receipt-secret"  # noqa: S105 —— dev 占位，生产从 env 注入
    manifest_cache_ttl: float = 60.0  # 01 §5：网关侧 manifest 内存缓存
    chain_cache_ttl: float = 30.0  # 09 P0-4：链上约束短缓存
