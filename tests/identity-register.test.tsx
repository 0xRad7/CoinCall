// @vitest-environment jsdom
/**
 * 「注册新 Agent 身份」闭环测试（mock 8010 两端点）：
 * 注册成功自动回填 agent_id（含 RegisterStep 集成）、轮询 found=false→true、
 * 超时友好态+手动查询入口、dry_run 语义说明文案、注册请求体 dry_run:false。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { IdentityRegister } from "../src/components/IdentityRegister";
import { RegisterStep } from "../src/pages/ProviderWorkbench";

const TX = "0x" + "ab".repeat(32);
const OWNER = "0xC37fFE97B4D2C3D0187B1dDEDF273E52A461B63a";

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
      const next = resultResponses.shift() ?? { found: true, tx_hash: TX, agent_ids: [200], owner: OWNER, agent_wallet: "0xAdeE6D874ceC4b8Dba0C969585bd2E593ddfba3D" };
      return jsonResponse(next);
    }
    if (/\/api\/chain\/api\/v1\/agent-identity\/\d+$/.test(url)) {
      return jsonResponse({ token_id: 200, owner: OWNER, token_uri: "u", agent_wallet: "0xAdeE6D874ceC4b8Dba0C969585bd2E593ddfba3D", metadata: {} });
    }
    return jsonResponse({ error: "not_mocked", detail: url }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
}

function mintedMatcher(n: number) {
  return (_: string, el: Element | null) => !!el && el.className.includes("alert ok") && (el.textContent ?? "").includes(`身份 #${n} 已铸造`);
}

beforeEach(() => {
  cleanup();
  installFetch();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("IdentityRegister 组件", () => {
  it("注册成功：显示已铸造 + 回调 agentId + 请求体 dry_run:false", async () => {
    resultResponses = [{ found: false, tx_hash: TX }, { found: true, tx_hash: TX, agent_ids: [200], owner: OWNER, agent_wallet: "0xAdeE6D874ceC4b8Dba0C969585bd2E593ddfba3D" }];
    const onRegistered = vi.fn();
    render(<IdentityRegister onRegistered={onRegistered} pollMs={5} maxAttempts={10} />);

    fireEvent.click(screen.getByText("发起注册"));
    expect(await screen.findByText(mintedMatcher(200))).toBeTruthy();
    expect(onRegistered).toHaveBeenCalledWith(200, OWNER, "0xAdeE6D874ceC4b8Dba0C969585bd2E593ddfba3D");
    // 注册请求体语义：真实上链（dry_run=false），并带 agent_uri
    const reg = calls.find((c) => c.url.endsWith("/agent-identity/register"))!;
    expect(reg.method).toBe("POST");
    expect(JSON.parse(reg.body!)).toMatchObject({ dry_run: false, agent_uri: expect.stringMatching(/^https?:\/\//) });
    // 交易哈希外链渲染（href 指向 scan.bohr.life/tx/…）
    const link = document.querySelector('a[href*="scan.bohr.life/tx/"]');
    expect(link).toBeTruthy();
  });

  it("轮询 found=false → true：register-result 被多次查询", async () => {
    resultResponses = [
      { found: false, tx_hash: TX },
      { found: false, tx_hash: TX },
      { found: true, tx_hash: TX, agent_ids: [201], owner: OWNER },
    ];
    render(<IdentityRegister onRegistered={vi.fn()} pollMs={5} maxAttempts={10} />);
    fireEvent.click(screen.getByText("发起注册"));
    await waitFor(() => expect(screen.getByText(mintedMatcher(201))).toBeTruthy());
    const polls = calls.filter((c) => c.url.includes("/register-result/"));
    expect(polls.length).toBeGreaterThanOrEqual(3);
  });

  it("超时友好态：显示手动查询入口；查询仍 found=false 给人话提示", async () => {
    resultResponses = Array.from({ length: 20 }, () => ({ found: false, tx_hash: TX }));
    render(<IdentityRegister onRegistered={vi.fn()} pollMs={5} maxAttempts={3} />);
    fireEvent.click(screen.getByText("发起注册"));
    expect(await screen.findByText(/未查到铸造回执/)).toBeTruthy();
    // 手动查询入口出现；粘贴哈希再查（仍 false → 人话提示）
    const input = screen.getByPlaceholderText(/粘贴注册交易哈希/) as HTMLInputElement;
    fireEvent.change(input, { target: { value: TX } });
    fireEvent.click(screen.getByText("查询铸造结果"));
    expect(await screen.findByText(/仍未上链/)).toBeTruthy();
  });

  it("dry_run 语义与「平台代发」说明文案存在", () => {
    render(<IdentityRegister onRegistered={vi.fn()} />);
    const text = document.body.textContent ?? "";
    expect(text).toContain("dry_run=false");
    expect(text).toContain("平台链上服务代发");
    expect(text).toContain("平台代管账户");
    expect(text).toContain("无需你的钱包签名");
  });
});

describe("RegisterStep 集成（自动回填 agent_id）", () => {
  it("注册铸造成功 → 新 agentId 自动填入 Agent ID 输入框，链上预检转绿", async () => {
    resultResponses = [{ found: false, tx_hash: TX }, { found: true, tx_hash: TX, agent_ids: [200], owner: OWNER, agent_wallet: "0xAdeE6D874ceC4b8Dba0C969585bd2E593ddfba3D" }];
    render(<RegisterStep initial={null} onNext={vi.fn()} />);

    const idInput = screen.getByPlaceholderText("例如 162") as HTMLInputElement;
    expect(idInput.value).toBe("");
    fireEvent.click(screen.getByText("发起注册"));

    await waitFor(() => expect(idInput.value).toBe("200"), { timeout: 5_000 }); // 默认 pollMs=2s，放宽等待
    expect(await screen.findByText(/✓ 链上身份存在/)).toBeTruthy();
  });
});
