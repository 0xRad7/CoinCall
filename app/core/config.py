"""应用配置：环境变量唯一入口（pydantic-settings）。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """coincall-core 管理面配置。

    env 前缀 COINCALL_CORE_；.env 文件可选。
    """

    model_config = SettingsConfigDict(env_prefix="COINCALL_CORE_", env_file=".env", extra="ignore")

    port: int = 8020
    duckdb_path: str = "data/core.duckdb"
