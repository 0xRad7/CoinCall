"""T9：01 节 ServiceManifest 校验（定价/端点类型）+ manifests 目录 API。"""

import copy

import pytest
from fastapi.testclient import TestClient

from tests.conftest import VALID_MANIFEST

pytestmark = pytest.mark.unit


def _post(client: TestClient, manifest: dict[str, object]) -> object:
    return client.post("/manifests", json=manifest)


def test_publish_and_get_roundtrip(client: TestClient) -> None:
    resp = _post(client, VALID_MANIFEST)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["service_id"] == "svc_translate_v1"
    assert body["status"] == "active"
    assert body["manifest"]["chain"] == {"network": 968}
    assert body["manifest_hash"].startswith("sha256:")
    assert len(body["manifest_hash"]) == len("sha256:") + 64

    got = client.get("/manifests/svc_translate_v1")
    assert got.status_code == 200
    assert got.json()["manifest"]["pricing"]["amount_raw"] == "10000"
    assert got.json()["manifest_hash"] == body["manifest_hash"]


def test_catalog_lists_and_filters(client: TestClient) -> None:
    _post(client, VALID_MANIFEST)
    paused = copy.deepcopy(VALID_MANIFEST)
    paused["service_id"] = "svc_paused"
    paused["status"] = "paused"
    _post(client, paused)

    catalog = client.get("/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["count"] == 2
    assert {s["service_id"] for s in catalog.json()["services"]} == {
        "svc_translate_v1",
        "svc_paused",
    }
    assert "ETag" in catalog.headers

    only_active = client.get("/catalog", params={"status": "active"})
    assert only_active.json()["count"] == 1
    assert only_active.json()["services"][0]["service_id"] == "svc_translate_v1"


def test_update_manifest_keeps_service_id_changes_hash(client: TestClient) -> None:
    first = _post(client, VALID_MANIFEST).json()
    updated = copy.deepcopy(VALID_MANIFEST)
    updated["version"] = "1.1.0"
    updated["pricing"] = {"model": "per_call", "amount": "0.02", "amount_raw": "20000"}
    second = _post(client, updated).json()
    assert second["service_id"] == first["service_id"]
    assert second["manifest_hash"] != first["manifest_hash"]
    got = client.get("/manifests/svc_translate_v1").json()["manifest"]
    assert got["version"] == "1.1.0"
    assert got["pricing"]["amount_raw"] == "20000"


def test_get_unknown_service_404_three_segment(client: TestClient) -> None:
    resp = client.get("/manifests/svc_nope")
    assert resp.status_code == 404
    body = resp.json()
    for field in ("error", "detail", "code", "trace_id"):
        assert field in body


# ---- 定价校验（amount_raw 权威 + 一致性） ----


def test_reject_pricing_precision_mismatch(client: TestClient) -> None:
    bad = copy.deepcopy(VALID_MANIFEST)
    bad["pricing"] = {"model": "per_call", "amount": "0.01", "amount_raw": "999"}
    resp = _post(client, bad)
    assert resp.status_code == 422
    assert resp.json()["code"] == "manifest_invalid"


def test_reject_amount_raw_not_decimal_digits(client: TestClient) -> None:
    bad = copy.deepcopy(VALID_MANIFEST)
    bad["pricing"] = {"model": "per_call", "amount": "0.01", "amount_raw": "0x10"}
    assert _post(client, bad).status_code == 422


def test_reject_unknown_pricing_token(client: TestClient) -> None:
    bad = copy.deepcopy(VALID_MANIFEST)
    bad["pricing"] = {"model": "per_call", "amount": "1", "amount_raw": "1000000", "token": "XXX"}
    assert _post(client, bad).status_code == 422


def test_reject_non_per_call_pricing_model(client: TestClient) -> None:
    bad = copy.deepcopy(VALID_MANIFEST)
    bad["pricing"] = {"model": "monthly", "amount": "0.01", "amount_raw": "10000"}
    assert _post(client, bad).status_code == 422


# ---- 端点类型校验 ----


def test_reject_unknown_endpoint_type(client: TestClient) -> None:
    bad = copy.deepcopy(VALID_MANIFEST)
    bad["endpoint"] = {"type": "grpc", "url": "https://x.example"}
    assert _post(client, bad).status_code == 422


def test_reject_http_json_without_url(client: TestClient) -> None:
    bad = copy.deepcopy(VALID_MANIFEST)
    bad["endpoint"] = {"type": "http_json", "timeout_ms": 1000}
    assert _post(client, bad).status_code == 422


def test_internal_endpoint_url_optional(client: TestClient) -> None:
    ok = copy.deepcopy(VALID_MANIFEST)
    ok["endpoint"] = {"type": "internal", "handler": "echo"}
    resp = _post(client, ok)
    assert resp.status_code == 201


def test_reject_timeout_over_hard_cap(client: TestClient) -> None:
    bad = copy.deepcopy(VALID_MANIFEST)
    bad["endpoint"] = {
        "type": "http_json",
        "url": "https://team-a.example/translate",
        "timeout_ms": 60001,
    }
    assert _post(client, bad).status_code == 422


# ---- 其余结构校验 ----


def test_reject_bad_wallet_format(client: TestClient) -> None:
    bad = copy.deepcopy(VALID_MANIFEST)
    bad["provider"]["wallet"] = "0x123"
    assert _post(client, bad).status_code == 422


def test_reject_bad_service_id_slug(client: TestClient) -> None:
    bad = copy.deepcopy(VALID_MANIFEST)
    bad["service_id"] = "Svc 中文!"
    assert _post(client, bad).status_code == 422


def test_reject_schema_without_type_keyword(client: TestClient) -> None:
    bad = copy.deepcopy(VALID_MANIFEST)
    bad["input_schema"] = {"properties": {"text": {"type": "string"}}}
    assert _post(client, bad).status_code == 422


def test_reject_chain_other_than_968(client: TestClient) -> None:
    bad = copy.deepcopy(VALID_MANIFEST)
    bad["chain"] = {"network": 1}
    assert _post(client, bad).status_code == 422
