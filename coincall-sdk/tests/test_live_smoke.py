"""live/needs_funds 冒烟（打真实服务与测试网 968；A7：只用公开 anvil 测试账户）。

- live：core(8020) 目录只读冒烟。
- needs_funds：anvil #1 钱包 → mint+approve（本地直签 raw tx，BOT 付 gas）→
  core 签 key → 对 svc_rad_ai 真实付费调用 200 → 等 keeper 结算（≤90s）→ 断言 Charged。

8020/8030 连接拒绝（其他任务在重启服务）按纪律等 5s 重试。
"""

import time
import uuid

import httpx
import pytest

from coincall.client import DEFAULT_CORE_URL, DEFAULT_GATEWAY_URL, Client
from coincall.wallet import LocalWallet

CORE_URL = "http://127.0.0.1:8020"
GATEWAY_URL = "http://127.0.0.1:8030"
KEEPER_STATUS_URL = f"{GATEWAY_URL}/internal/keeper/status"
SETTLE_WAIT_S = 90
RETRY_INTERVAL_S = 5

# anvil 账户 #1（公开助记词派生，测试网无价值）；测试网持有 BOT 付 gas
ANVIL1_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"

# httpx 一律 trust_env=False（C-07）；连接拒绝（服务重启中）等 5s 重试
_HTTP = httpx.Client(trust_env=False, timeout=30)


def _request_with_retry(method: str, url: str, **kwargs: object) -> httpx.Response:
    last_exc: Exception | None = None
    for _ in range(12):  # 12 × 5s = 最多等 60s（另一任务在重启服务属常态）
        try:
            return getattr(_HTTP, method)(url, **kwargs)  # type: ignore[arg-type]
        except httpx.ConnectError as exc:  # 另一任务在重启服务
            last_exc = exc
            time.sleep(RETRY_INTERVAL_S)
    raise AssertionError(f"{url} 连续连接拒绝: {last_exc}")


@pytest.mark.live
def test_catalog_live() -> None:
    """只读：真实 core 目录可被 SDK catalog() 消费（服务重启中则等待回归）。"""
    resp = _request_with_retry("get", f"{CORE_URL}/catalog")
    assert resp.status_code == 200, resp.text
    catalog = resp.json()
    assert catalog["count"] >= 1
    assert any(svc["service_id"] == "svc_rad_ai" for svc in catalog["services"])


@pytest.mark.needs_funds
def test_paid_call_e2e_settled_by_keeper() -> None:
    """全链路：mint+approve（本地直签）→ 签 key → 付费调用 200 → keeper 链上结算。"""
    # 先确认两个服务在跑（重启中则等待回归），再进入不可重试的链上段
    assert _request_with_retry("get", f"{CORE_URL}/catalog").status_code == 200
    keeper0 = _request_with_retry("get", KEEPER_STATUS_URL).json()
    assert keeper0["running"] is True

    wallet = LocalWallet.from_key(ANVIL1_KEY)
    assert wallet.chain_id == 968

    # ① 资金准备：授权不足本地直签补齐；余额不足 skip（epoch2 真 USDT 无公开 mint，需人工转入）
    bal = wallet.balance()
    if bal.usdt_balance_raw < 2_000_000:
        pytest.skip("付费钱包测试网 USDT 不足——先向该地址转入 USDT（0x75ed…）再跑 live 冒烟")
    if bal.vault_allowance_raw < 2_000_000:
        approved = wallet.approve_vault("10")
        assert approved["status"] == 1, approved
    ready = wallet.balance()
    assert ready.available_raw >= 10_000  # 至少够一笔 svc_rad_ai（0.01 USDT）

    # ② core 签发 api key（绑定消费者钱包；明文只回一次）
    resp = _request_with_retry(
        "post", f"{CORE_URL}/apikeys", json={"consumer_wallet": wallet.address}
    )
    assert resp.status_code == 201, resp.text
    api_key = resp.json()["api_key"]

    # ③ 真实付费调用（预算 0.1 USDT 生效中；唯一文本避开幂等重放）
    client = Client(
        api_key=api_key,
        wallet=wallet,
        gateway_url=GATEWAY_URL,
        core_url=CORE_URL,
        budget_raw=100_000,
        http=_HTTP,
    )
    marker = f"p1-1-live-{uuid.uuid4().hex[:8]}"
    result = client.call("svc_rad_ai", {"text": marker})
    assert result.status_code == 200
    assert result.receipt_id and result.receipt_id.startswith("rcp_")
    assert result.charged_raw == "10000"
    assert client.spent_raw == 10_000
    body_text = str(result.body)
    assert marker in body_text  # internal 回显服务把原文带回

    # ④ 等 keeper 结算（≤90s；cumulative Charged 增量 ≥ 本笔）
    keeper_before = _request_with_retry("get", KEEPER_STATUS_URL).json()
    charged_before = int(keeper_before["cumulative_charged_raw"])
    deadline = time.monotonic() + SETTLE_WAIT_S
    settled = None
    while time.monotonic() < deadline:
        status = _request_with_retry("get", KEEPER_STATUS_URL).json()
        if int(status["cumulative_charged_raw"]) >= charged_before + 10_000:
            settled = status
            break
        time.sleep(3)
    assert settled is not None, f"{SETTLE_WAIT_S}s 内未见 keeper 结算增量"
    assert settled["last_batch"]["tx_hash"].startswith("0x")
    assert settled["queue"]["pending"] == 0

    # ⑤ 结算后钱包余额至少减少本笔 0.01 USDT
    # （公开测试钱包可能被并行任务同时消费，用单调下界而非严格等式）
    after = wallet.balance()
    assert after.usdt_balance_raw <= ready.usdt_balance_raw - 10_000


@pytest.mark.unit
def test_default_urls_match_running_services() -> None:
    """默认 URL 即赛时本机拓扑（8030 网关 / 8020 core）——README 示例的隐含前提。"""
    assert DEFAULT_CORE_URL == "http://127.0.0.1:8020"
    assert DEFAULT_GATEWAY_URL == "http://127.0.0.1:8030"
    assert CORE_URL == DEFAULT_CORE_URL and GATEWAY_URL == DEFAULT_GATEWAY_URL
