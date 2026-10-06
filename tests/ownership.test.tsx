// @vitest-environment jsdom
/**
 * 「我的服务」所有权测试（连接钱包 ∪ 认领身份）：
 * 未连接=只给引导无列表（绝无“显示全部”兜底）；双条件过滤（钱包命中/agent_id 命中/都不中 → 2 行+徽标）；
 * 冒用行（agent_id 命中但收款钱包≠我）橙徽标；internal 全量预取只发 mine 数量次请求；残留扫描。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { ManageStep } from "../src/pages/ProviderWorkbench";
import { WalletProvider, useWallet } from "../src/state/WalletContext";
import type { Eip1193Provider } from "../src/chain/injected";
import type { Catalog } from "../src/api/core";

const MY = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8";
const OTHER = "0xAdeE6D874ceC4b8Dba0C969585bd2E593ddfba3D";

let calls: Array<{ url: string; method: string }>;

function svc(serviceId: string, wallet: string, agentId: number) {
  return {
    service_id: serviceId,
    manifest: {
      service_id: serviceId,
      name: `Svc ${serviceId}`,
      description: "",
      version: "1.0.0",
      provider: { agent_id: agentId, wallet, display_name: "P" },
      endpoint: { type: "http_json", url: null, timeout_ms: 30000, method: "GET" },
      pricing: { token: "USDT", model: "per_call", amount: "0.01", amount_raw: "10000" },
      chain: { network: 968 },
      input_schema: { type: "object" },
      output_schema: { type: "object" },
      status: "active",
      created_at: "t",
    },
    status: "active",
    manifest_hash: "sha256:x",
  };
}

/** 三服务：钱包命中 / agent_id 命中（钱包他人=冒用形态可切换）/ 都不中。 */
function catalog(): Catalog {
  return {
    services: [
      svc("svc_wallet_hit", MY, 900), // 收款所有权
      svc("svc_identity_hit", OTHER, 169), // 身份所有权（收款钱包≠我 → 冒用橙徽标形态）
      svc("svc_foreign", OTHER, 777), // 都不中 → 不显示
    ],
    count: 3,
  } as unknown as Catalog;
}

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

