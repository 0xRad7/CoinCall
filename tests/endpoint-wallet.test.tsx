// @vitest-environment jsdom
/**
 * 1) 调用端点动态生成三态（默认同源代理 origin/api/gw、LAN origin、VITE_GATEWAY_PUBLIC_URL 覆盖）
 *    + 目录卡渲染动态端点；
 * 2) 提现：地址默认=连接钱包、「查询我的 credits」一键（RPC eth_call mock）、快填；
 * 3) 地址输入审计：Provider 页无空置的「我的地址」类 placeholder。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import Overview from "../src/pages/Overview";
import { WithdrawStep } from "../src/pages/ProviderWorkbench";
import { WalletProvider, useWallet } from "../src/state/WalletContext";
import { gatewayBaseUrl, gatewayCallUrl } from "../src/chain/constants";
import type { Eip1193Provider } from "../src/chain/injected";
import { fetchProviderCredits } from "../src/chain/rpc";

// ethers 的 JsonRpcProvider 在测试环境会绕过 global fetch 走真网络——模块级 mock 保证无网化
const creditsMock = vi.mocked(fetchProviderCredits);
vi.mock("../src/chain/rpc", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../src/chain/rpc")>();
  return { ...mod, fetchProviderCredits: vi.fn() };
});

const MY = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8";

function mockWalletProvider(): Eip1193Provider {
  return {
    request: async ({ method }: { method: string }) => {
      if (method === "eth_requestAccounts" || method === "eth_accounts") return [MY];
      if (method === "eth_chainId") return "0x3c8";
      return null;
    },
  };
}
function announce() {
  window.dispatchEvent(
    new CustomEvent("eip6963:announceProvider", { detail: { info: { uuid: "com.okx.wallet", name: "OKX Wallet", icon: "", rdns: "com.okx.wallet" }, provider: mockWalletProvider() } })
  );
}
function ConnectProbe() {
  const w = useWallet();
  return (
    <button onClick={() => void w.connect()} data-testid="probe-connect">
      连接钱包
    </button>
  );
}

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.unstubAllEnvs();
  delete (window as { ethereum?: unknown }).ethereum;
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("网关调用端点动态生成（单一来源 helper）", () => {
  it("默认 = 访问 origin + /api/gw（jsdom 本机 localhost）", () => {
    expect(gatewayBaseUrl()).toBe(`${window.location.origin}/api/gw`);
    expect(gatewayCallUrl("svc_x")).toBe(`${window.location.origin}/api/gw/call/svc_x`);
  });

  it("LAN origin：局域网访客自动得到 172.x 地址（零配置）", () => {
    expect(gatewayBaseUrl("http://172.20.31.7:5173")).toBe("http://172.20.31.7:5173/api/gw");
    expect(gatewayCallUrl("svc_x", "http://172.20.31.7:5173")).toBe("http://172.20.31.7:5173/api/gw/call/svc_x");
  });

  it("VITE_GATEWAY_PUBLIC_URL 覆盖：直接拼 {值}/call/{id}（尾斜杠容错）", () => {
    vi.stubEnv("VITE_GATEWAY_PUBLIC_URL", "https://api.coincall.example/");
    expect(gatewayBaseUrl("http://172.20.31.7:5173")).toBe("https://api.coincall.example"); // 覆盖优先于 origin
    expect(gatewayCallUrl("svc_x")).toBe("https://api.coincall.example/call/svc_x");
  });

  it("目录卡渲染动态端点：默认 origin；覆盖后为域名", async () => {
    const catalog = {
      services: [
        {
          service_id: "svc_http",
          manifest: {
            service_id: "svc_http",
            name: "HTTP Svc",
            description: "",
            version: "1.0.0",
            provider: { agent_id: 169, wallet: MY, display_name: "P" },
            endpoint: { type: "http_json", url: null, timeout_ms: 30000, method: "GET" },
            pricing: { token: "USDT", model: "per_call", amount: "0.01", amount_raw: "10000" },
            chain: { network: 968 },
            input_schema: { type: "object" },
            output_schema: { type: "object" },
            status: "active",
            created_at: "t",
          },
          status: "active",
          manifest_hash: "sha256:a",
        },
      ],
      count: 1,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
      const url = String(input);
      if (url === "/api/core/catalog") return jsonResponse(catalog);
      if (url === "/api/core/stats/overview") return jsonResponse({ gmv_raw: 0, gmv: "0", charged_count: 0, calls_success_total: 0, calls_aborted_total: 0, services_total: 1, services_active: 1, providers_registered: 1, providers_with_revenue: 0, synced_to_block: 1, degraded: [] });
      if (url === "/api/core/leaderboard/providers") return jsonResponse({ order: "revenue", providers: [] });
      if (url === "/api/core/leaderboard/services") return jsonResponse({ order: "revenue", services: [] });
      if (url === "/api/gw/internal/keeper/status") return jsonResponse({ enabled: true, running: true, batch_size: 3, flush_interval_s: 30, queue: { pending: 0, done: 0, failed: 0, expired: 0 }, last_batch: null, cumulative_charged_count: 0, cumulative_charged_raw: "0", blacklisted_consumers: [], last_error: null });
      return jsonResponse({ error: "not_mocked", detail: url }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { unmount } = render(<Overview />);
    await waitFor(() => expect(document.body.textContent).toContain(`POST ${window.location.origin}/api/gw/call/svc_http`));
    unmount();

    vi.stubEnv("VITE_GATEWAY_PUBLIC_URL", "https://api.coincall.example");
    render(<Overview />);
    await waitFor(() => expect(document.body.textContent).toContain("POST https://api.coincall.example/call/svc_http"));
  });
});

describe("提现：地址自动读连接钱包 + 一键查我的 credits", () => {
  it("连接后地址输入默认=连接地址，有快填按钮；「查询我的 credits」一键用连接地址查询并展示", async () => {
    creditsMock.mockResolvedValue(1_500_000n); // 1.5 USDT

    render(
      <WalletProvider>
        <WithdrawStep />
        <ConnectProbe />
      </WalletProvider>
    );
    announce();
    fireEvent.click(screen.getByTestId("probe-connect"));

    const input = await screen.findByLabelText("提现查询地址");
    await waitFor(() => expect((input as HTMLInputElement).value).toBe(MY)); // 默认自动读连接钱包
    expect(screen.getByRole("button", { name: new RegExp(`使用当前钱包 ${MY.slice(0, 6)}`) })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "查询我的 credits" }));
    await waitFor(() => expect(screen.getByText(/1\.5 USDT/)).toBeTruthy());
    // 一键查询用的是连接地址（credits(address) 的参数语义）
    expect(creditsMock).toHaveBeenCalledWith(MY);
  });
});

describe("地址输入审计：Provider 页无空置「我的地址」类 placeholder", () => {
  it("不含让用户手填自己地址的空置提示（我的地址语义一律自动填）", () => {
    const content = readFileSync(join(__dirname, "..", "src", "pages", "ProviderWorkbench.tsx"), "utf-8");
    for (const banned of ["输入你的地址", "填你的地址", "填写你的钱包地址", "粘贴你的地址"]) {
      expect(content, banned).not.toContain(banned);
    }
    // 提现/发布的「我的地址」输入都有自动来源（aria-label 存在即受测）
    expect(content).toContain("查询我的 credits");
    expect(content).toContain("使用当前钱包");
  });
});
