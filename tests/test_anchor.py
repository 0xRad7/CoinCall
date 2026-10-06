"""unit（10 §1/§2 冻结契约·G2）：keeper 锚定任务三路 + 组装口径。

链路：每 COINCALL_ANCHOR_INTERVAL_S（默认 1800s）——
  GET {core}/internal/decision/anchor-pending
  → 对每条经 bot-chain-api POST /api/v1/contracts/send 调 ERC-8004 setMetadata
    （tokenId=provider agent_id；key="coincall:decision:v1"；value=digest|pointer UTF-8 bytes）
  → 成功回 POST {core}/internal/decision/anchor-result
  → 失败记日志下轮重试（幂等靠 core 的 anchor_records）。

三路：pending→提交→回执上报 / 提交失败重试（不上报）/ core 不可达降级不抛。
零网络：core 与 bot-chain-api 通道均经 respx（仅方法级装饰器，A2/C-06）。
"""

import asyncio
import json

import httpx
import pytest
import respx

from app.modules.anchor import (
    ANCHOR_KEY,
    AnchorChainError,
    AnchorTask,
    BotChainAnchorChain,
    DecisionCoreClient,
)

pytestmark = pytest.mark.unit

CORE = "http://core.test"
CHAIN = "http://chain.test"
REGISTRY = "0xec8fFbC3c9A34AdDCbB3A14F91db2bd26A8b99c0"
OPERATOR = "0xC37fFE97B4D2C3D0187B1dDEDF273E52A461B63a"

PENDING_BODY = {
    "pending": [
        {
            "anchor_id": "anc_0001",
            "token_id": 162,
            "digest": "sha256:" + "ab" * 32,
            "pointer": "http://127.0.0.1:8020/decision/explain/svc_translate",
        },
        {  # 无 pointer：value 退化为纯 digest
            "anchor_id": "anc_0002",
            "token_id": 137,
            "digest": "sha256:" + "cd" * 32,
        },
    ]
}


def _task() -> AnchorTask:
    http = httpx.AsyncClient(trust_env=False)
    core = DecisionCoreClient(base_url=CORE, http=http)
    chain = BotChainAnchorChain(
        base_url=CHAIN, registry_address=REGISTRY, operator_address=OPERATOR, http=http
    )
    return AnchorTask(core=core, chain=chain, interval_s=1800.0)


@respx.mock
async def test_anchor_happy_path_submits_and_reports() -> None:
    pending_route = respx.get(f"{CORE}/internal/decision/anchor-pending").respond(json=PENDING_BODY)
    send_route = respx.post(f"{CHAIN}/api/v1/contracts/send").respond(
        json={"tx_hash": "0xabc", "status": 1, "block_number": 10, "gas_used": 1}
    )
    result_route = respx.post(f"{CORE}/internal/decision/anchor-result").respond(json={"ok": True})

    task = _task()
    report = await task.run_cycle()

    assert pending_route.called and send_route.call_count == 2 and result_route.call_count == 2
    # 提交 payload：setMetadata(tokenId, key, digest|pointer UTF-8 bytes)
    first_send = json.loads(send_route.calls[0].request.content)
    assert first_send["from_address"] == OPERATOR
    assert first_send["to"].lower() == REGISTRY.lower()
    assert first_send["method"] == "setMetadata"
    assert first_send["args"][0] == 162
    assert first_send["args"][1] == ANCHOR_KEY
    expected_value = "sha256:" + "ab" * 32 + "|http://127.0.0.1:8020/decision/explain/svc_translate"
    assert first_send["args"][2] == "0x" + expected_value.encode().hex()
    second_send = json.loads(send_route.calls[1].request.content)
    assert second_send["args"][2] == "0x" + ("sha256:" + "cd" * 32).encode().hex()
    # 回执上报：anchor_id + tx_hash 指针
    first_result = json.loads(result_route.calls[0].request.content)
    assert first_result["anchor_id"] == "anc_0001"
    assert first_result["tx_hash"] == "0xabc"
    assert first_result["token_id"] == 162
    assert first_result["key"] == ANCHOR_KEY
    assert first_result["value"] == expected_value
    assert report == {
        "pending": 2,
        "submitted": 2,
        "reported": 2,
        "failed": 0,
        "report_failed": 0,
        "degraded": False,
    }
    assert task.metrics.anchored_count == 2
    assert task.metrics.last_error is None
    assert task.status_snapshot()["anchored_count"] == 2


