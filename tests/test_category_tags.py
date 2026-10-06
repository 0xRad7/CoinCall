"""决策层契约 v1.2：manifest.category/tags 受控词表 + 目录/榜单行透出（10 §0.5）。

向后兼容：旧 manifest（无 category/tags）发布读取均缺省 other/[]。
"""

import copy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.modules.manifest import CATEGORIES
from tests.conftest import (
    VALID_MANIFEST,
    FakeChainClient,
    FakeGatewayStatsClient,
    FakeIdentityClient,
)

pytestmark = pytest.mark.unit


def test_category_vocabulary_frozen() -> None:
    """受控词表冻结（10 §0.5）：六个类目，顺序即展示顺序。"""
    assert CATEGORIES == [
        "translation",
        "data-feed",
        "on-chain-query",
        "analysis",
        "agent-tool",
        "other",
    ]


def test_publish_with_category_and_tags(client: TestClient) -> None:
    manifest = copy.deepcopy(VALID_MANIFEST)
    manifest["category"] = "translation"
    manifest["tags"] = ["zh-en", "markdown", "fast"]
    resp = client.post("/manifests", json=manifest)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["manifest"]["category"] == "translation"
    assert body["manifest"]["tags"] == ["zh-en", "markdown", "fast"]

    got = client.get("/manifests/svc_translate_v1").json()
    assert got["manifest"]["category"] == "translation"

    catalog = client.get("/catalog").json()
    row = catalog["services"][0]
    assert row["manifest"]["category"] == "translation"
    assert row["manifest"]["tags"] == ["zh-en", "markdown", "fast"]


def test_default_category_other_and_empty_tags(client: TestClient) -> None:
    """向后兼容：不带 category/tags 的旧 manifest 照常发布，缺省 other/[]。"""
    resp = client.post("/manifests", json=VALID_MANIFEST)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["manifest"]["category"] == "other"
    assert body["manifest"]["tags"] == []


def test_old_stored_manifest_served_with_defaults(client: TestClient) -> None:
    """发布兼容旧数据：库中旧行（缺 category/tags）读出时补缺省 other/[]（目录行透出统一形态）。"""
    store: Any = client.app.state.store
    legacy = copy.deepcopy(VALID_MANIFEST)
    store.upsert_service(
        "svc_legacy_v1",
        legacy,  # 未经 v1.2 校验的旧行：无 category/tags
        "active",
        "sha256:" + "0" * 64,
    )
    row = client.get("/manifests/svc_legacy_v1").json()
    assert row["manifest"]["category"] == "other"
    assert row["manifest"]["tags"] == []
    catalog = client.get("/catalog").json()
    legacy_row = next(r for r in catalog["services"] if r["service_id"] == "svc_legacy_v1")
    assert legacy_row["manifest"]["category"] == "other"


@pytest.mark.parametrize(
    "category",
    ["Translation", "data_feed", "random", "", "onchain"],
)
def test_invalid_category_rejected(client: TestClient, category: str) -> None:
    manifest = copy.deepcopy(VALID_MANIFEST)
    manifest["category"] = category
    resp = client.post("/manifests", json=manifest)
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "request_invalid"


@pytest.mark.parametrize(
    "tags",
    [
        ["a", "b", "c", "d", "e", "f"],  # >5
        [""],  # 空串
        ["x" * 33],  # 超长
        ["good", ""],  # 混入空串
    ],
)
def test_invalid_tags_rejected(client: TestClient, tags: list[str]) -> None:
    manifest = copy.deepcopy(VALID_MANIFEST)
    manifest["tags"] = tags
    resp = client.post("/manifests", json=manifest)
    assert resp.status_code == 422, resp.text


def test_tags_max_five_valid(client: TestClient) -> None:
    manifest = copy.deepcopy(VALID_MANIFEST)
    manifest["tags"] = ["a", "b", "c", "d", "e"]
    assert client.post("/manifests", json=manifest).status_code == 201


def _leaderboard_client(tmp_path: Path) -> TestClient:
    settings = Settings(duckdb_path=str(tmp_path / "lb.duckdb"))
    app = create_app(
        settings,
        identity_client=FakeIdentityClient(),
        chain_client=FakeChainClient(),
        gateway_client=FakeGatewayStatsClient(),
    )
    return TestClient(app)


def test_leaderboard_rows_expose_category_tags(tmp_path: Path) -> None:
    """榜单行透出（10 §0.5）：ServiceStatsRow 携带 category/tags，旧数据缺省。"""
    with _leaderboard_client(tmp_path) as client:
        tagged = copy.deepcopy(VALID_MANIFEST)
        tagged["category"] = "data-feed"
        tagged["tags"] = ["weather"]
        assert client.post("/manifests", json=tagged).status_code == 201
        legacy = copy.deepcopy(VALID_MANIFEST)
        legacy["service_id"] = "svc_legacy_v1"
        assert client.post("/manifests", json=legacy).status_code == 201

        rows = client.get("/leaderboard/services").json()["services"]
        by_id = {r["service_id"]: r for r in rows}
        assert by_id["svc_translate_v1"]["category"] == "data-feed"
        assert by_id["svc_translate_v1"]["tags"] == ["weather"]
        assert by_id["svc_legacy_v1"]["category"] == "other"
        assert by_id["svc_legacy_v1"]["tags"] == []
        store_rows = client.get("/store").json()["services"]
        assert {r["category"] for r in store_rows} == {"data-feed", "other"}
