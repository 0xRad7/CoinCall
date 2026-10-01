"""环境变量 → Settings → ChainSpec（01 篇 §五 ChainConfig 落地）。

主网双重锁（铁律 A1）：BOT_CHAIN_NETWORK=mainnet 且 BOT_CHAIN_ALLOW_MAINNET=1 才放行。
出资私钥（铁律 A4）：只从环境变量读取；主网模式下拒绝加载（仅允许测试网私钥）。
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.chains import CHAINS, ChainSpec
from app.core.errors import ServiceError

NetworkName = Literal["testnet", "mainnet"]
PRIVATE_KEY_HEX_LEN = 66  # "0x" + 64 位 hex


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_chain_network: NetworkName = "testnet"
    bot_chain_allow_mainnet: bool = False
    bot_chain_test_private_key: SecretStr | None = None
    bot_chain_keystore_secret: SecretStr | None = None
    redis_url: str | None = None
    duckdb_path: str = "data/botchain.duckdb"
    bot_chain_api_key: SecretStr | None = None
    proxy: str | None = None
    indexer_autostart: bool = False
    log_level: str = "INFO"

    @property
    def chain_spec(self) -> ChainSpec:
        if self.bot_chain_network == "mainnet" and not self.bot_chain_allow_mainnet:
            msg = (
                "主网默认禁用（铁律 A1）：需 BOT_CHAIN_NETWORK=mainnet 且 BOT_CHAIN_ALLOW_MAINNET=1"
            )
            raise ServiceError(msg, code="mainnet_locked")
        return CHAINS[self.bot_chain_network]

    @property
    def funded_key(self) -> str | None:
        """测试网出资私钥（明文仅在本进程内流转，禁止日志/响应回显）。"""
        if self.bot_chain_network != "testnet":
            return None  # 铁律 A4：出资私钥仅允许测试网
        raw = self.bot_chain_test_private_key
        if raw is None:
            return None
        key = raw.get_secret_value().strip()
        valid = key.startswith("0x") and len(key) == PRIVATE_KEY_HEX_LEN
        if not valid:
            msg = "BOT_CHAIN_TEST_PRIVATE_KEY 格式非法（应为 0x + 64 位 hex）"
            raise ServiceError(msg, code="bad_private_key")
        try:
            int(key[2:], 16)
        except ValueError as exc:
            bad = "BOT_CHAIN_TEST_PRIVATE_KEY 非十六进制"
            raise ServiceError(bad, code="bad_private_key") from exc
        return key


@lru_cache
def get_settings() -> Settings:
    return Settings()
