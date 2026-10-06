// @vitest-environment jsdom
/**
 * method/探测交互测试：请求方式显隐、GET 嵌套即时预警、探测成功/失败/非 2xx 三路、
 * 「用结果生成 Schema」回填编辑器与「自动识别，请核对」标注、探测头不落盘文案。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { PublishStep } from "../src/pages/ProviderWorkbench";
import { WalletProvider } from "../src/state/WalletContext";

const OWNER = "0xC37fFE97B4D2C3D0187B1dDEDF273E52A461B63a";
const ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8";

let calls: Array<{ url: string; method: string; body?: string }>;
/** probe 外层 HTTP 状态 与 业务 status_code 分开控制 */
let probeHttp: number;
let probeResult: { status_code: number; content_type: string; elapsed_ms: number; body: unknown };

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

function installFetch() {
  calls = [];
  probeHttp = 200;
  probeResult = { status_code: 200, content_type: "application/json", elapsed_ms: 2518, body: { args: { tag: "a" }, headers: { "X-Api-Key": "sk-tmp" } } };
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? String(init.body) : undefined });
    if (url.endsWith("/api/chain/api/v1/agent-identity/169")) {
      return jsonResponse({ token_id: 169, owner: OWNER, token_uri: "u", agent_wallet: OWNER, metadata: {} });
    }
    if (url.endsWith("/api/core/services/probe")) {
      if (probeHttp !== 200) return jsonResponse({ error: "probe_failed", detail: "连接超时" }, probeHttp);
      return jsonResponse(probeResult);
    }
    return jsonResponse({ error: "not_mocked", detail: url }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
}

beforeEach(() => {
  cleanup();
  sessionStorage.clear();
  installFetch();
  delete (window as { ethereum?: unknown }).ethereum;
});
afterEach(() => vi.unstubAllGlobals());

function renderPublish() {
  return render(
    <WalletProvider>
      <PublishStep claimed={{ agent_id: 169, display_name: "Demo Booth", wallet: ADDR }} onNext={vi.fn()} onBack={vi.fn()} />
    </WalletProvider>
  );
}

/** 切到 http_json 并填基础字段。 */
async function toHttpJson() {
  fireEvent.click(screen.getByText("http_json（自运营 URL）"));
  fireEvent.change(screen.getByPlaceholderText("https://your-host/endpoint"), { target: { value: "https://httpbin.org/get" } });
  fireEvent.change(screen.getByPlaceholderText("例如 svc_my_translate"), { target: { value: "svc_probe" } });
  fireEvent.change(screen.getByPlaceholderText("例如 中英技术翻译"), { target: { value: "Probe Demo" } });
  fireEvent.change(screen.getByLabelText("服务收款钱包地址"), { target: { value: ADDR } });
}

function schemaTextareas(): HTMLTextAreaElement[] {
  const inputs = document.querySelectorAll('textarea.code') as NodeListOf<HTMLTextAreaElement>;
  return Array.from(inputs);
}

describe("请求方式（method）", () => {
  it("internal 隐藏；http_json 显示单选与探测按钮；默认 POST", async () => {
    renderPublish();
    expect(screen.queryByText("请求方式")).toBeNull();
    fireEvent.click(screen.getByText("http_json（自运营 URL）"));
    expect(screen.getByText("请求方式")).toBeTruthy();
    expect(screen.getByText("POST（默认）")).toBeTruthy();
    expect(screen.getByText("探测接口")).toBeTruthy();
    expect(screen.queryByText(/GET 模式：消费者仍 POST JSON 给网关/)).toBeNull();
    fireEvent.click(screen.getByText("GET"));
    expect(screen.getByText(/GET 模式：消费者仍 POST JSON 给网关/)).toBeTruthy();
  });

  it("GET + input_schema 含嵌套对象 → 即时黄条预警（不等 422）", async () => {
    renderPublish();
    await toHttpJson();
    fireEvent.click(screen.getByText("GET"));
    // 手写嵌套 schema 进 input 编辑器（第一个 code textarea）
    const inputTa = schemaTextareas()[0] as HTMLTextAreaElement;
    fireEvent.change(inputTa, { target: { value: JSON.stringify({ type: "object", properties: { cfg: { type: "object" } } }) } });
    expect(await screen.findByText(/GET 模式不支持嵌套对象参数/)).toBeTruthy();
    // 切回 POST → 预警消失
    fireEvent.click(screen.getByText("POST（默认）"));
    expect(screen.queryByText(/GET 模式不支持嵌套对象参数/)).toBeNull();
  });
});

