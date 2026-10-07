// @vitest-environment jsdom
/**
 * 消费端「① 授权额度 → ② 支付调用」一条动线测试：
 * 授权充足→①折叠直通②；不足→①展开默认=单价×10、去钱包授权成功后自动进②（折叠）；
 * 402 insufficient_allowance→内联展开①并预置金额（无跨区跳转按钮）；资金状态面板无 approve 操作；
 * 「去授权」旧按钮文案清零；历史「含授权」标记。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { FundsSection, TrialCallSection } from "../src/pages/ConsumerWorkbench";
import { Wallet as EthersWallet } from "ethers";
import { WalletProvider, useWallet } from "../src/state/WalletContext";
import type { Eip1193Provider } from "../src/chain/injected";
import { fetchAllowance, fetchTokenBalance } from "../src/chain/rpc";
import { waitForInjectedReceipt } from "../src/chain/injected";

const MY = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8";
const ANVIL1_PK = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d";
const PRICE_RAW = 10_000n; // 0.01 USDT

// ethers RPC 在测试环境绕过 global fetch——模块级 mock 三数读取
const balanceMock = vi.mocked(fetchTokenBalance);
const allowanceMock = vi.mocked(fetchAllowance);
vi.mock("../src/chain/rpc", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../src/chain/rpc")>();
  return { ...mod, fetchTokenBalance: vi.fn(), fetchAllowance: vi.fn() };
});
// 注入路径的回执等待走真 RPC——mock 成即时确认（eth_sendTransaction 由钱包 mock 返回合法哈希）
const receiptMock = vi.mocked(waitForInjectedReceipt);
vi.mock("../src/chain/injected", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../src/chain/injected")>();
  return { ...mod, waitForInjectedReceipt: vi.fn(async (hash: string) => ({ status: 1, hash, blockNumber: 1, index: 0 })) };
});

const CATALOG = {
  services: [
    {
      service_id: "svc_t",
      manifest: {
        service_id: "svc_t",
        name: "Translate",
        description: "",
        version: "1.0.0",
        provider: { agent_id: 169, wallet: MY, display_name: "P" },
        endpoint: { type: "internal", url: null, timeout_ms: 30000 },
        pricing: { token: "USDT", model: "per_call", amount: "0.01", amount_raw: PRICE_RAW.toString() },
        chain: { network: 968 },
        input_schema: { type: "object", properties: { text: { type: "string" } }, required: ["text"] },
        output_schema: { type: "object" },
        status: "active",
        created_at: "t",
      },
      status: "active",
      manifest_hash: "sha256:x",
    },
  ],
  count: 1,
};

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

const signerWallet = new EthersWallet(ANVIL1_PK);

function mockWalletProvider(): Eip1193Provider {
  return {
    request: async ({ method, params }: { method: string; params?: unknown[] | object }) => {
      if (method === "eth_requestAccounts" || method === "eth_accounts") return [MY];
      if (method === "eth_chainId") return "0x3c8";
      if (method === "eth_estimateGas") return "0x10000";
      if (method === "eth_sendTransaction") return "0x" + "ab".repeat(32);
      if (method === "eth_signTypedData_v4") {
        const [, payloadJson] = params as [string, string];
        const payload = JSON.parse(payloadJson) as { domain: unknown; types: Record<string, Record<string, unknown>[] & { EIP712Domain?: unknown }>; message: Record<string, unknown> };
        const { EIP712Domain: _omit, ...typesOnly } = payload.types;
        void _omit;
        return await signerWallet.signTypedData(payload.domain as never, typesOnly as never, payload.message as never);
      }
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

let calls: Array<{ url: string; method: string; headers?: Record<string, string> }>;

function installFetch(gatewayStatus = 200) {
  calls = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    calls.push({ url, method: init?.method ?? "GET" });
    if (url === "/api/core/catalog") return jsonResponse(CATALOG);
    if (url.endsWith("/api/gw/call/svc_t")) {
      if (gatewayStatus === 402) {
        return jsonResponse({ error: "payment_required", detail: "授权不足", code: "insufficient_allowance", service_id: "svc_t", pricing: { amount: "0.01", amount_raw: "10000", token: "USDT" }, wallet_balance_raw: "1000000", payment: { scheme: "erc3009-vault", header: "X-PAYMENT", domain: {}, approve_to: "0x" }, trace_id: "t" }, 402);
      }
      return new Response(JSON.stringify({ ok: true, translated: "hi" }), { status: 200, headers: { "X-Receipt-Id": "rcp_1", "X-Charged-Raw": "10000" } });
    }
    return jsonResponse({ error: "not_mocked", detail: url }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
}

async function renderTrialConnected() {
  localStorage.setItem("coincall.apikey", "ck_test_x");
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
}

beforeEach(() => {
  cleanup();
  sessionStorage.clear();
  localStorage.clear();
  balanceMock.mockReset().mockResolvedValue(1_000_000n);
  delete (window as { ethereum?: unknown }).ethereum;
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("① 授权额度 → ② 支付调用（同一动线）", () => {
  it("授权充足：①折叠为「✓ 已授权，可直接支付」一行，直通②（付费按钮就绪）", async () => {
    allowanceMock.mockResolvedValue(500_000n); // 0.5 USDT >> 0.01
    installFetch();
    await renderTrialConnected();
    const collapsed = await screen.findByText(/✓ 已授权，可直接支付/);
    expect(collapsed.textContent).toContain("0.5"); // 折叠态展示可用授权（0.5 USDT）
    expect(screen.queryByRole("button", { name: "去钱包授权" })).toBeNull();
    expect(screen.getByRole("button", { name: /^付费调用/ })).toBeTruthy();
  });

  it("授权不足：①展开，金额默认=单价×10（0.1），「去钱包授权」成功后自动折叠进②", async () => {
    allowanceMock.mockResolvedValueOnce(0n).mockResolvedValue(100_000n); // 授权前 0 → 成功后 0.1
    installFetch();
    await renderTrialConnected();
    expect(await screen.findByRole("button", { name: "去钱包授权" })).toBeTruthy();
    expect((screen.getByLabelText("授权金额") as HTMLInputElement).value).toBe("0.1"); // 0.01×10
    expect(screen.getByText(/一次授权可供多次调用/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "去钱包授权" }));
    await waitFor(() => expect(screen.getByText(/✓ 授权已上链——进入 ② 支付调用/)).toBeTruthy());
    expect(receiptMock).toHaveBeenCalled(); // 注入回执等待走 mock（无网化）
    await waitFor(() => expect(screen.getByText(/✓ 已授权，可直接支付/)).toBeTruthy()); // 自动折叠
    expect(localStorage.getItem("coincall.approvedOnce")).toBe("1"); // 本地记忆
  });

  it("402 insufficient_allowance：无「去授权」跳转按钮，①内联展开且金额预置覆盖本单", async () => {
    // 场景：授权充足但服务端影子闸门判定不足（构造：本地 allowance 高、网关 402）
    allowanceMock.mockResolvedValue(500_000n);
    installFetch(402);
    await renderTrialConnected();
    await waitFor(() => expect(screen.getByText(/✓ 已授权，可直接支付/)).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: /^付费调用/ }));
    await new Promise((r) => setTimeout(r, 300));
    console.log("CALLS:", JSON.stringify(calls.map((c) => c.url)));
    console.log("SPIN:", document.body.textContent?.includes("等待钱包签名"));
    console.log("ERRMSG:", (document.querySelector(".alert.err > div:first-child")?.textContent ?? "none").slice(0, 120));
    console.log("BTN_DISABLED:", (screen.getByRole("button", { name: /^付费调用/ }) as HTMLButtonElement).disabled);
    await waitFor(() => expect(screen.getByText(/402 ·/)).toBeTruthy());
    // 无旧跳转按钮；①已展开且金额预置（0.1 = 单价×10，覆盖本单）
    expect(screen.queryByRole("button", { name: /去授权 / })).toBeNull();
    expect(screen.getByText(/已在上方「① 授权额度」展开并预置金额/)).toBeTruthy();
    expect((screen.getByLabelText("授权金额") as HTMLInputElement).value).toBe("0.1");
  });
});

describe("资金状态面板瘦身", () => {
  it("只读三数+水龙头；无任何授权操作", async () => {
    balanceMock.mockResolvedValue(2_000_000n);
    allowanceMock.mockResolvedValue(500_000n);
    installFetch();
    render(
      <WalletProvider>
        <FundsSection />
        <ConnectProbe />
      </WalletProvider>
    );
    announce();
    fireEvent.click(screen.getByTestId("probe-connect"));
    await waitFor(() => expect(screen.getByText(/资金状态面板/)).toBeTruthy());
    await waitFor(() => expect(screen.getByText("获取 USDT")).toBeTruthy());
    const card = screen.getByText(/资金状态面板/).closest(".card")!;
    expect(card.textContent).not.toMatch(/授权滑条|去钱包授权|确认授权/);
    expect(card.querySelectorAll("button").length).toBeLessThanOrEqual(4); // 刷新/复制/去水龙头/刷新余额——无 approve 动作
  });
});

describe("残留扫描", () => {
  it("「去授权」旧按钮文案清零；跨区跳转事件清零", () => {
    const content = readFileSync(join(__dirname, "..", "src", "pages", "ConsumerWorkbench.tsx"), "utf-8");
    expect(content).not.toContain("去授权 ");
    expect(content).not.toContain("coincall:goto-approve");
  });
});
