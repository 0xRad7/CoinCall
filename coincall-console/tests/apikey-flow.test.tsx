// @vitest-environment jsdom
/**
 * API key 三层换机体验测试：
 * 无本地 key+已连接 → 主推「为当前钱包签发新 key」+ 名下 N 个 key 信息行；
 * 粘贴 key validate 通过（存入启用）/失败两路；本机 key 绑定钱包≠连接钱包黄条；
 * validate 请求体断言；「本机没有 API key」旧文案残留扫描。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ApiKeySection, TrialCallSection } from "../src/pages/ConsumerWorkbench";
import { WalletProvider, useWallet } from "../src/state/WalletContext";
import type { Eip1193Provider } from "../src/chain/injected";

const MY = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8";
const OTHER = "0xAdeE6D874ceC4b8Dba0C969585bd2E593ddfba3D";
const KEY = "cck_live_abcd1234efgh5678";

let calls: Array<{ url: string; method: string; body?: string }>;
let validateOk: boolean;
let validateWallet: string;

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

function installFetch(walletKeyCount = 2) {
  calls = [];
  validateOk = true;
  validateWallet = MY;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? String(init.body) : undefined });
    if (url.startsWith("/api/core/apikeys?wallet=")) {
      const wallet = decodeURIComponent(url.split("wallet=")[1]!.toLowerCase());
      return jsonResponse({
        keys: Array.from({ length: walletKeyCount }, (_, i) => ({
          key_id: `key_${i}`,
          consumer_wallet: wallet,
          quota_raw: null,
          status: "active",
          created_at: "2026-10-01 00:00:00",
        })),
      });
    }
    if (url === "/api/core/apikeys" && method === "POST") {
      const body = JSON.parse(String(init?.body)) as { consumer_wallet: string };
      return jsonResponse({ key_id: "key_new", api_key: KEY, consumer_wallet: body.consumer_wallet, quota_raw: null, created_at: "t" });
    }
    if (url === "/api/core/internal/apikeys/validate") {
      if (!validateOk) return jsonResponse({ error: "unauthorized", detail: "api key 不存在", code: "apikey_unknown" }, 401);
      return jsonResponse({ key_id: "key_x", consumer_wallet: validateWallet, quota_raw: null, status: "active" });
    }
    return jsonResponse({ error: "not_mocked", detail: url }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
}

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

async function renderKeySectionConnected() {
  render(
    <WalletProvider>
      <ApiKeySection />
      <ConnectProbe />
    </WalletProvider>
  );
  announce();
  fireEvent.click(screen.getByTestId("probe-connect"));
}

beforeEach(() => {
  cleanup();
  sessionStorage.clear();
  localStorage.clear();
  delete (window as { ethereum?: unknown }).ethereum;
});
afterEach(() => vi.unstubAllGlobals());

describe("换机：无本地 key + 已连接", () => {
  it("主推「为当前钱包签发新 key」+ 名下 N 个 key 信息行（解释不可找回）", async () => {
    installFetch(3);
    await renderKeySectionConnected();
    expect(await screen.findByRole("button", { name: /为当前钱包签发新 key/ })).toBeTruthy();
    expect(document.body.textContent).toContain("你的钱包名下已有 3 个 key（明文只在签发时显示一次）");
    expect(screen.getByText(/换机器不用慌/)).toBeTruthy();
    expect(screen.queryByText(/本机没有 API key/)).toBeNull();
  });

  it("签发 → 明文一次性展示 → 我已保存 → 自动实检并存入本机", async () => {
    installFetch(0);
    await renderKeySectionConnected();
    fireEvent.click(await screen.findByRole("button", { name: /为当前钱包签发新 key（/ }));
    expect(await screen.findByText(/明文 key 仅此一次展示/)).toBeTruthy();
    fireEvent.click(screen.getByText("我已保存（存入本机并实检）"));
    await waitFor(() => expect(screen.getByText(/已确认保存并实检通过/)).toBeTruthy());
    expect(localStorage.getItem("coincall.apikey")).toBe(KEY);
    // 实检请求体断言
    const v = calls.find((c) => c.url === "/api/core/internal/apikeys/validate")!;
    expect(JSON.parse(v.body!)).toEqual({ api_key: KEY });
  });
});

describe("粘贴已有 key（换机路径二）", () => {
  it("validate 通过 → ✓ 有效·绑定钱包 → 存入本机并启用", async () => {
    installFetch();
    await renderKeySectionConnected();
    fireEvent.change(await screen.findByLabelText("粘贴 API key"), { target: { value: KEY } });
    fireEvent.click(screen.getByRole("button", { name: "验证并启用" }));
    expect(await screen.findByText(/✓ 有效 · 绑定钱包/)).toBeTruthy();
    const okBox = screen.getAllByText(/✓ 有效 · 绑定钱包/)[0]!.closest(".alert")!;
    expect(okBox.textContent).toContain("=当前连接钱包，可直接支付");
    fireEvent.click(screen.getByText("存入本机并启用"));
    expect(localStorage.getItem("coincall.apikey")).toBe(KEY);
    expect(await screen.findByText(/本机已保存/)).toBeTruthy();
  });

  it("validate 失败（401 apikey_unknown）→ 红条提示复制完整明文或重签，不保存", async () => {
    installFetch();
    validateOk = false;
    await renderKeySectionConnected();
    fireEvent.change(await screen.findByLabelText("粘贴 API key"), { target: { value: "cck_wrong_key!" } });
    fireEvent.click(screen.getByRole("button", { name: "验证并启用" }));
    expect(await screen.findByText(/验证未通过/)).toBeTruthy();
    expect(localStorage.getItem("coincall.apikey")).toBeNull();
  });

  it("粘贴的 key 绑定他人钱包 → 警示「≠当前连接钱包，支付会 402」", async () => {
    installFetch();
    validateWallet = OTHER;
    await renderKeySectionConnected();
    fireEvent.change(await screen.findByLabelText("粘贴 API key"), { target: { value: KEY } });
    fireEvent.click(screen.getByRole("button", { name: "验证并启用" }));
    expect(await screen.findByText(/≠当前连接钱包，支付会 402/)).toBeTruthy();
  });
});

describe("本机已保存（路径一）", () => {
  it("掩码展示 + 实检 ✓；绑定钱包≠连接钱包黄条警示", async () => {
    localStorage.setItem("coincall.apikey", KEY);
    installFetch();
    validateWallet = OTHER; // 注意在 installFetch 之后设置（其会重置默认）
    await renderKeySectionConnected();
    expect(await screen.findByText(/本机已保存/)).toBeTruthy();
    expect(screen.getByText("cck_…5678")).toBeTruthy(); // 掩码
    fireEvent.click(screen.getByRole("button", { name: "服务端实检" }));
    const warn = await screen.findByText(/签名人不符会被网关 402/);
    expect(warn.closest(".alert.warn")!.textContent).toContain("该 key 绑定的钱包 ≠ 当前连接钱包");
    expect(screen.getByText(/签名人不符会被网关 402/)).toBeTruthy();
  });
});

describe("试用调用 key 未就绪引导", () => {
  it("无 key 时提示三路径（不再说「本机没有 API key」）；保存后自动解除", async () => {
    const catalog = {
      services: [
        {
          service_id: "svc_t",
          manifest: {
            service_id: "svc_t", name: "T", description: "", version: "1",
            provider: { agent_id: 1, wallet: MY, display_name: "P" },
            endpoint: { type: "internal", url: null, timeout_ms: 30000 },
            pricing: { token: "USDT", model: "per_call", amount: "0.01", amount_raw: "10000" },
            chain: { network: 968 }, input_schema: { type: "object" }, output_schema: { type: "object" },
            status: "active", created_at: "t",
          },
          status: "active", manifest_hash: "x",
        },
      ],
      count: 1,
    };
    calls = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
      const url = String(input);
      if (url === "/api/core/catalog") return jsonResponse(catalog);
      return jsonResponse({ error: "x" }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <WalletProvider>
        <TrialCallSection />
        <ConnectProbe />
      </WalletProvider>
    );
    announce();
    fireEvent.click(screen.getByTestId("probe-connect"));
    await waitFor(() => expect(screen.getByText(/— 选择服务/)).toBeTruthy());
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "svc_t" } });
    expect(await screen.findByText(/API key 未就绪/)).toBeTruthy();
    expect(screen.getByText(/粘贴原机器的 key/)).toBeTruthy();
    expect(screen.getByText(/为当前钱包签发新 key/)).toBeTruthy();
    // 事件化：存入 key 后警告自动消失
    localStorage.setItem("coincall.apikey", KEY);
    window.dispatchEvent(new CustomEvent("coincall:apikey"));
    await waitFor(() => expect(screen.queryByText(/API key 未就绪/)).toBeNull());
  });
});

describe("旧文案残留扫描", () => {
  it("纯 localStorage 视角文案清零", () => {
    const content = readFileSync(join(__dirname, "..", "src", "pages", "ConsumerWorkbench.tsx"), "utf-8");
    expect(content).not.toContain("本机没有 API key");
  });
});
