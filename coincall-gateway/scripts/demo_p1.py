"""P1-4 三幕演示剧本（07 §1）：无人工干预彩排 + 录档 results/demo_p1.md（T24）。

第一幕 挂服务（30 秒口径）：登记 provider(162) → 发布 3 个 manifest → catalog 可见
第二幕 付费调用：SDK 钱包导入 anvil#1 → 真实付费调用 svc_translate / svc_contract_scan /
       svc_chain_report → 402 指引路径演示（未 approve 新 key → guidance → approve → 成功）
第三幕 结算与信誉：keeper 攒批上链（≤90s/批）→ Charged 明细+交易哈希 → providerWithdraw
       （anvil#2 直签 raw tx，chainId 断言先于签名）→ MockUSDT 到账增量==提取额 → 排行榜/proof
兜底演练：svc_translate 切友队 http_json 端点 → 掉线（502 零扣款）→ 切备用端点 → 切回
       internal 兜底；切换传播时延 = 网关 manifest 缓存 TTL（60s）。

前置：8010/8020 在跑；8030 以 COINCALL_KEEPER_ENABLED=true 在跑（README 演示快速启动）。
用法：uv run python scripts/demo_p1.py
诚实边界：overview 的数字就是全部真实发生过的调用（07 §5）；anvil 私钥为公开测试密钥，
日志永不打印任何私钥。
"""

from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
from eth_account import Account
from eth_utils import keccak
from web3 import Web3

# 演示消费端必须用刚交付的 SDK（coincall-sdk 只读；不装入本仓 venv，sys.path 引入）
GATEWAY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GATEWAY_ROOT))
sys.path.insert(0, str(GATEWAY_ROOT.parent / "coincall-sdk"))

from coincall import Client  # noqa: E402
from coincall.errors import GatewayError, PaymentRequiredError  # noqa: E402
from coincall.wallet import LocalWallet  # noqa: E402

from app.modules.internal_services import decode_charged_log  # noqa: E402

BOTCHAIN = "http://127.0.0.1:8010"
CORE = "http://127.0.0.1:8020"
GATEWAY = "http://127.0.0.1:8030"
RPC = "https://rpc.bohr.life/"
CHAIN_ID = 968
PAY_VAULT = "0xFe91F55C0e7Ccbc4A6619C67544Ab453cf79C471"
MOCK_USDT = "0x4F8f2eaAA3988E9f59B72C93262DDC1084E540fb"
EXPLORER_TX = "https://scan.bohr.life/tx/"
AGENT_ID = 162
DISPLAY_NAME = "CoinCall Demo Provider (booth)"

# anvil 公开助记词派生的测试账户（无资金价值；日志只输出地址，永不打印私钥）。
# 地址以密钥本地派生为准：#1=0x7099…dc79C8；#2 派生 0xB4AD…79F（P1-4 演示收款钱包）
ANVIL1_PK = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
ANVIL2_PK = "0x8b3a350cf5c3c96a137834cda2a3c9d6f0f6cd8e21ee7ab1d1a1f04e1e0b1c0e"

GAS_GWEI = 20
TRANSFER_GAS = 40_000  # legacy 转账余量（实测 gasUsed=21000；53000 是漏 to 的合约创建 intrinsic）
MANIFEST_CACHE_TTL_S = 60.0  # 网关 manifest 缓存（01 §5）：切换传播时延上限
SWITCH_PROPAGATION_S = MANIFEST_CACHE_TTL_S + 6.0
KEEPER_WAIT_S = 90.0
CHAIN_CACHE_TTL_S = 30.0  # 网关链上约束短缓存（09 P0-4）：approve/mint 后的可见性时延
TEAM_PORT, BACKUP_PORT = 18031, 18032
DRILL_TEXT = "failover drill"

