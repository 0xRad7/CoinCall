// @vitest-environment jsdom
/**
 * 「注册新 Agent 身份」闭环 + 三钱包角色术语测试：
 * 注册成功自动回填（含 RegisterStep 集成）、轮询 found=false→true、超时友好态、dry_run 语义、
 * 绿条三角色文案（不得把 agent_wallet 称作收款钱包）+ 绑定引导、
 * 绑定成功绿条更新（连接钱包 signTypedData 全 mock）、发布表单默认服务收款钱包=连接地址、
 * 术语残留扫描（「收款钱包」只允许以「服务收款钱包」出现）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Wallet } from "ethers";
import { IdentityRegister } from "../src/components/IdentityRegister";
import { PublishStep } from "../src/pages/ProviderWorkbench";
import { WalletProvider, useWallet } from "../src/state/WalletContext";

const TX = "0x" + "ab".repeat(32);
const TX2 = "0x" + "cd".repeat(32);
const OWNER = "0xC37fFE97B4D2C3D0187B1dDEDF273E52A461B63a"; // 平台代管账户
const ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"; // anvil#1（连接钱包 / 用户自己的地址）
const ANVIL1_PK = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d";

let calls: Array<{ url: string; method: string; body?: string }> = [];
let resultResponses: Array<{ found: boolean; tx_hash: string; agent_ids?: number[]; owner?: string; agent_wallet?: string }>;

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

function installFetch() {
  calls = [];
  resultResponses = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    calls.push({ url, method: init?.method ?? "GET", body: init?.body ? String(init.body) : undefined });
    if (url.endsWith("/api/chain/api/v1/agent-identity/register")) {
      return jsonResponse({ dry_run: false, tx_hash: TX, status: 1, block_number: 123 });
    }
    if (url.includes("/api/v1/agent-identity/register-result/")) {
      const next = resultResponses.shift() ?? { found: true, tx_hash: TX, agent_ids: [169], owner: OWNER, agent_wallet: OWNER };
      return jsonResponse(next);
    }
    if (/\/api\/chain\/api\/v1\/agent-identity\/\d+\/wallet$/.test(url)) {
      return jsonResponse({ tx_hash: TX2, explorer_url: `https://scan.bohr.life/tx/${TX2}` });
    }
    if (/\/api\/chain\/api\/v1\/agent-identity\/\d+$/.test(url)) {
      return jsonResponse({ token_id: 169, owner: OWNER, token_uri: "u", agent_wallet: OWNER, metadata: {} });
    }
    return jsonResponse({ error: "not_mocked", detail: url }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
}

/** 可连接的 mock 浏览器钱包（anvil#1 真签名）。 */
function mockWalletProvider() {
  const wallet = new Wallet(ANVIL1_PK);
  return {
    request: async ({ method, params }: { method: string; params?: unknown[] | object }) => {
      if (method === "eth_requestAccounts" || method === "eth_accounts") return [ADDR];
      if (method === "eth_chainId") return "0x3c8";
      if (method === "eth_signTypedData_v4") {
        const [addr, payloadJson] = params as [string, string];
        if (!addr || addr.toLowerCase() !== ADDR.toLowerCase()) throw Object.assign(new Error("wrong account"), { code: -32000 });
        const payload = JSON.parse(payloadJson) as { domain: unknown; types: Record<string, Record<string, unknown>[] & { EIP712Domain?: unknown }>; message: Record<string, unknown> };
        const { EIP712Domain: _omit, ...typesOnly } = payload.types;
        void _omit;
        return await wallet.signTypedData(payload.domain as never, typesOnly as never, payload.message as never);
      }
      return null;
    },
  };
}

function announce(provider: unknown, rdns = "com.okx.wallet", name = "OKX Wallet") {
  window.dispatchEvent(
    new CustomEvent("eip6963:announceProvider", { detail: { info: { uuid: rdns, name, icon: "", rdns }, provider } })
  );
}

/** 连接探针：模拟用户在页面上连接钱包。 */
function ConnectProbe() {
  const w = useWallet();
  return (
    <div>
      <button onClick={() => void w.connect()}>连接钱包</button>
      <div data-testid="wstate">{w.address ?? "未连接"}</div>
    </div>
  );
}

function mintedMatcher(n: number) {
  return (_: string, el: Element | null) => !!el && el.className.includes("alert ok") && (el.textContent ?? "").includes(`身份 #${n} 已铸造`);
}

/** 含 agent_wallet 的文案里不得再出现「收款钱包」（除非以「服务收款钱包」出现）。 */
const AMBIGUOUS = /(?<!服务)收款钱包/;

