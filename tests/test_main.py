"""应用层：healthz 与三段错误模型贯穿（trace_id）。"""

from fastapi.testclient import TestClient


def test_healthz(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "coincall-core"}


def test_error_responses_carry_trace_id(client: TestClient) -> None:
    resp = client.get("/manifests/svc_missing")
    assert resp.status_code == 404
    trace = resp.json()["trace_id"]
    assert isinstance(trace, str) and len(trace) > 0


def test_validation_error_uses_three_segment_model(client: TestClient) -> None:
    resp = client.post("/apikeys", json={"consumer_wallet": 42})
    assert resp.status_code == 422
    body = resp.json()
    for field in ("error", "detail", "code", "trace_id"):
        assert field in body
