// @vitest-environment jsdom
/**
 * 认领身份（认证先行）四态测试：未连接只给内联连接入口；a/b/c/d 四态渲染各自正确；
 * c 态全链（绑定签名 mock → 自动登记）；d 态无操作入口；发布默认=认领钱包；旧独立绑定步骤残留扫描。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Wallet } from "ethers";
import { ClaimStep, PublishStep } from "../src/pages/ProviderWorkbench";
import { WalletProvider, useWallet } from "../src/state/WalletContext";
import type { Eip1193Provider } from "../src/chain/injected";
import type { ClaimState } from "../src/api/core";

const ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"; // anvil#1（我的连接钱包）
const OTHER = "0xAdeE6D874ceC4b8Dba0C969585bd2E593ddfba3D";
const CUSTODIAN = "0xC37fFE97B4D2C3D0187B1dDEDF273E52A461B63a";
const ANVIL1_PK = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d";
const TX = "0x" + "ab".repeat(32);

let calls: Array<{ url: string; method: string; body?: string }>;
let claimStateResp: ClaimState;

function cs(over: Partial<ClaimState>): ClaimState {
  return { agent_id: 169, identity_found: true, agent_wallet: CUSTODIAN, claimed_by_wallet: null, platform_custodian: CUSTODIAN, ...over };
}

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

function installFetch() {
  calls = [];
  claimStateResp = cs({});
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? String(init.body) : undefined });
    if (/\/api\/core\/providers\/\d+\/claim-state$/.test(url)) return jsonResponse(claimStateResp);
    if (url === "/api/core/providers" && method === "POST") {
      const body = JSON.parse(String(init?.body)) as { agent_id: number; claim_wallet: string };
      if (body.claim_wallet.toLowerCase() !== ADDR.toLowerCase()) {
        return jsonResponse({ error: "claim_requires_binding", detail: "链上 agentWallet 与认领钱包不等", code: "claim_requires_binding" }, 422);
      }
      return jsonResponse({ agent_id: body.agent_id, display_name: (JSON.parse(String(init?.body)) as { display_name: string }).display_name, wallet: body.claim_wallet, created_at: "t" });
    }
    if (/\/api\/chain\/api\/v1\/agent-identity\/\d+$/.test(url)) {
      return jsonResponse({ token_id: 169, owner: CUSTODIAN, token_uri: "u", agent_wallet: CUSTODIAN, metadata: {} });
    }
    if (/\/api\/chain\/api\/v1\/agent-identity\/\d+\/wallet$/.test(url)) return jsonResponse({ tx_hash: TX });
    return jsonResponse({ error: "not_mocked", detail: url }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
}

function mockWalletProvider(): Eip1193Provider {
  const wallet = new Wallet(ANVIL1_PK);
  return {
    request: async ({ method, params }: { method: string; params?: unknown[] | object }) => {
      if (method === "eth_requestAccounts" || method === "eth_accounts") return [ADDR];
      if (method === "eth_chainId") return "0x3c8";
      if (method === "eth_signTypedData_v4") {
        const [, payloadJson] = params as [string, string];
        const payload = JSON.parse(payloadJson) as { domain: unknown; types: Record<string, Record<string, unknown>[] & { EIP712Domain?: unknown }>; message: Record<string, unknown> };
        const { EIP712Domain: _omit, ...typesOnly } = payload.types;
        void _omit;
        return await wallet.signTypedData(payload.domain as never, typesOnly as never, payload.message as never);
      }
      return null;
    },
  };
}

function announce(provider: unknown) {
  window.dispatchEvent(
    new CustomEvent("eip6963:announceProvider", { detail: { info: { uuid: "com.okx.wallet", name: "OKX Wallet", icon: "", rdns: "com.okx.wallet" }, provider } })
  );
}

function ConnectProbe() {
  const w = useWallet();
  return (
    <div>
      <button onClick={() => void w.connect()} data-testid="probe-connect">
        连接钱包
      </button>
      <div data-testid="wstate">{w.address ?? "未连接"}</div>
    </div>
  );
}

async function renderClaimConnected() {
  render(
    <WalletProvider>
      <ClaimStep initial={null} onNext={vi.fn()} />
      <ConnectProbe />
    </WalletProvider>
  );
  announce(mockWalletProvider());
  fireEvent.click(screen.getByTestId("probe-connect"));
  await waitFor(() => expect(screen.getByTestId("wstate").textContent).toBe(ADDR));
}

async function fillAgentId() {
  fireEvent.change(screen.getByPlaceholderText("例如 169"), { target: { value: "169" } });
}

beforeEach(() => {
  cleanup();
  sessionStorage.clear();
  installFetch();
  delete (window as { ethereum?: unknown }).ethereum;
});
afterEach(() => vi.unstubAllGlobals());

describe("前置：未连接", () => {
  it("只有内联连接入口，不出现表单", () => {
    render(
      <WalletProvider>
        <ClaimStep initial={null} onNext={vi.fn()} />
      </WalletProvider>
    );
    expect(screen.getByRole("button", { name: "连接钱包" })).toBeTruthy();
    expect(screen.queryByPlaceholderText("例如 169")).toBeNull();
    expect(screen.getByText(/认领 = 把链上身份钱包绑定为你当前连接的钱包 \+ 平台登记/)).toBeTruthy();
  });
});

describe("四态渲染（mock claim-state）", () => {
  it("a 态：身份不存在 → 红条 + 注册闭环入口", async () => {
    claimStateResp = cs({ identity_found: false, agent_wallet: null });
    await renderClaimConnected();
    await fillAgentId();
    expect(await screen.findByText(/身份不存在/)).toBeTruthy();
    expect(screen.getByText(/没有身份？↓ 注册一个/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "提交认领" })).toBeNull();
    expect(screen.queryByRole("button", { name: "绑定我的钱包并认领" })).toBeNull();
  });

  it("b 态：agent_wallet=我的地址 → 绿「可直接认领」+ 展示名 + 提交认领", async () => {
    claimStateResp = cs({ agent_wallet: ADDR });
    await renderClaimConnected();
    await fillAgentId();
    expect(await screen.findByText(/身份钱包已是你的地址/)).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText(/My Translate Booth/), { target: { value: "My Booth" } });
    expect(screen.getByRole("button", { name: "提交认领" })).toBeTruthy();
  });

  it("b 态变体：claimed_by_wallet=我的地址 → 「已由你认领」", async () => {
    claimStateResp = cs({ claimed_by_wallet: ADDR });
    await renderClaimConnected();
    await fillAgentId();
    expect(await screen.findByText(/已由你认领/)).toBeTruthy();
  });

  it("c 态：托管+未认领 → 黄「需先绑定」+「绑定我的钱包并认领」主按钮", async () => {
    await renderClaimConnected();
    await fillAgentId();
    expect(await screen.findByText(/需先绑定/)).toBeTruthy();
    expect(screen.getByText(/平台代管账户/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "绑定我的钱包并认领" })).toBeTruthy();
  });

  it("d 态：已被他人认领 → 红死路，无任何操作按钮", async () => {
    claimStateResp = cs({ claimed_by_wallet: OTHER });
    await renderClaimConnected();
    await fillAgentId();
    expect(await screen.findByText(/无法经平台认领/)).toBeTruthy();
    expect(screen.getByText(new RegExp(OTHER.slice(0, 8)))).toBeTruthy();
    expect(screen.queryByRole("button", { name: "提交认领" })).toBeNull();
    expect(screen.queryByRole("button", { name: "绑定我的钱包并认领" })).toBeNull();
    expect(screen.queryByPlaceholderText(/My Translate Booth/)).toBeNull();
  });

  it("d 态变体：agent_wallet=他人非托管地址 → 死路", async () => {
    claimStateResp = cs({ agent_wallet: OTHER });
    await renderClaimConnected();
    await fillAgentId();
    expect(await screen.findByText(/由他人绑定/)).toBeTruthy();
  });
});

describe("c 态全链（绑定签名 → 自动登记）", () => {
  it("绑定我的钱包并认领 → eth_signTypedData_v4 → bindWallet → POST /providers(claim_wallet) → 认领完成卡片", async () => {
    const onNext = vi.fn();
    render(
      <WalletProvider>
        <ClaimStep initial={null} onNext={onNext} />
        <ConnectProbe />
      </WalletProvider>
    );
    announce(mockWalletProvider());
    fireEvent.click(screen.getByTestId("probe-connect"));
    await waitFor(() => expect(screen.getByTestId("wstate").textContent).toBe(ADDR));
    fireEvent.change(screen.getByPlaceholderText("例如 169"), { target: { value: "169" } });
    fireEvent.change(await screen.findByPlaceholderText(/My Translate Booth/), { target: { value: "Booth 169" } });
    fireEvent.click(await screen.findByRole("button", { name: "绑定我的钱包并认领" }));

    expect(await screen.findByText(/认领完成/)).toBeTruthy();
    expect(screen.getByText("#169")).toBeTruthy();
    expect(document.body.textContent).toContain("Booth 169");
    // 链路断言：签名 RPC 之后的两个 POST 都发生，且认领体带 claim_wallet=连接地址
    const bindCall = calls.find((c) => /\/agent-identity\/169\/wallet$/.test(c.url))!;
    const body = JSON.parse(bindCall.body!) as { wallet_address: string; dry_run: boolean; deadline: number; signature: string };
    expect(body.wallet_address.toLowerCase()).toBe(ADDR.toLowerCase());
    expect(body.dry_run).toBe(false);
    expect(body.signature).toMatch(/^0x[0-9a-f]{130}$/);
    const claimCall = calls.find((c) => c.url === "/api/core/providers" && c.method === "POST")!;
    expect(JSON.parse(claimCall.body!)).toMatchObject({ agent_id: 169, display_name: "Booth 169", claim_wallet: ADDR });
  });

  it("拒绝签名（4001）→ 友好取消态，不发登记请求", async () => {
    // 用拒绝签名的钱包连接（单候选自动选中），走 c 态点击绑定后 4001
    const rejecting: Eip1193Provider = {
      request: async ({ method }: { method: string }) => {
        if (method === "eth_requestAccounts" || method === "eth_accounts") return [ADDR];
        if (method === "eth_chainId") return "0x3c8";
        if (method === "eth_signTypedData_v4") return Promise.reject(Object.assign(new Error("rejected"), { code: 4001 }));
        return null;
      },
    };
    render(
      <WalletProvider>
        <ClaimStep initial={null} onNext={vi.fn()} />
        <ConnectProbe />
      </WalletProvider>
    );
    announce(rejecting);
    fireEvent.click(screen.getByTestId("probe-connect"));
    await waitFor(() => expect(screen.getByTestId("wstate").textContent).toBe(ADDR));
    fireEvent.change(screen.getByPlaceholderText("例如 169"), { target: { value: "169" } });
    fireEvent.change(await screen.findByPlaceholderText(/My Translate Booth/), { target: { value: "Booth" } });
    fireEvent.click(await screen.findByRole("button", { name: "绑定我的钱包并认领" }));
    expect(await screen.findByText(/你取消了绑定签名/)).toBeTruthy();
    expect(calls.find((c) => c.url === "/api/core/providers" && c.method === "POST")).toBeUndefined(); // 未发登记
  });
});

describe("发布默认=认领钱包", () => {
  it("PublishStep 挂载即默认填 claimed.wallet（认证先行的收入锚点）", async () => {
    render(
      <WalletProvider>
        <PublishStep claimed={{ agent_id: 169, display_name: "Booth 169", wallet: ADDR }} onNext={vi.fn()} onBack={vi.fn()} />
      </WalletProvider>
    );
    await waitFor(() => expect((screen.getByLabelText("服务收款钱包地址") as HTMLInputElement).value).toBe(ADDR));
  });
});

describe("旧「绑定」独立步骤残留扫描", () => {
  const root = join(__dirname, "..", "src");
  it("ProviderWorkbench 无 bind 步骤键/BindStep 组件/独立绑定步骤标题", () => {
    const content = readFileSync(join(root, "pages/ProviderWorkbench.tsx"), "utf-8");
    expect(content).not.toContain('key: "bind"');
    expect(content).not.toContain("function BindStep");
    expect(content).not.toContain("步骤 4：身份钱包绑定");
    expect(content).not.toContain("④ 身份钱包绑定");
  });
  it("帮助页向导图为四步（无 ⑤）", () => {
    const content = readFileSync(join(root, "pages/Help.tsx"), "utf-8");
    expect(content).not.toContain("<h4>⑤ ");
    expect(content).toContain("① 认领身份");
  });
});
