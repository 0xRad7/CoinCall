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