SERVICES: dict[str, dict[str, Any]] = {
    "svc_translate": {
        "name": "技术翻译（internal 兜底）",
        "price": "0.01",
        "price_raw": "10000",
        "endpoint": {"type": "internal", "url": "internal://translate"},
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    "svc_contract_scan": {
        "name": "PayVault Charged 合约快查",
        "price": "0.02",
        "price_raw": "20000",
        "endpoint": {"type": "internal", "url": "internal://contract_scan"},
        "input_schema": {
            "type": "object",
            "properties": {"window_blocks": {"type": "integer", "minimum": 1, "maximum": 5000}},
        },
    },
    "svc_chain_report": {
        "name": "链上数据报告（平台自举第一付费端点）",
        "price": "0.05",
        "price_raw": "50000",
        "endpoint": {"type": "internal", "url": "internal://chain_report"},
        "input_schema": {"type": "object"},
    },
}
#: 第二幕 5 笔付费调用（fresh 的 translate + #1 的三个服务各一笔）
DEMO_SPEND_RAW = 10000 + sum(int(s["price_raw"]) for s in SERVICES.values())


class Recorder:
    """双写记录器：stdout + results/demo_p1.md（逐段 flush，失败也保留现场）。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(exist_ok=True)
        self.lines = [
            "# P1-4 三幕演示彩排实录（T24 绑定）",
            "",
            f"- 彩排时间：{datetime.now(UTC).isoformat()}；链：BOT Chain 测试网 968",
            f"- PayVault `{PAY_VAULT}` / MockUSDT `{MOCK_USDT}`（6 位精度）",
            "- 剧本：coincall-docs/07；脚本：scripts/demo_p1.py；consumer=anvil#1，"
            "provider 收款钱包=anvil#2（公开测试账户，私钥不上日志）",
            "",
        ]

    def log(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] {msg}")
        self.lines.append(f"- `{stamp}` {msg}")
        self.flush()

    def block(self, title: str, text: str) -> None:
        print(f"--- {title} ---")
        print(text)
        self.lines += [f"**{title}**", "", "```", text.rstrip(), "```", ""]
        self.flush()

    def flush(self) -> None:
        self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


class DemoError(AssertionError):
    """彩排断言失败（剧本走不下去即失败，无兜底掩饰）。"""


def check(cond: Any, msg: str) -> None:
    if not cond:
        raise DemoError(msg)


def api_get(
    url: str, *, tries: int = 6, delay: float = 5.0, timeout: float = 15.0
) -> dict[str, Any]:
    """GET + 5s×N 重试（演示对服务瞬断的容错；trust_env=False 防 C-07 代理劫持）。"""
    with httpx.Client(trust_env=False, timeout=timeout) as c:
        for attempt in range(1, tries + 1):
            try:
                r = c.get(url)
                if r.status_code < 500:
                    return dict(r.json()) if r.content else {}
            except httpx.HTTPError:
                pass
            if attempt < tries:
                time.sleep(delay)
    raise DemoError(f"GET {url} 重试 {tries} 次失败")


def api_post(url: str, payload: dict[str, Any], *, tries: int = 3) -> dict[str, Any]:
    """POST（仅用于幂等/签发语义端点：manifests upsert、apikeys、providers upsert）。"""
    with httpx.Client(trust_env=False, timeout=15.0) as c:
        for attempt in range(1, tries + 1):
            try:
                r = c.post(url, json=payload)
                if r.status_code < 500:
                    check(r.status_code < 400, f"POST {url} → {r.status_code}: {r.text[:300]}")
                    return dict(r.json())
            except httpx.HTTPError:
                pass
            if attempt < tries:
                time.sleep(5.0)
    raise DemoError(f"POST {url} 重试失败")


def send_tx(
    w3: Web3, account: Any, to: str, data: str, *, gas: int, value: int = 0
) -> tuple[str, dict[str, Any]]:
    """本地直签 raw tx：chainId 断言先于签名（防错链），20 gwei，等回执。

    legacy（gasPrice）形态 + 必须显式带 `to`（与 SDK ChainGateway 同款）：漏 to 会被
    当作合约创建签名上链——回执 status=1 但 value 沉进空创建里、收款方余额不增。
    """
    check(w3.eth.chain_id == CHAIN_ID, f"chainId={w3.eth.chain_id} 非 968，拒绝签名")
    tx: dict[str, Any] = {
        "from": account.address,
        "to": Web3.to_checksum_address(to),
        "nonce": w3.eth.get_transaction_count(account.address),
        "gas": gas,
        "gasPrice": GAS_GWEI * 10**9,
        "chainId": CHAIN_ID,
    }
    if value:
        tx["value"] = value
    if data and data != "0x":
        tx["data"] = data
    signed = account.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed["raw_transaction"])
    receipt = w3.eth.wait_for_transaction_receipt(h, timeout=90)
    check(receipt["status"] == 1, f"tx {h.hex()} 回执失败")
    return h.hex(), dict(receipt)


def erc20_call(w3: Web3, to: str, sig: str, *args: str) -> int:
    data = "0x" + keccak(text=sig)[:4].hex()
    for a in args:
        data += a.rjust(64, "0")
    raw = w3.eth.call({"to": Web3.to_checksum_address(to), "data": data})
    return int.from_bytes(raw, "big")


def addr_arg(addr: str) -> str:
    return addr.lower().removeprefix("0x")


def fresh_key() -> str:
    """每次付费调用独立幂等键（同参数默认幂等重放防双扣，演示逐笔计费）。"""
    return str(uuid.uuid4())


def paid_call(
    client: Client,
    service_id: str,
    params: dict[str, Any],
    rec: Recorder | None = None,
    tries: int = 4,
) -> Any:
    """付费调用 + 瞬断重试（5s×N，上游协调惯例）：仅 502 provider_failed/service_error
    重试——该路径 calls=aborted 零扣款（A7），重试用新幂等键+新授权 nonce；
    402/401/4xx 语义错误不重试直接抛。"""
    last: GatewayError | None = None
    for attempt in range(1, tries + 1):
        try:
            return client.call(service_id, params, idempotency_key=fresh_key())
        except GatewayError as exc:
            if exc.status_code != 502:
                raise
            last = exc
            if rec is not None:
                rec.log(
                    f"付费调用 {service_id} 瞬断（{exc.detail[:60]}）→ 5s 后重试 {attempt}/{tries - 1}"
                )
            time.sleep(5.0)
    assert last is not None
    raise last


# ---- 本地 http_json 端点（友队 / 我方备用，演示 http_json 真实外联形态） ----


def make_translate_server(tag: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # http.server 约定名
            length = int(self.headers.get("Content-Length") or 0)
            payload: dict[str, Any] = {}
            if length:
                try:
                    loaded = json.loads(self.rfile.read(length))
                    if isinstance(loaded, dict):
                        payload = loaded
                except ValueError:
                    payload = {}
            body = {
                "translated": f"[{tag}] {payload.get('text')}",
                "text": payload.get("text"),
                "provider_endpoint": tag,
            }
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args: Any) -> None:  # 静默访问日志
            return

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def start_server(server: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t


def publish_translate_manifest(endpoint: dict[str, Any], provider_wallet: str) -> dict[str, Any]:
    spec = SERVICES["svc_translate"]
    return api_post(
        f"{CORE}/manifests",
        {
            "service_id": "svc_translate",
            "name": spec["name"],
            "version": "1.0.1",
            "provider": {
                "agent_id": AGENT_ID,
                "wallet": provider_wallet,
                "display_name": DISPLAY_NAME,
            },
            "endpoint": endpoint,
            "pricing": {
                "model": "per_call",
                "token": "USDT",
                "amount": spec["price"],
                "amount_raw": spec["price_raw"],
            },
            "chain": {"network": CHAIN_ID},
            "input_schema": spec["input_schema"],
            "output_schema": {"type": "object"},
        },
    )


def wait_manifest_propagation(rec: Recorder, what: str) -> None:
    rec.log(f"等待网关 manifest 缓存 TTL 过期（{MANIFEST_CACHE_TTL_S:.0f}s 切换传播上限）→ {what}")
    for left in range(int(SWITCH_PROPAGATION_S), 0, -10):
        print(f"  …{left}s", flush=True)
        time.sleep(min(10, left))


def keeper_settled_enough(status: dict[str, Any], base_count: int, want: int) -> bool:
    pending = int((status.get("queue") or {}).get("pending") or 0)
    done_new = int(status.get("cumulative_charged_count") or 0) - base_count
    return pending == 0 and done_new >= want


def main() -> None:  # 三幕剧本单函数流程（scripts 豁免组风格）
    rec = Recorder(Path("results/demo_p1.md"))
    w3 = Web3(Web3.HTTPProvider(RPC))
    consumer = Account.from_key(ANVIL1_PK)
    provider_acct = Account.from_key(ANVIL2_PK)
    provider_wallet = provider_acct.address
    check(w3.eth.chain_id == CHAIN_ID, "链不在 968，拒绝彩排")

    rec.log("=== 预检：三服务健康 + keeper 开关 + 链 RPC ===")
    for name, url in (("8010", BOTCHAIN), ("8020", CORE), ("8030", GATEWAY)):
        api_get(f"{url}/healthz")
        rec.log(f"{name} healthz OK")
    keeper0 = api_get(f"{GATEWAY}/internal/keeper/status")
    check(keeper0.get("enabled") is True, "8030 未开 keeper（需 COINCALL_KEEPER_ENABLED=true）")
    rec.log(f"keeper: batch_size={keeper0['batch_size']} queue={keeper0['queue']}")
    block0 = w3.eth.block_number
    overview0 = api_get(f"{CORE}/stats/overview")
    rec.block(
        "开场大屏 /stats/overview（演示前）", json.dumps(overview0, ensure_ascii=False, indent=1)
    )

    # gas 预检（公开测试账户间 legacy raw tx）：consumer(#1) 为 gas 赞助方；
    # provider(#2) 不足 0.05 BOT 则补 withdraw gas；到账即断言（防链吞值）
    check(
        w3.eth.get_balance(consumer.address) > 3 * 10**17,
        "anvil#1 BOT 不足 0.3，无法赞助演示 gas（先领水再彩排）",
    )
    if w3.eth.get_balance(provider_wallet) < 5 * 10**16:
        h, _r = send_tx(
            w3, consumer, provider_wallet, "0x", gas=TRANSFER_GAS, value=int(0.05 * 10**18)
        )
        rec.log(f"provider(#2) withdraw gas 预备（#1 → #2 0.05 BOT）：{h}")
    check(
        w3.eth.get_balance(provider_wallet) >= 4 * 10**16,
        "provider(#2) BOT 到账断言失败（链吞值？）",
    )
    rec.log(
        f"钱包就绪：consumer={consumer.address} provider={provider_wallet} "
        f"(BOT: #1={w3.eth.get_balance(consumer.address)} #2={w3.eth.get_balance(provider_wallet)} wei)"
    )

    # ---------------- 第一幕：挂服务（30 秒口径） ----------------
    rec.log("=== 第一幕：挂服务（任何 Agent 能力，30 秒变成收费服务） ===")
    t1 = time.monotonic()
    row = api_post(f"{CORE}/providers", {"agent_id": AGENT_ID, "display_name": DISPLAY_NAME})
    rec.log(f"provider 登记：agent_id={row['agent_id']} 链上 agentWallet={row['wallet']}")
    for service_id, spec in SERVICES.items():
        ack = api_post(
            f"{CORE}/manifests",
            {
                "service_id": service_id,
                "name": spec["name"],
                "version": "1.0.0",
                "provider": {
                    "agent_id": AGENT_ID,
                    "wallet": provider_wallet,
                    "display_name": DISPLAY_NAME,
                },
                "endpoint": spec["endpoint"],
                "pricing": {
                    "model": "per_call",
                    "token": "USDT",
                    "amount": spec["price"],
                    "amount_raw": spec["price_raw"],
                },
                "chain": {"network": CHAIN_ID},
                "input_schema": spec["input_schema"],
                "output_schema": {"type": "object"},
            },
        )
        rec.log(
            f"manifest 发布 {service_id} @ {spec['price']} USDT hash={str(ack['manifest_hash'])[:24]}…"
        )
    catalog = api_get(f"{CORE}/catalog")
    listed = {str(s["service_id"]) for s in catalog["services"]}
    check(set(SERVICES) <= listed, f"catalog 缺服务: {set(SERVICES) - listed}")
    act1_s = time.monotonic() - t1
    check(act1_s <= 30.0, f"第一幕耗时 {act1_s:.1f}s 超 30s 口径")
    rec.log(f"catalog 可见 {sorted(listed)}；第一幕耗时 {act1_s:.1f}s（≤30s 口径 PASS）")

    # ---------------- 第二幕：付费调用（402 指引 + SDK 真实付费） ----------------
    rec.log("=== 第二幕：付费调用（Agent 只需要会 HTTP） ===")
    wallet1 = LocalWallet.from_key(ANVIL1_PK)
    rec.log(f"SDK 导入消费者钱包 anvil#1：{wallet1.address}（私钥不出本进程）")
    if wallet1.balance().usdt_balance_raw < 500_000:
        minted = wallet1.mint("1")
        rec.log(f"MockUSDT 公开 mint 1 USDT：tx={minted['tx_hash']}")
    if wallet1.balance().vault_allowance_raw < 500_000:
        appr = wallet1.approve_vault("1")
        rec.log(f"approve(PayVault) 1 USDT：tx={appr['tx_hash']}")
    key1 = api_post(f"{CORE}/apikeys", {"consumer_wallet": wallet1.address})
    client1 = Client(api_key=str(key1["api_key"]), wallet=wallet1, budget_raw=2_000_000)
    rec.log(f"core 签发 api key（key_id={key1.get('key_id')}）；SDK 预算闸 budget_raw=2000000")

    # 402 指引路径：未 approve 的新钱包 → insufficient_allowance → guidance → approve → 成功
    rec.log("--- 402 指引路径演示：未 approve 的新 key ---")
    fresh = LocalWallet.create()
    rec.log(f"SDK 本地新钱包（新 key：先 mint 出余额，保持未 approve）：{fresh.address}")
    h, _r = send_tx(w3, consumer, fresh.address, "0x", gas=TRANSFER_GAS, value=int(0.05 * 10**18))
    rec.log(f"新钱包 gas 预备（#1 → fresh 0.05 BOT）：{h}")
    check(
        w3.eth.get_balance(fresh.address) >= 4 * 10**16,
        "fresh BOT 到账断言失败（链吞值？）",
    )
    minted = fresh.mint("0.1")
    rec.log(f"新钱包 mint 0.1 USDT：tx={minted['tx_hash']}")
    key_fresh = api_post(f"{CORE}/apikeys", {"consumer_wallet": fresh.address})
    client_fresh = Client(api_key=str(key_fresh["api_key"]), wallet=fresh, budget_raw=100_000)
    guidance_text = ""
    try:
        client_fresh.call("svc_translate", {"text": "hello 402 drill"}, idempotency_key=fresh_key())
        raise DemoError("未 approve 的新钱包竟调用成功，402 指引路径未触发")
    except PaymentRequiredError as exc:
        check(exc.code == "insufficient_allowance", f"预期 insufficient_allowance，实得 {exc.code}")
        guidance_text = str(exc)
        rec.log(f"402 质询命中：code={exc.code}（SDK 已把质询转成人话指引）")
    rec.block("PaymentRequiredError（SDK 402 指引原文）", guidance_text)
    appr = fresh.approve_vault("0.1")
    rec.log(f"照指引执行 wallet.approve_vault('0.1')：tx={appr['tx_hash']}")
    rec.log(f"等网关链上约束短缓存过期（{CHAIN_CACHE_TTL_S:.0f}s，approve 可见性）再重试")
    time.sleep(CHAIN_CACHE_TTL_S + 2.0)
    ok_fresh = paid_call(client_fresh, "svc_translate", {"text": "hello 402 drill"}, rec)
    check(ok_fresh.charged_raw == "10000", f"fresh 调用扣款 {ok_fresh.charged_raw} != 10000")
    rec.log(
        f"approve 后重试成功：receipt={ok_fresh.receipt_id} X-Charged-Raw={ok_fresh.charged_raw}"
    )

    rec.log("--- anvil#1 经 SDK 对三个服务各发起真实付费调用 ---")
    paid_count = 1
    r1 = paid_call(
        client1, "svc_translate", {"text": "Agent economies need verifiable settlement."}, rec
    )
    paid_count += 1
    rec.block(
        "svc_translate 收据（X-Charged-Raw 上屏）",
        json.dumps(
            {
                "receipt_id": r1.receipt_id,
                "X-Charged-Raw": r1.charged_raw,
                "X-Receipt-Sig": (r1.receipt_sig or "")[:24] + "…",
                "translated": r1.body.get("translated"),
                "engine": r1.body.get("engine"),
            },
            ensure_ascii=False,
            indent=1,
        ),
    )
    r2 = paid_call(client1, "svc_contract_scan", {"window_blocks": 5000}, rec)
    paid_count += 1
    rec.block(
        "svc_contract_scan 结构化摘要（付费结果）",
        json.dumps(
            {
                "receipt_id": r2.receipt_id,
                "X-Charged-Raw": r2.charged_raw,
                "count": r2.body.get("count"),
                "total_value": r2.body.get("total_value"),
                "window": r2.body.get("window"),
                "latest": (r2.body.get("events") or [{}])[0],
            },
            ensure_ascii=False,
            indent=1,
        ),
    )
    r3 = paid_call(client1, "svc_chain_report", {}, rec)
    paid_count += 1
    rec.block("svc_chain_report 报告全文（付费结果）", str(r3.body.get("report")))
    spent_after_act2 = client1.spent_raw + client_fresh.spent_raw
    check(spent_after_act2 == DEMO_SPEND_RAW, f"本地花费 {spent_after_act2} != {DEMO_SPEND_RAW}")
    rec.log(
        f"第二幕合计 {paid_count} 笔付费调用、SDK 本地预算计数 {spent_after_act2} raw"
        f"（= 定价总和 {DEMO_SPEND_RAW}）"
    )

    # ---------------- 第三幕：结算与信誉 ----------------
    rec.log("=== 第三幕：结算与信誉（收入就是最硬的信誉，每一笔都在链上） ===")
    base_count = int(keeper0["cumulative_charged_count"])
    batch_txs: list[str] = []
    deadline = time.monotonic() + KEEPER_WAIT_S * 2
    while time.monotonic() < deadline:
        st = api_get(f"{GATEWAY}/internal/keeper/status")
        last = st.get("last_batch") or {}
        if last.get("tx_hash") and last.get("charged") and str(last["tx_hash"]) not in batch_txs:
            batch_txs.append(str(last["tx_hash"]))
        if keeper_settled_enough(st, base_count, paid_count):
            break
        time.sleep(3)
    else:
        raise DemoError("keeper 180s 内未结清本幕调用")
    st = api_get(f"{GATEWAY}/internal/keeper/status")
    done_new = int(st["cumulative_charged_count"]) - base_count
    check(done_new == paid_count, f"keeper 新结算笔数 {done_new} != {paid_count}")
    rec.log(
        f"keeper 新批上链（{len(batch_txs)} 个批次 tx）："
        + ", ".join(t[:18] + "…" for t in batch_txs)
    )

    logs = api_get(
        f"{BOTCHAIN}/api/v1/contracts/logs?address={PAY_VAULT}"
        f"&topic0=0x{keccak(text='Charged(address,address,uint256,bytes32)').hex()}"
        f"&from_block={max(0, block0 - 10)}&to_block={w3.eth.block_number}"
    )
    events = [decode_charged_log(log) for log in logs.get("logs") or []]
    events = [e for e in events if int(e["block_number"] or 0) >= block0 - 10]
    charged_total = sum(int(e["value_raw"] or 0) for e in events)
    check(charged_total == DEMO_SPEND_RAW, f"新 Charged 总额 {charged_total} != {DEMO_SPEND_RAW}")
    rec.block(
        "链上 Charged 明细（大屏）",
        "\n".join(
            f"{e['tx_hash']}  block={e['block_number']}  {e['value']} USDT  "
            f"provider={e['provider']} from={e['from']}"
            for e in sorted(events, key=lambda e: int(e["block_number"] or 0))
        ),
    )

    rec.log("--- providerWithdraw（anvil#2 本地直签，msg.sender=provider） ---")
    credits_before = erc20_call(w3, PAY_VAULT, "credits(address)", addr_arg(provider_wallet))
    check(credits_before > 0, "provider credits 为 0，无款可提")
    usdt_before = erc20_call(w3, MOCK_USDT, "balanceOf(address)", addr_arg(provider_wallet))
    withdraw_data = (
        "0x"
        + keccak(text="providerWithdraw(address,uint256)")[:4].hex()
        + (addr_arg(provider_wallet).rjust(64, "0") + hex(credits_before)[2:].rjust(64, "0"))
    )
    wh, receipt = send_tx(w3, provider_acct, PAY_VAULT, withdraw_data, gas=120_000)
    usdt_after = erc20_call(w3, MOCK_USDT, "balanceOf(address)", addr_arg(provider_wallet))
    credits_after = erc20_call(w3, PAY_VAULT, "credits(address)", addr_arg(provider_wallet))
    delta = usdt_after - usdt_before
    check(delta == credits_before, f"到账增量 {delta} != 提取额 {credits_before}")
    check(credits_after == 0, f"提现后 credits 残留 {credits_after}")
    withdrawn_topic = "0x" + keccak(text="Withdrawn(address,address,uint256)").hex()
    # HexBytes.hex() 在本 web3 版本无 0x 前缀，补齐再比对
    wevents = [t for t in receipt.get("logs", []) if "0x" + t["topics"][0].hex() == withdrawn_topic]
    withdrawn_amount = int(wevents[0]["data"].hex(), 16) if wevents else -1
    check(
        withdrawn_amount == credits_before,
        f"Withdrawn 事件金额 {withdrawn_amount} != {credits_before}",
    )
    rec.block(
        "providerWithdraw 到账对账",
        json.dumps(
            {
                "tx": wh,
                "explorer": EXPLORER_TX + wh,
                "credits_before_raw": credits_before,
                "usdt_balance_before_raw": usdt_before,
                "usdt_balance_after_raw": usdt_after,
                "到账增量（raw）": delta,
                "断言": "增量 == Withdrawn 事件金额 == 提取额 == credits_before；提现后 credits == 0",
                "Withdrawn 事件数": len(wevents),
                "Withdrawn 金额（raw）": withdrawn_amount,
            },
            ensure_ascii=False,
            indent=1,
        ),
    )

    rec.log("--- 排行榜 / proof / overview 刷新 ---")
    board = api_get(f"{CORE}/leaderboard/providers")
    me = next(
        (p for p in board["providers"] if str(p["wallet"]).lower() == provider_wallet.lower()), None
    )
    check(me is not None, "provider 未上榜")
    proof = api_get(f"{CORE}/leaderboard/providers/{provider_wallet}/proof")
    overview = api_get(f"{CORE}/stats/overview")
    rec.block(
        "排行榜（/leaderboard/providers，收入优先）",
        json.dumps(board, ensure_ascii=False, indent=1),
    )
    rec.block(
        f"链上 proof（/leaderboard/providers/{provider_wallet[:10]}…/proof）",
        json.dumps(
            {
                "revenue": proof["revenue"],
                "revenue_raw": proof["revenue_raw"],
                "count": proof["count"],
                "events（前 3 笔）": [
                    {"tx": e["tx_hash"], "value": e["value"], "url": e["explorer_url"]}
                    for e in proof["events"][:3]
                ],
            },
            ensure_ascii=False,
            indent=1,
        ),
    )
    rec.block(
        "收尾大屏 /stats/overview（演示后）", json.dumps(overview, ensure_ascii=False, indent=1)
    )
    rec.log(
        f"GMV {overview0.get('gmv')} → {overview.get('gmv')} USDT；"
        f"provider 收入（链上 Charged 真相）revenue={me['revenue']} count={me['charged_count']}"
    )

    # ---------------- 兜底演练（07 §4 风险表） ----------------
    rec.log("=== 兜底演练：svc_translate 端点热切换（07 §4 友队掉线风险行） ===")
    team = make_translate_server("友队翻译-team", TEAM_PORT)
    start_server(team)
    ack = publish_translate_manifest(
        {"type": "http_json", "url": f"http://127.0.0.1:{TEAM_PORT}/translate", "timeout_ms": 8000},
        provider_wallet,
    )
    rec.log(f"PATCH=upsert 切友队 http_json 端点 hash={str(ack['manifest_hash'])[:24]}…")
    wait_manifest_propagation(rec, "友队端点生效")
    r = client1.call("svc_translate", {"text": DRILL_TEXT}, idempotency_key=fresh_key())
    check(r.body.get("provider_endpoint") == "友队翻译-team", f"友队端点未生效: {r.body}")
    rec.log(
        f"http_json 形态真实外联成功：receipt={r.receipt_id} body.provider_endpoint=友队翻译-team"
    )

    team.shutdown()
    time.sleep(1)
    spent_before = client1.spent_raw
    aborted_before = api_get(f"{GATEWAY}/internal/stats/calls")["totals"]["calls_aborted"]
    try:
        client1.call("svc_translate", {"text": DRILL_TEXT}, idempotency_key=fresh_key())
        raise DemoError("友队掉线后调用竟成功，风险行未复现")
    except GatewayError as exc:
        check(
            exc.status_code == 502 and exc.code == "provider_failed",
            f"预期 502/provider_failed，实得 {exc.status_code}/{exc.code}",
        )
        rec.log(f"友队掉线 → 502 provider_failed（{exc.detail[:60]}）")
    aborted_after = api_get(f"{GATEWAY}/internal/stats/calls")["totals"]["calls_aborted"]
    check(client1.spent_raw == spent_before, "失败调用后本地花费变了（违反零扣款语义）")
    check(aborted_after == aborted_before + 1, "gateway aborted 未 +1")
    rec.log(
        f"失败=从未扣款：SDK spent_raw 不变（{spent_before}）、gateway aborted +1 → {aborted_after}"
    )

    backup = make_translate_server("我方备用端点-backup", BACKUP_PORT)
    start_server(backup)
    ack = publish_translate_manifest(
        {
            "type": "http_json",
            "url": f"http://127.0.0.1:{BACKUP_PORT}/translate",
            "timeout_ms": 8000,
        },
        provider_wallet,
    )
    rec.log(f"PATCH=upsert 切我方备用端点 hash={str(ack['manifest_hash'])[:24]}…")
    wait_manifest_propagation(rec, "备用端点生效")
    r = client1.call("svc_translate", {"text": DRILL_TEXT}, idempotency_key=fresh_key())
    check(r.body.get("provider_endpoint") == "我方备用端点-backup", f"备用端点未生效: {r.body}")
    rec.log(f"备用端点承接成功：receipt={r.receipt_id}（调用不中断，同一 SDK 形态）")

    ack = publish_translate_manifest(dict(SERVICES["svc_translate"]["endpoint"]), provider_wallet)
    rec.log(f"PATCH=upsert 切回 internal://translate 兜底 hash={str(ack['manifest_hash'])[:24]}…")
    wait_manifest_propagation(rec, "internal 兜底生效")
    r = client1.call("svc_translate", {"text": DRILL_TEXT}, idempotency_key=fresh_key())
    check(r.body.get("engine") == "internal-fallback", f"internal 兜底未生效: {r.body}")
    rec.log(f"切回 internal 兜底成功：receipt={r.receipt_id} engine=internal-fallback")
    team.server_close()
    backup.server_close()

    rec.block(
        "彩排结论",
        "三幕全程无人工干预；402 指引→approve→成功闭环；keeper 批量结算上链；\n"
        "providerWithdraw 到账增量==提取额；友队掉线=零扣款，manifest 热切换三段式\n"
        f"（友队→备用→internal）全部恢复服务。切换传播时延=manifest 缓存 TTL {MANIFEST_CACHE_TTL_S:.0f}s。\n"
        "诚实边界：overview 数字即全部真实发生过的调用，无 GMV 修饰。",
    )
    print(f"\nDEMO P1 PASS — 录档 results/demo_p1.md（run {uuid.uuid4().hex[:8]}）")


if __name__ == "__main__":
    main()