@respx.mock
async def test_anchor_submit_failure_keeps_for_retry_without_report() -> None:
    respx.get(f"{CORE}/internal/decision/anchor-pending").respond(json=PENDING_BODY)
    respx.post(f"{CHAIN}/api/v1/contracts/send").respond(status_code=500, text="boom")
    result_route = respx.post(f"{CORE}/internal/decision/anchor-result")

    task = _task()
    report = await task.run_cycle()  # 不得抛：失败记日志，下轮重试

    assert report["submitted"] == 0 and report["failed"] == 2 and report["reported"] == 0
    assert not result_route.called
    assert task.metrics.last_error is not None
    assert task.metrics.anchored_count == 0


@respx.mock
async def test_anchor_receipt_status_failed_raises_for_retry() -> None:
    """/contracts/send 返回 status=0（上链 revert）→ 同失败路径，不回执。"""
    respx.get(f"{CORE}/internal/decision/anchor-pending").respond(json=PENDING_BODY)
    respx.post(f"{CHAIN}/api/v1/contracts/send").respond(
        json={"tx_hash": "0xdead", "status": 0, "block_number": 10, "gas_used": 1}
    )
    result_route = respx.post(f"{CORE}/internal/decision/anchor-result")

    task = _task()
    report = await task.run_cycle()

    assert report["failed"] == 2 and report["reported"] == 0
    assert not result_route.called


@respx.mock
async def test_anchor_core_unreachable_degrades_without_raise() -> None:
    respx.get(f"{CORE}/internal/decision/anchor-pending").mock(
        side_effect=httpx.ConnectError("core down")
    )
    send_route = respx.post(f"{CHAIN}/api/v1/contracts/send")

    task = _task()
    report = await task.run_cycle()  # 降级为告警，不阻塞

    assert report["degraded"] is True
    assert report["pending"] == 0
    assert not send_route.called
    assert task.metrics.last_error is not None


@respx.mock
async def test_anchor_result_report_failure_does_not_lose_anchor() -> None:
    """提交成功但回执上报失败：记告警（core 侧缺回执会下轮重发，setMetadata 幂等覆盖）。"""
    respx.get(f"{CORE}/internal/decision/anchor-pending").respond(json=PENDING_BODY)
    respx.post(f"{CHAIN}/api/v1/contracts/send").respond(
        json={"tx_hash": "0xabc", "status": 1, "block_number": 10, "gas_used": 1}
    )
    respx.post(f"{CORE}/internal/decision/anchor-result").respond(status_code=500)

    task = _task()
    report = await task.run_cycle()

    assert report["submitted"] == 2 and report["reported"] == 0 and report["report_failed"] == 2
    assert task.metrics.last_error is not None


@respx.mock
async def test_anchor_task_loop_start_stop() -> None:
    """常驻任务：start 后按 interval 周期执行；stop 收口（不泄漏 task）。"""
    route = respx.get(f"{CORE}/internal/decision/anchor-pending").respond(json={"pending": []})
    http = httpx.AsyncClient(trust_env=False)
    task = AnchorTask(
        core=DecisionCoreClient(base_url=CORE, http=http),
        chain=BotChainAnchorChain(
            base_url=CHAIN, registry_address=REGISTRY, operator_address=OPERATOR, http=http
        ),
        interval_s=0.01,
    )
    task.start()
    for _ in range(50):
        if route.call_count >= 2:
            break
        await asyncio.sleep(0.01)
    assert route.call_count >= 2
    snap = task.status_snapshot()
    assert snap["running"] is True and snap["interval_s"] == 0.01
    await task.stop()
    assert task.status_snapshot()["running"] is False
    await http.aclose()


async def test_anchor_chain_error_wraps_http_failure() -> None:
    """直连通道：bot-chain-api 非 2xx → AnchorChainError（行保持待重试语义）。"""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(503, text="unavailable")),
        trust_env=False,
    ) as http:
        chain = BotChainAnchorChain(
            base_url=CHAIN, registry_address=REGISTRY, operator_address=OPERATOR, http=http
        )
        with pytest.raises(AnchorChainError):
            await chain.submit_anchor(162, ANCHOR_KEY, "sha256:x")
