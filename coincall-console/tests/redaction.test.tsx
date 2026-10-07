// @vitest-environment jsdom
/**
 * 公开面脱敏测试：目录卡不渲染上游 url 且有「CoinCall 调用端点」区块（含复制与一句话）；
 * 管理页改价从 internal 通道取全量 manifest（url 不被抹掉）且自己的行可显示真实上游；
 * 公开页面组件残留扫描（不得渲染 endpoint.url）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import Overview from "../src/pages/Overview";
import { ManageStep } from "../src/pages/ProviderWorkbench";
import { WalletProvider, useWallet } from "../src/state/WalletContext";
import { gatewayCallUrl } from "../src/chain/constants";
import type { Eip1193Provider } from "../src/chain/injected";
import type { Catalog } from "../src/api/core";

const SECRET_UPSTREAM = "https://upstream.secret/api";
const ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8";

/** 公开 catalog：url 恒 null（服务端已脱敏；测试同时给一个「如果回归」的对照断言）。 */
function publicCatalog(): Catalog {
  return {
    services: [
      {
        service_id: "svc_http",
        manifest: {
          service_id: "svc_http",
          name: "HTTP Svc",
          description: "",
          version: "1.0.0",
          provider: { agent_id: 169, wallet: "0x70997970C51812dc3A010C7d01b50e0d17dc79C8", display_name: "Demo" },
          endpoint: { type: "http_json", url: null, timeout_ms: 30000, method: "GET" },
          pricing: { token: "USDT", model: "per_call", amount: "0.01", amount_raw: "10000" },
          chain: { network: 968 },
          input_schema: { type: "object" },
          output_schema: { type: "object" },
          status: "active",
          created_at: "2026-10-01T00:00:00Z",
        },
        status: "active",
        manifest_hash: "sha256:a",
      },
    ],
    count: 1,
  } as unknown as Catalog;
}

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

let calls: Array<{ url: string; method: string; body?: string }> = [];

function installFetch(overrides?: { catalog?: Catalog; internalUrl?: string }) {
  const catalog = overrides?.catalog ?? publicCatalog();
  const internalUrl = overrides?.internalUrl ?? SECRET_UPSTREAM;
  calls = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? String(init.body) : undefined });
    if (url === "/api/core/catalog") return jsonResponse(catalog);
    if (url === "/api/core/providers") return jsonResponse({ providers: [] });
    if (/\/api\/core\/internal\/manifests\/[^/]+$/.test(url)) {
      const full = JSON.parse(JSON.stringify(catalog.services[0]!.manifest));
      full.endpoint.url = internalUrl;
      return jsonResponse({ service_id: full.service_id, manifest: full });
    }
    if (url === "/api/core/manifests") return jsonResponse({ service_id: "svc_http", status: "active", manifest_hash: "sha256:n", manifest: catalog.services[0]!.manifest });
    if (url === "/api/core/stats/overview") return jsonResponse({ gmv_raw: 1, gmv: "0", charged_count: 0, calls_success_total: 0, calls_aborted_total: 0, services_total: 1, services_active: 1, providers_registered: 1, providers_with_revenue: 0, synced_to_block: 1, degraded: [] });
    if (url === "/api/core/leaderboard/providers") return jsonResponse({ order: "revenue", providers: [] });
    if (url === "/api/core/leaderboard/services") return jsonResponse({ order: "revenue", services: [] });
    if (url === "/api/gw/internal/keeper/status") return jsonResponse({ enabled: true, running: true, batch_size: 3, flush_interval_s: 30, queue: { pending: 0, done: 0, failed: 0, expired: 0 }, last_batch: null, cumulative_charged_count: 0, cumulative_charged_raw: "0", blacklisted_consumers: [], last_error: null });
    return jsonResponse({ error: "not_mocked", detail: url }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock as unknown as { mock: { calls: Array<{ url: string; body?: string }> } };
}

