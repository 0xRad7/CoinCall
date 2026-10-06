// @vitest-environment jsdom
/**
 * 调用后反馈测试：
 * 历史记录存 X-Receipt-Sig-Ed25519/X-Receipt-Ts（ TrialCall 成功路径断言）；
 * 评价弹层组装收据五元组+签名、提交成功/409/401 文案；
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Wallet } from "ethers";
import ConsumerWorkbench from "../src/pages/ConsumerWorkbench";
import { WalletProvider, useWallet } from "../src/state/WalletContext";
import type { Eip1193Provider } from "../src/chain/injected";
import type { Catalog } from "../src/api/core";

const MY = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8";
const ANVIL1_PK = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d";

vi.mock("../src/chain/rpc", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../src/chain/rpc")>();
  return { ...mod, fetchTokenBalance: vi.fn().mockResolvedValue(1_000_000n), fetchAllowance: vi.fn().mockResolvedValue(500_000n) };
});

const CATALOG: Catalog = {
  services: [
    {
      service_id: "svc_t",
      manifest: {
        service_id: "svc_t", name: "Translate", description: "", version: "1",
        provider: { agent_id: 169, wallet: MY, display_name: "P" },
        endpoint: { type: "internal", url: null, timeout_ms: 30000 },
        pricing: { token: "USDT", model: "per_call", amount: "0.01", amount_raw: "10000" },
        chain: { network: 968 }, input_schema: { type: "object" }, output_schema: { type: "object" },
        status: "active", created_at: "t",
      },
      status: "active", manifest_hash: "x",
    },
  ],
  count: 1,
} as unknown as Catalog;

let calls: Array<{ url: string; method: string; body?: string; headers?: Record<string, string> }>;
let feedbackStatus = 201;

function jsonResponse(obj: unknown, status = 200, extraHeaders: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json", ...extraHeaders } });
}

const signerWallet = new Wallet(ANVIL1_PK);
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
        const { EIP712Domain: _o, ...typesOnly } = payload.types;
        void _o;
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

function installFetch() {
  calls = [];
  feedbackStatus = 201;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const headers: Record<string, string> = {};
    new Headers(init?.headers).forEach((v, k) => (headers[k] = v));
    calls.push({ url, method, body: init?.body ? String(init.body) : undefined, headers });
    if (url === "/api/core/catalog") return jsonResponse(CATALOG);
    if (url === "/api/core/apikeys?wallet=" + MY) return jsonResponse({ keys: [] });
    if (url.endsWith("/api/gw/call/svc_t")) {
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "X-Receipt-Id": "rcp_test123",
          "X-Charged-Raw": "10000",
          "X-Receipt-Sig": "sig-old",
          "X-Receipt-Sig-Ed25519": "ed25519abcdef",
          "X-Receipt-Ts": "1791300000",
        },
      });
    }
    if (url === "/api/core/feedback" && method === "POST") {
      if (feedbackStatus === 409) return jsonResponse({ error: "conflict", detail: "该收据已评价", code: "duplicate" }, 409);
      if (feedbackStatus === 401) return jsonResponse({ error: "unauthorized", detail: "收据签名无效", code: "receipt_invalid" }, 401);
      return jsonResponse({ ok: true }, 201);
    }
    if (url.startsWith("/api/core/decision/")) return jsonResponse({ categories: [], counts: {}, total_active: 0 });
    return jsonResponse({ error: "not_mocked", detail: url }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
}

async function payCall() {
  render(
    <WalletProvider>
      <ConsumerWorkbench />
      <ConnectProbe />
    </WalletProvider>
  );
  localStorage.setItem("coincall.apikey", "ck_test_x");
  window.dispatchEvent(new CustomEvent("coincall:apikey"));
  announce();
  fireEvent.click(screen.getByTestId("probe-connect"));
  await waitFor(() => expect(screen.getByText(/— 选择服务/)).toBeTruthy());
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "svc_t" } });
  await waitFor(() => expect(screen.getByText(/✓ 已授权，可直接支付/)).toBeTruthy());
  fireEvent.click(screen.getByRole("button", { name: /^付费调用/ }));
  await waitFor(() => expect(screen.getByText(/✓ 调用成功并已计费/)).toBeTruthy());
  // 历史已记录
  await waitFor(() => expect(screen.getByText(/成功 · 0\.01/)).toBeTruthy());
}

beforeEach(() => {
  cleanup();
  sessionStorage.clear();
  localStorage.clear();
  installFetch();
  delete (window as { ethereum?: unknown }).ethereum;
});
afterEach(() => vi.unstubAllGlobals());

describe("调用后反馈", () => {
  it("付费调用历史存新收据头（Sig-Ed25519/Ts）→ 点评价弹层 → 提交五元组+签名 → 已评价徽章", async () => {
    await payCall();

    // 历史条目落盘断言
    const hist = JSON.parse(localStorage.getItem("coincall.callHistory") ?? "[]") as Array<{ receiptSigEd?: string; receiptTs?: number }>;
    expect(hist[0]!.receiptSigEd).toBe("ed25519abcdef");
    expect(hist[0]!.receiptTs).toBe(1_791_300_000);

    // 评价弹层
    fireEvent.click(screen.getByRole("button", { name: "评价" }));
    expect(await screen.findByRole("dialog", { name: "评价服务" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "5 星" }));
    fireEvent.change(screen.getByLabelText("评价内容"), { target: { value: "翻译不错" } });
    fireEvent.click(screen.getByRole("button", { name: "提交评价" }));

    await waitFor(() => expect(screen.getByText("已评价")).toBeTruthy());
    // 提交体断言：五元组（含 status=success 与 ts）+ 签名 + 星级 + 评论
    const fb = calls.find((c) => c.url === "/api/core/feedback")!;
    expect(JSON.parse(fb.body!)).toEqual({
      service_id: "svc_t",
      receipt: { receipt_id: "rcp_test123", service_id: "svc_t", amount_raw: "10000", status: "success", ts: 1_791_300_000, receipt_sig_hex: "ed25519abcdef" },
      rating: 5,
      comment: "翻译不错",
    });
  });

  it("409：该次调用已评价过（人话文案）", async () => {
    feedbackStatus = 409;
    await payCall();
    fireEvent.click(screen.getByRole("button", { name: "评价" }));
    fireEvent.click(await screen.findByRole("button", { name: "3 星" }));
    fireEvent.click(screen.getByRole("button", { name: "提交评价" }));
    expect(await screen.findByText(/该次调用已评价过/)).toBeTruthy();
    expect(screen.queryByText("已评价")).toBeNull(); // 未标记
  });

  it("401：收据签名无效（人话文案）", async () => {
    feedbackStatus = 401;
    await payCall();
    fireEvent.click(screen.getByRole("button", { name: "评价" }));
    fireEvent.click(await screen.findByRole("button", { name: "1 星" }));
    fireEvent.click(screen.getByRole("button", { name: "提交评价" }));
    expect(await screen.findByText(/收据签名验证未通过/)).toBeTruthy();
  });
});

describe("历史记录新头断言（源码级）", () => {
  it("TrialCall 成功路径 appendHistory 携带 receiptSigEd/receiptTs（防回归）", () => {
    const src = readFileSync(join(__dirname, "..", "src", "pages", "ConsumerWorkbench.tsx"), "utf-8");
    expect(src).toContain("receiptSigEd: outcome.receipt.receiptSigEd");
    expect(src).toContain("receiptTs: outcome.receipt.receiptTs");
  });
});