beforeEach(() => {
  cleanup();
  sessionStorage.clear();
  installFetch();
  delete (window as { ethereum?: unknown }).ethereum;
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("IdentityRegister 组件", () => {
  it("注册成功：显示已铸造 + 回调 agentId + 请求体 dry_run:false", async () => {
    resultResponses = [{ found: false, tx_hash: TX }, { found: true, tx_hash: TX, agent_ids: [169], owner: OWNER, agent_wallet: OWNER }];
    const onRegistered = vi.fn();
    render(
      <WalletProvider>
        <IdentityRegister onRegistered={onRegistered} pollMs={5} maxAttempts={10} />
      </WalletProvider>
    );

    fireEvent.click(screen.getByText("发起注册"));
    await screen.findByText(mintedMatcher(169));
    expect(onRegistered).toHaveBeenCalledWith(169, OWNER, OWNER);
    // 注册请求体语义：真实上链（dry_run=false），并带 agent_uri
    const reg = calls.find((c) => c.url.endsWith("/agent-identity/register"))!;
    expect(JSON.parse(reg.body!)).toMatchObject({ dry_run: false, agent_uri: expect.stringMatching(/^https?:\/\//) });
    // 交易哈希外链渲染
    expect(document.querySelector('a[href*="scan.bohr.life/tx/"]')).toBeTruthy();
  });

  it("绿条三角色术语：agent_wallet=平台代管时称「身份钱包（当前=平台代管）」，绝无歧义「收款钱包」，并带绑定引导", async () => {
    resultResponses = [{ found: true, tx_hash: TX, agent_ids: [169], owner: OWNER, agent_wallet: OWNER }];
    render(
      <WalletProvider>
        <IdentityRegister onRegistered={vi.fn()} pollMs={5} maxAttempts={5} />
      </WalletProvider>
    );
    fireEvent.click(screen.getByText("发起注册"));
    const bar = await screen.findByText(mintedMatcher(169));
    const text = bar.textContent ?? "";
    expect(text).toContain("平台代管账户");
    expect(text).toContain("身份钱包");
    expect(text).toContain("当前=平台代管");
    expect(text).toContain("建议绑定你自己的");
    expect(AMBIGUOUS.test(text)).toBe(false);
    // 跳过绑定也允许的提示
    expect(text).toContain("可跳过绑定");
    // 未连接钱包时的绑定引导（提示先连接）
    expect(bar.textContent).toMatch(/连接钱包/);
    expect(bar.textContent).toMatch(/把身份钱包绑/);
  });

  it("轮询 found=false → true：register-result 被多次查询", async () => {
    resultResponses = [
      { found: false, tx_hash: TX },
      { found: false, tx_hash: TX },
      { found: true, tx_hash: TX, agent_ids: [201], owner: OWNER },
    ];
    render(
      <WalletProvider>
        <IdentityRegister onRegistered={vi.fn()} pollMs={5} maxAttempts={10} />
      </WalletProvider>
    );
    fireEvent.click(screen.getByText("发起注册"));
    await waitFor(() => expect(screen.getByText(mintedMatcher(201))).toBeTruthy());
    const polls = calls.filter((c) => c.url.includes("/register-result/"));
    expect(polls.length).toBeGreaterThanOrEqual(3);
  });

  it("超时友好态：显示手动查询入口；查询仍 found=false 给人话提示", async () => {
    resultResponses = Array.from({ length: 20 }, () => ({ found: false, tx_hash: TX }));
    render(
      <WalletProvider>
        <IdentityRegister onRegistered={vi.fn()} pollMs={5} maxAttempts={3} />
      </WalletProvider>
    );
    fireEvent.click(screen.getByText("发起注册"));
    expect(await screen.findByText(/未查到铸造回执/)).toBeTruthy();
    const input = screen.getByPlaceholderText(/粘贴注册交易哈希/) as HTMLInputElement;
    fireEvent.change(input, { target: { value: TX } });
    fireEvent.click(screen.getByText("查询铸造结果"));
    expect(await screen.findByText(/仍未上链/)).toBeTruthy();
  });

  it("dry_run 语义与「平台代发」说明文案存在", () => {
    render(
      <WalletProvider>
        <IdentityRegister onRegistered={vi.fn()} />
      </WalletProvider>
    );
    const text = document.body.textContent ?? "";
    expect(text).toContain("dry_run=false");
    expect(text).toContain("平台链上服务代发");
    expect(text).toContain("平台代管账户");
    expect(text).toContain("无需你的钱包签名");
  });
});

describe("注册 → 绑定引导链路（连接钱包后）", () => {
  it("连接钱包后点「把身份钱包绑定为当前连接的钱包」→ 扩展签名 → 绑定成功绿条更新为新地址", async () => {
    const onRegistered = vi.fn();
    render(
      <WalletProvider>
        <IdentityRegister onRegistered={onRegistered} pollMs={5} maxAttempts={5} />
        <ConnectProbe />
      </WalletProvider>
    );
    // 连接（单候选 6963 → 自动直连）
    announce(mockWalletProvider());
    fireEvent.click(screen.getByText("连接钱包"));
    await waitFor(() => expect(screen.getByTestId("wstate").textContent).toBe(ADDR));

    // 注册 → 绿条显示「身份钱包（当前=平台代管）」+ 主动作按钮
    fireEvent.click(screen.getByText("发起注册"));
    const bar = await screen.findByText(mintedMatcher(169));
    expect(bar.textContent).toContain("当前=平台代管");
    const bindBtn = await screen.findByText(/^把身份钱包绑定为当前连接的钱包/);
    fireEvent.click(bindBtn);

    // 扩展签名 → 8010 绑定 → 绿条更新为你绑定的地址，不再「平台代管」
    await waitFor(() => expect(bar.textContent).toContain("你绑定的"), { timeout: 5_000 });
    expect(bar.textContent).toContain(ADDR.slice(0, 8));
    expect(bar.textContent).not.toContain("当前=平台代管");
    expect(AMBIGUOUS.test(bar.textContent ?? "")).toBe(false);
    // 绑定请求体：wallet_address=连接地址、dry_run:false、deadline≈now+300、签名 65 字节
    const bindCall = calls.find((c) => /\/agent-identity\/\d+\/wallet$/.test(c.url))!;
    expect(JSON.parse(bindCall.body!)).toMatchObject({ wallet_address: ADDR, dry_run: false });
    const body = JSON.parse(bindCall.body!) as { deadline: number; signature: string };
    expect(body.deadline).toBeGreaterThan(Math.floor(Date.now() / 1000) + 280);
    expect(body.deadline).toBeLessThan(Math.floor(Date.now() / 1000) + 320);
    expect(body.signature).toMatch(/^0x[0-9a-f]{130}$/);
  }, 20_000);
});

describe("注册闭环回调（onRegistered 供认领步骤回填 agent_id）", () => {
  it("注册铸造成功 → onRegistered 收到 (169, owner, agent_wallet)", async () => {
    resultResponses = [{ found: false, tx_hash: TX }, { found: true, tx_hash: TX, agent_ids: [169], owner: OWNER, agent_wallet: OWNER }];
    const onRegistered = vi.fn();
    render(
      <WalletProvider>
        <IdentityRegister onRegistered={onRegistered} pollMs={5} maxAttempts={5} />
      </WalletProvider>
    );
    fireEvent.click(screen.getByText("发起注册"));
    await waitFor(() => expect(onRegistered).toHaveBeenCalledWith(169, OWNER, OWNER), { timeout: 5_000 });
  });
});

describe("PublishStep 服务收款钱包（默认=认领钱包）", () => {
  it("挂载即默认填认领钱包；连接后出现快填按钮；与身份钱包差异给提示", async () => {
    render(
      <WalletProvider>
        <PublishStep claimed={{ agent_id: 169, display_name: "Demo Booth", wallet: ADDR }} onNext={vi.fn()} onBack={vi.fn()} />
        <ConnectProbe />
      </WalletProvider>
    );
    // 认证先行：默认 = 认领钱包（无需等连接）
    await waitFor(() => expect((screen.getByLabelText("服务收款钱包地址") as HTMLInputElement).value).toBe(ADDR));
    expect(document.body.textContent).toContain("Charged 记账键");
    expect(document.body.textContent).toContain("与身份钱包相互独立");
    // 身份钱包（mock=平台代管）≠ 服务收款钱包 → 差异提示
    expect(document.body.textContent).toMatch(/与链上身份钱包（0xC37fFE97…）不同/);
    // 连接后：快填按钮（=当前钱包）
    announce(mockWalletProvider());
    fireEvent.click(screen.getByText("连接钱包"));
    await waitFor(() => expect(screen.getByRole("button", { name: new RegExp(`使用当前钱包 ${ADDR.slice(0, 6)}`) })).toBeTruthy(), { timeout: 5_000 });
    expect(document.body.textContent ?? "").not.toMatch(AMBIGUOUS);
  });
});

describe("术语残留扫描（全站）", () => {
  const root = join(__dirname, "..", "src");
  const files = [
    "components/IdentityRegister.tsx",
    "components/BindIdentityWallet.tsx",
    "pages/ProviderWorkbench.tsx",
    "pages/Help.tsx",
    "lib/errors.ts",
    "pages/ConsumerWorkbench.tsx",
    "api/gateway.ts",
  ];
  it("「收款钱包」只允许以「服务收款钱包」出现（平台代管/agent_wallet 语境绝不用该词）", () => {
    for (const f of files) {
      const content = readFileSync(join(root, f), "utf-8");
      const hits = content.match(new RegExp(AMBIGUOUS, "g"));
      expect(hits, `${f} 仍有歧义「收款钱包」: ${hits?.join(",")}`).toBeNull();
    }
  });
});