function mockWalletProviderM(): Eip1193Provider {
  return {
    request: async ({ method }: { method: string }) => {
      if (method === "eth_requestAccounts" || method === "eth_accounts") return [ADDR];
      if (method === "eth_chainId") return "0x3c8";
      return null;
    },
  };
}
function announceM() {
  window.dispatchEvent(
    new CustomEvent("eip6963:announceProvider", { detail: { info: { uuid: "com.okx.wallet", name: "OKX Wallet", icon: "", rdns: "com.okx.wallet" }, provider: mockWalletProviderM() } })
  );
}
function ConnectProbeM() {
  const w = useWallet();
  return (
    <button onClick={() => void w.connect()} data-testid="probe-connect-m">
      连接钱包
    </button>
  );
}

beforeEach(() => {
  cleanup();
  sessionStorage.clear();
  delete (window as { ethereum?: unknown }).ethereum;
});
afterEach(() => vi.unstubAllGlobals());

describe("目录卡（公开面零上游地址）", () => {
  it("不渲染 endpoint.url（即便服务端回归泄漏）；显示 CoinCall 调用端点 + 复制 + 一句话", async () => {
    // 对照组：mock 故意在公开 manifest 里带上 url，证明展示层也不渲染
    const leaky = publicCatalog();
    (leaky.services[0]!.manifest as { endpoint: { url?: string } }).endpoint.url = SECRET_UPSTREAM;
    installFetch({ catalog: leaky });
    render(<Overview />);

    expect(await screen.findByText("CoinCall 调用端点")).toBeTruthy();
    expect(document.body.textContent ?? "").toContain(`POST ${gatewayCallUrl("svc_http")}`); // jsdom origin + /api/gw 同源代理，随访问地址动态
    expect(screen.getByText("复制")).toBeTruthy();
    expect(screen.getByText(/真实上游由平台中转，消费者只看到 CoinCall 端点/)).toBeTruthy();
    expect(screen.getByText("GET 上游")).toBeTruthy(); // method 徽标保留
    // 绝不出现上游地址
    expect(document.body.textContent ?? "").not.toContain("upstream.secret");
    expect(document.body.textContent ?? "").not.toContain("https://upstream");
  });
});

describe("管理页（internal 通道取全量）", () => {
  it("自己的行显示真实上游（来自 internal）；改价 repost 带全量 url 不被抹掉", async () => {
    installFetch();
    render(
      <WalletProvider>
        <ManageStep claimedAgentId={null} onNext={vi.fn()} onBack={vi.fn()} />
        <ConnectProbeM />
      </WalletProvider>
    );
    announceM();
    fireEvent.click(screen.getByTestId("probe-connect-m"));
    // 自己的行显示真实上游 URL（internal 通道取回）
    expect(await screen.findByText(new RegExp("upstream\\.secret"))).toBeTruthy();

    // 改价 0.01 → 0.05
    const priceInput = document.querySelector('input[style*="width: 110"]') as HTMLInputElement;
    fireEvent.change(priceInput, { target: { value: "0.05" } });
    fireEvent.click(screen.getByText("应用改价"));
    expect(await screen.findByText("确认改价")).toBeTruthy();
    fireEvent.click(screen.getByText("确认执行"));

    await waitFor(() => {
      const post = calls.filter((c) => c.url === "/api/core/manifests" && c.method === "POST").pop();
      expect(post).toBeTruthy();
      const body = JSON.parse(post!.body!) as { endpoint: { url: string }; pricing: { amount_raw: string }; version: string };
      expect(body.endpoint.url).toBe(SECRET_UPSTREAM); // internal 全量 url 保住了
      expect(body.pricing.amount_raw).toBe("50000");
      expect(body.version).toBe("1.0.1"); // 版本递增
    });
  });
});

describe("残留扫描（公开页面组件不得渲染 endpoint.url）", () => {
  const root = join(__dirname, "..", "src");
  it("Overview / ConsumerWorkbench 无 endpoint.url 渲染", () => {
    for (const f of ["pages/Overview.tsx", "pages/ConsumerWorkbench.tsx"]) {
      const content = readFileSync(join(root, f), "utf-8");
      expect(content, f).not.toContain("endpoint.url");
    }
  });
});