function installFetch(providersClaim: Array<{ agent_id: number; claim_wallet: string | null }>) {
  calls = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    calls.push({ url, method: init?.method ?? "GET" });
    if (url === "/api/core/catalog") return jsonResponse(catalog());
    if (url === "/api/core/providers") return jsonResponse({ providers: providersClaim.map((p) => ({ ...p, display_name: "P", wallet: p.claim_wallet ?? OTHER, created_at: "t" })) });
    if (/\/api\/core\/internal\/manifests\/[^/]+$/.test(url)) {
      const sid = url.split("/").pop()!;
      const m = catalog().services.find((s) => s.service_id === sid)!.manifest;
      const full = JSON.parse(JSON.stringify(m));
      full.endpoint.url = `https://upstream.example/${sid}`;
      return jsonResponse({ service_id: sid, manifest: full });
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

beforeEach(() => {
  cleanup();
  sessionStorage.clear();
  delete (window as { ethereum?: unknown }).ethereum;
});
afterEach(() => vi.unstubAllGlobals());

async function renderConnected(claimedAgentId: number | null) {
  render(
    <WalletProvider>
      <ManageStep claimedAgentId={claimedAgentId} onNext={vi.fn()} onBack={vi.fn()} />
      <ConnectProbe />
    </WalletProvider>
  );
  announce();
  fireEvent.click(screen.getByTestId("probe-connect"));
  await waitFor(() => expect(document.querySelectorAll("table tbody tr").length).toBeGreaterThan(0));
}

describe("未连接前置", () => {
  it("只显示内联连接引导，无列表、无「显示全部」", () => {
    installFetch([]);
    render(
      <WalletProvider>
        <ManageStep claimedAgentId={null} onNext={vi.fn()} onBack={vi.fn()} />
      </WalletProvider>
    );
    expect(screen.getByRole("button", { name: "连接钱包" })).toBeTruthy();
    expect(screen.getByText(/服务收款钱包是你的、或身份由你认领/)).toBeTruthy();
    expect(document.querySelector("table")).toBeNull();
    expect(document.body.textContent).not.toContain("显示全部");
  });
});

describe("双条件过滤与徽标", () => {
  it("只显示 2 行命中（钱包命中绿徽标；身份命中蓝徽标+冒用橙徽标）；都不中的不显示", async () => {
    installFetch([{ agent_id: 169, claim_wallet: MY }]);
    await renderConnected(null);

    const rows = document.querySelectorAll("table tbody tr");
    expect(rows.length).toBe(2); // wallet_hit + identity_hit；foreign 不显示
    const text = document.body.textContent ?? "";
    expect(text).toContain("svc_wallet_hit");
    expect(text).toContain("svc_identity_hit");
    expect(text).not.toContain("svc_foreign");
    // 徽标
    expect(screen.getByText("服务收款钱包=你")).toBeTruthy();
    expect(screen.getByText("身份 #169=你认领")).toBeTruthy();
    expect(screen.getByText("⚠ 此服务收款钱包非你，请核实")).toBeTruthy(); // 冒用：身份命中但钱包他人
  });

  it("向导①认领上下文加速：claimedAgentId 纳入（providers 未返回也命中）", async () => {
    installFetch([]); // providers 空——权威源无认领记录
    await renderConnected(169); // 但①步刚认领 169 → 上下文兜底纳入
    const text = document.body.textContent ?? "";
    expect(text).toContain("svc_identity_hit");
    expect(screen.getByText("身份 #169=你认领")).toBeTruthy();
  });

  it("双命中行（agent_id=我认领且收款钱包=我）双徽标、无警示", async () => {
    // 构造：wallet_hit 行同时 agent_id=169 且认领 169
    const c = catalog();
    (c.services[0]!.manifest as { provider: { agent_id: number } }).provider.agent_id = 169;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = String(input);
      calls.push({ url, method: init?.method ?? "GET" });
      if (url === "/api/core/catalog") return jsonResponse(c);
      if (url === "/api/core/providers") return jsonResponse({ providers: [{ agent_id: 169, display_name: "P", wallet: MY, claim_wallet: MY, created_at: "t" }] });
      if (/\/api\/core\/internal\/manifests\/[^/]+$/.test(url)) {
        const sid = url.split("/").pop()!;
        const m = c.services.find((s) => s.service_id === sid)!.manifest;
        const full = JSON.parse(JSON.stringify(m));
        full.endpoint.url = `https://upstream.example/${sid}`;
        return jsonResponse({ service_id: sid, manifest: full });
      }
      return jsonResponse({ error: "x" }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <WalletProvider>
        <ManageStep claimedAgentId={null} onNext={vi.fn()} onBack={vi.fn()} />
        <ConnectProbe />
      </WalletProvider>
    );
    announce();
    fireEvent.click(screen.getByTestId("probe-connect"));
    await waitFor(() => expect(document.querySelectorAll("table tbody tr").length).toBe(2));
    expect(screen.getByText("服务收款钱包=你")).toBeTruthy();
    expect(screen.getAllByText("身份 #169=你认领").length).toBe(2); // 两行都命中身份（169 认领）
    // 冒用警示只出现在收款钱包≠我的那行（svc_identity_hit）；双命中行（svc_wallet_hit）无警示
    expect(screen.getAllByText(/此服务收款钱包非你/).length).toBe(1);
  });

  it("internal 全量预取只发 mine 数量次（2 次），不为他人服务取数", async () => {
    installFetch([{ agent_id: 169, claim_wallet: MY }]);
    await renderConnected(null);
    await waitFor(() => {
      const internalCalls = calls.filter((c) => /\/internal\/manifests\//.test(c.url));
      expect(internalCalls.length).toBe(2); // wallet_hit + identity_hit
      expect(internalCalls.some((c) => c.url.includes("svc_foreign"))).toBe(false);
    });
  });
});

describe("「显示全部」残留扫描", () => {
  it("ProviderWorkbench 无显示全部兜底文案", () => {
    const content = readFileSync(join(__dirname, "..", "src", "pages", "ProviderWorkbench.tsx"), "utf-8");
    expect(content).not.toContain("显示全部");
  });
});
