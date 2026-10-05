"""CoinCall core 单测夹具：每个用例独立 tmp_path DuckDB（C-14 单写者纪律）。"""

from pathlib import Path

import pytest
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """全隔离应用实例：DuckDB 落 tmp_path，零网络。"""
    settings = Settings(duckdb_path=str(tmp_path / "core.duckdb"))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


VALID_MANIFEST: dict[str, object] = {
    "service_id": "svc_translate_v1",
    "name": "中英技术文档翻译",
    "description": "面向技术文档的中英互译，保留 Markdown 结构",
    "version": "1.0.0",
    "provider": {
        "agent_id": 137,
        "wallet": "0x1234567890AbCdEf1234567890aBcDeF12345678",
        "display_name": "Team booth-demo",
    },
    "endpoint": {
        "type": "http_json",
        "url": "https://team-a.example/translate",
        "timeout_ms": 30000,
    },
    "pricing": {"model": "per_call", "amount": "0.01", "amount_raw": "10000", "token": "USDT"},
    "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
    "output_schema": {"type": "object", "properties": {"result": {"type": "string"}}},
}
