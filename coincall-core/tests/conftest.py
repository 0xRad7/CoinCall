"""CoinCall core 单测夹具：tmp_path DuckDB + 外部依赖全 fake（C-10/C-14，零网络）。"""

from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.modules.identity import IdentityInfo

#: 已知 fake 身份：agent_id → agent_wallet（与 VALID_MANIFEST.provider.wallet 同源）
KNOWN_IDENTITIES: dict[int, str] = {137: "0x1234567890AbCdEf1234567890aBcDeF12345678"}


class FakeIdentityClient:
    """IdentityClient 桩：内存身份表，记录查询（断言短缓存命中）。"""

    def __init__(self, known: dict[int, str] | None = None) -> None:
        self.known = known if known is not None else dict(KNOWN_IDENTITIES)
        self.calls: list[int] = []

    def get(self, agent_id: int, *, force: bool = False) -> IdentityInfo | None:
        self.calls.append(agent_id)
        wallet = self.known.get(agent_id)
        if wallet is None:
            return None
        return IdentityInfo(
            token_id=agent_id,
            owner="0x0000000000000000000000000000000000000001",
            agent_wallet=wallet,
            token_uri="",
        )


class FakeChainClient:
    """ChainSource 桩（leaderboard 用）：可编程 tip 与分窗日志。"""

    def __init__(
        self,
        tip: int = 25_800_000,
        logs_by_block: dict[int, list[dict[str, Any]]] | None = None,
        fail_tip: bool = False,
    ) -> None:
        self.tip = tip
        self.logs_by_block = logs_by_block or {}
        self.fail_tip = fail_tip
        self.pulled_windows: list[tuple[int, int]] = []

    def tip_block(self) -> int:
        if self.fail_tip:
            raise RuntimeError("chain unreachable (fake)")
        return self.tip

    def charged_logs(self, from_block: int, to_block: int) -> list[dict[str, Any]]:
        self.pulled_windows.append((from_block, to_block))
        return [
            dict(log, block_number=block)
            for block in sorted(self.logs_by_block)
            if from_block <= block <= to_block
            for log in self.logs_by_block[block]
        ]


class FakeGatewayStatsClient:
    """GatewayStatsClient 桩：可编程 stats 或故障；记录 window_hours 请求口径。"""

    def __init__(self, stats: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.stats = stats or {"services": [], "totals": {}}
        self.error = error
        self.calls = 0
        self.requested_windows: list[int | None] = []

    def stats_view(self, window_hours: int | None = None) -> dict[str, Any]:
        self.calls += 1
        self.requested_windows.append(window_hours)
        if self.error is not None:
            raise self.error
        return self.stats


class FakeReceiptPubkeyClient:
    """ReceiptPubkeySource 桩：测试自持 Ed25519 密钥对（可签可验）；unavailable=True 模拟降级。"""

    def __init__(self, *, unavailable: bool = False) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.unavailable = unavailable
        self.calls = 0

    def public_key_hex(self) -> str | None:
        self.calls += 1
        if self.unavailable:
            return None
        if isinstance(self.key, Ed25519PrivateKey):
            pub: Ed25519PublicKey = self.key.public_key()
            return pub.public_bytes_raw().hex()
        return None


def sign_fake_receipt(
    key: Ed25519PrivateKey,
    *,
    receipt_id: str,
    service_id: str,
    amount_raw: str,
    status: str,
    ts: str,
) -> str:
    """按冻结契约规范串 receipt_id|service_id|amount_raw|status|ts 做 Ed25519 签名（hex）。

    此处独立拼串（不 import 实现）——串形态漂移时测试侧即失配，契约双写自检。
    """
    message = f"{receipt_id}|{service_id}|{amount_raw}|{status}|{ts}"
    return key.sign(message.encode()).hex()


def make_charged_log(
    *,
    provider: str,
    value_raw: int,
    payer: str = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94",
    nonce: str = "0x" + "00" * 31 + "01",
    tx_hash: str = "0x" + "ab" * 32,
    log_index: int = 0,
) -> dict[str, Any]:
    """构造 8010 GET /contracts/logs 返回的原始 Charged log 形态。"""
    return {
        "address": "0xfe91f55c0e7ccbc4a6619c67544ab453cf79c471",
        "topics": [
            "0x7cbb811de7ebfc8f2d6195f3af05b0b12fc3e9c8b48a2dbf3ff1c4ef81291dbf",
            "0x" + "00" * 12 + provider.lower().removeprefix("0x"),
            "0x" + "00" * 12 + payer.lower().removeprefix("0x"),
            nonce,
        ],
        "data": "0x" + format(value_raw, "064x"),
        "block_number": 0,  # FakeChainClient 按块注入时覆写
        "transaction_hash": tx_hash,
        "log_index": log_index,
    }


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    """全隔离应用实例：DuckDB 落 tmp_path，链/身份/网关/收据公钥客户端全 fake（零网络）。"""
    settings = Settings(duckdb_path=str(tmp_path / "core.duckdb"))
    app = create_app(
        settings,
        identity_client=FakeIdentityClient(),
        chain_client=FakeChainClient(),
        gateway_client=FakeGatewayStatsClient(),
        receipt_key_client=FakeReceiptPubkeyClient(),
    )
    with TestClient(app) as test_client:
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
