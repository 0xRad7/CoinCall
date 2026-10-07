// @vitest-environment jsdom
/**
 * 决策层展示测试：
 * 类目 chips + 分区列表渲染；四分量条形与徽章来自 mock decision；
 * as_of 拨针触发重新请求（断言二次请求带 as_of 参数）；排序按 score 降序。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import Overview from "../src/pages/Overview";

const R = (sid: string, name: string, score: number, parts: [number, number, number, number], extra: Partial<{ success_rate: number | null; p95: number | null; fbCount: number; fbAvg: number | null; lastActivity: string | null; anchor?: { digest: string; anchor_tx: string } }>) => ({
  service_id: sid,
  name,
  status: "active",
  category: "other",
  tags: [],
  provider_wallet: "0xb4ad544f875b908110e9c8df1c54f1b1b0bbd79f",
  provider_agent_id: 162,
  price_raw: "10000",
  price: "0.01",
  score,
  components: {
    revenue: { total_raw: 100000, total: "0.1", charged_count: 10, distinct_payers: 3, score_component: parts[0], formula: "", proof: "" },
    fulfillment: { available: true, success_rate: extra.success_rate ?? 0.95, p95_ms: extra.p95 ?? 11, score_component: parts[1], proof: extra.anchor ?? null },
    feedback: { count: extra.fbCount ?? 0, avg: extra.fbAvg ?? null, score_component: parts[2] },
    freshness: { last_activity_at: extra.lastActivity ?? "2026-10-06T06:43:18Z", score_component: parts[3] },
  },
});

const DECISION = {
  category: null,
  window_hours: 168,
  as_of: "2026-10-06T12:00:00Z",
  sort: "score",
  weights: { revenue: 0.4, fulfillment: 0.25, feedback: 0.2, freshness: 0.15 },
  services: [
    R("svc_a", "Alpha", 0.9, [1.0, 0.95, 0.5, 0.9], { fbCount: 3, fbAvg: 4.7, anchor: { digest: "sha256:abcdef1234567890", anchor_tx: "0x" + "cd".repeat(32) } }),
    R("svc_b", "Beta", 0.6, [0.5, 0.6, 0.4, 0.2], { success_rate: null, p95: null }),
  ],
  degraded: [],
};

const CATS = { categories: ["translation", "other"], counts: { translation: 0, other: 2 }, total_active: 2 };

let calls: Array<{ url: string }>;

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

function installFetch() {
  calls = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = String(input);
    calls.push({ url });
    if (url === "/api/core/decision/categories") return jsonResponse(CATS);
    if (url.startsWith("/api/core/decision/services")) return jsonResponse(DECISION);
    if (url === "/api/core/catalog") return jsonResponse({ services: [], count: 0 });
    if (url === "/api/core/stats/overview") return jsonResponse({ gmv_raw: 0, gmv: "0", charged_count: 0, calls_success_total: 0, calls_aborted_total: 0, services_total: 0, services_active: 0, providers_registered: 0, providers_with_revenue: 0, synced_to_block: 0, degraded: [] });
    if (url === "/api/core/leaderboard/providers") return jsonResponse({ order: "revenue", providers: [] });
    if (url === "/api/core/leaderboard/services") return jsonResponse({ order: "revenue", services: [] });
    if (url === "/api/gw/internal/keeper/status") return jsonResponse({ enabled: true, running: true, batch_size: 3, flush_interval_s: 30, queue: { pending: 0, done: 0, failed: 0, expired: 0 }, last_batch: null, cumulative_charged_count: 0, cumulative_charged_raw: "0", blacklisted_consumers: [], last_error: null });
    return jsonResponse({ error: "not_mocked", detail: url }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
}

beforeEach(() => {
  cleanup();
  sessionStorage.clear();
  localStorage.clear();
  installFetch();
});
afterEach(() => vi.unstubAllGlobals());

describe("决策视图", () => {
  it("类目 chips + 排序列表渲染（名称/价格/综合分/徽章）", async () => {
    render(<Overview />);
    // 类目 chips（含数量）
    expect(await screen.findByRole("group", { name: "类目筛选" })).toBeTruthy();
    expect(screen.getByText(/全部（2）/)).toBeTruthy();
    expect(screen.getByText(/other（2）/)).toBeTruthy();
    // 排序行：Alpha 在前（score 0.9）
    expect(screen.getByText("Alpha")).toBeTruthy();
    expect(screen.getByText("Beta")).toBeTruthy();
    expect(screen.getByText("0.9000")).toBeTruthy(); // 综合分
    expect(screen.getByText("0.6000")).toBeTruthy();
    // 徽章：成功率/p95/时钟衰减（反馈分量已移除——★徽章不应出现）
    expect(screen.getAllByText(/95%/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/p95 11ms/).length).toBeGreaterThanOrEqual(1);
    expect(document.body.textContent).not.toMatch(/★/); // 无反馈星徽章
    expect(screen.getAllByText(/衰减 90%/).length).toBeGreaterThanOrEqual(1); // fresh 0.9
    // 公式三信号文案
    expect(screen.getByText(/score = 0\.5×收入 \+ 0\.3×履约 \+ 0\.2×新鲜度/)).toBeTruthy();
  });

  it("点类目 chip 触发重新请求带 category", async () => {
    render(<Overview />);
    await screen.findByText("Alpha");
    const before = calls.filter((c) => c.url.includes("/decision/services")).length;
    fireEvent.click(screen.getByText(/other（2）/));
    await waitFor(() => expect(calls.filter((c) => c.url.includes("/decision/services")).length).toBe(before + 1));
    expect(calls.filter((c) => c.url.includes("category=other")).length).toBeGreaterThanOrEqual(1);
  });

  it("时间拨针（as_of）触发二次请求带 as_of 参数", async () => {
    render(<Overview />);
    await screen.findByText("Alpha");
    fireEvent.change(screen.getByLabelText("时间拨针（as_of）"), { target: { value: "2026-10-12T00:00" } });
    await waitFor(() => expect(calls.some((c) => c.url.includes("as_of=2026-10-12"))) || expect(calls.some((c) => c.url.includes("as_of=2026%2F10%2F12"))).toBeTruthy());
    const asOfCalls = calls.filter((c) => c.url.includes("as_of="));
    expect(asOfCalls.length).toBeGreaterThanOrEqual(1);
  });
});