describe("探测接口", () => {
  it("成功路：发送探测 → 结果卡 → 生成 Schema 回填两个编辑器并标「自动识别，请核对」", async () => {
    renderPublish();
    await toHttpJson();
    fireEvent.click(screen.getByText("GET"));
    // 示例参数行（跳过第一行空名，填第一个参数行）
    fireEvent.change(screen.getByLabelText("参数名 1"), { target: { value: "tag" } });
    fireEvent.change(screen.getByLabelText("示例值 1"), { target: { value: "a" } });

    fireEvent.click(screen.getByText("探测接口"));
    // 弹层文案：headers 仅探测用不落盘
    expect(await screen.findByRole("dialog", { name: "探测接口" })).toBeTruthy();
    expect(screen.getByText(/仅探测用，不保存不落盘/)).toBeTruthy();
    fireEvent.click(screen.getByText("发送探测（GET）"));

    expect(await screen.findByText(/探测结果/)).toBeTruthy();
    expect(screen.getByText("2518 ms · application/json")).toBeTruthy();
    // 生成 Schema → 回填 + 标注
    fireEvent.click(screen.getByText("用结果生成 Schema（input + output）"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const [inputTa, outputTa] = schemaTextareas() as [HTMLTextAreaElement, HTMLTextAreaElement];
    expect(JSON.parse(inputTa.value)).toEqual({
      type: "object",
      properties: { tag: { type: "string" } },
      required: ["tag"],
    });
    expect(JSON.parse(outputTa.value)).toEqual({
      type: "object",
      properties: {
        args: { type: "object", properties: { tag: { type: "string" } } },
        headers: { type: "object", properties: { "X-Api-Key": { type: "string" } } },
      },
    });
    expect(screen.getAllByText("自动识别，请核对").length).toBe(2);
    // probe 请求体形状：query 字符串值、headers 为空对象时省略
    const probe = calls.find((c) => c.url.endsWith("/api/core/services/probe"))!;
    expect(JSON.parse(probe.body!)).toEqual({ url: "https://httpbin.org/get", method: "GET", query: { tag: "a" } });
  });

  it("临时认证头随探测发出（body.headers 有值）且文案明示不保存", async () => {
    renderPublish();
    await toHttpJson();
    fireEvent.click(screen.getByText("探测接口"));
    const dlg = await screen.findByRole("dialog", { name: "探测接口" });
    const scope = within(screen.getByRole("dialog"));
    fireEvent.click(scope.getByText("本次临时填写"));
    fireEvent.click(scope.getByText("+ 添加认证头"));
    fireEvent.change(scope.getByLabelText("头名 1"), { target: { value: "X-API-KEY" } });
    fireEvent.change(scope.getByLabelText("凭证值 1"), { target: { value: "sk-tmp-123" } });
    fireEvent.click(scope.getByText("发送探测（POST）"));
    await screen.findByText(/探测结果/);
    const probe = calls.find((c) => c.url.endsWith("/api/core/services/probe"))!;
    expect(JSON.parse(probe.body!)).toMatchObject({ method: "POST", headers: { "X-API-KEY": "sk-tmp-123" } });
    expect(dlg.textContent).toContain("不保存");
  });

  it("失败路：probe API 500 → 错误框，无结果卡", async () => {
    probeHttp = 500;
    renderPublish();
    await toHttpJson();
    fireEvent.click(screen.getByText("探测接口"));
    await screen.findByRole("dialog", { name: "探测接口" });
    fireEvent.click(screen.getByText("发送探测（POST）"));
    await waitFor(() => expect(document.querySelector(".alert.err")).toBeTruthy());
    expect(screen.queryByText(/探测结果/)).toBeNull();
  });

  it("非 2xx 探测响应：仍展示 body 且可生成 schema（错误结构也能推断）", async () => {
    probeResult = { status_code: 404, content_type: "application/json", elapsed_ms: 512, body: { error: "not_found", code: 404, detail: null } };
    renderPublish();
    await toHttpJson();
    fireEvent.click(screen.getByText("探测接口"));
    fireEvent.click(screen.getByText("发送探测（POST）"));
    expect(await screen.findByText(/探测结果/)).toBeTruthy();
    expect(screen.getByText("404")).toBeTruthy();
    expect(screen.getByText(/非 2xx 也可从错误结构生成 schema/)).toBeTruthy();
    fireEvent.click(screen.getByText("用结果生成 Schema（input + output）"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const tas = schemaTextareas();
    const outputTa = tas[tas.length - 1] as HTMLTextAreaElement; // POST 模式中间还有示例 JSON textarea，取末位
    expect(JSON.parse(outputTa.value)).toEqual({
      type: "object",
      properties: { error: { type: "string" }, code: { type: "integer" }, detail: { type: "null" } },
    });
  });
});
