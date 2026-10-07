// @vitest-environment jsdom
/**
 * 团队默认认证头 UI 测试：
 * 弹窗 roundtrip（GET 掩码/PUT 体/替换全部 N 个确认/清除二次确认）；
 * 发布复用开关三态（团队已配默认开+发布不发服务级 PUT；关+填写则 PUT；团队未配提示）；
 * 管理行未单独配置时显示「团队默认（X ✓）」徽标。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { PublishStep, TeamHome } from "../src/pages/ProviderWorkbench";
import { WalletProvider } from "../src/state/WalletContext";

const MY = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8";
const OWNER = "0xC37fFE97B4D2C3D0187B1dDEDF273E52A461B63a";

let calls: Array<{ url: string; method: string; body?: string }>;
let teamHeaders: string[]; // 团队默认头名（空=未配）
let serviceHeaderNames: string[];

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

function manifestOf(sid: string, agentId: number) {
  return {
    service_id: sid, name: `Svc ${sid}`, description: "", version: "1",
    provider: { agent_id: agentId, wallet: MY, display_name: "RadAI" },
    endpoint: { type: "http_json", url: null, timeout_ms: 30000, method: "POST" },
    pricing: { token: "USDT", model: "per_call", amount: "0.01", amount_raw: "10000" },
    chain: { network: 968 }, input_schema: { type: "object" }, output_schema: { type: "object" },
    status: "active", created_at: "t",
  };
}

function installFetch() {
  calls = [];
  teamHeaders = ["X-API-KEY"];
  serviceHeaderNames = []; // 服务级未配（触发团队默认徽标）
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? String(init.body) : undefined });
    if (/\/api\/core\/teams\/170\/credentials$/.test(url)) {
      if (method === "PUT") {
        const body = JSON.parse(String(init?.body)) as { headers: Record<string, string> };
        teamHeaders = Object.keys(body.headers);
        return jsonResponse({ agent_id: 170, header_names: teamHeaders, updated: true });
      }
      if (method === "DELETE") {
        teamHeaders = [];
        return jsonResponse({ agent_id: 170, deleted: true });
      }
      return jsonResponse({ agent_id: 170, header_names: teamHeaders });
    }
    if (/\/api\/chain\/api\/v1\/agent-identity\/170$/.test(url)) {
      return jsonResponse({ token_id: 170, owner: OWNER, token_uri: "u", agent_wallet: MY, metadata: {} });
    }
    if (url === "/api/core/manifests") return jsonResponse({ service_id: "svc_new", status: "active", manifest_hash: "sha256:x", manifest: manifestOf("svc_new", 170) });
    if (/\/api\/core\/services\/[^/]+\/credentials$/.test(url)) {
      if (method === "PUT") return jsonResponse({ service_id: "svc_new", header_names: ["X-API-KEY"], updated: true });
      return jsonResponse({ service_id: "svc_new", header_names: serviceHeaderNames });
    }
    if (url === "/api/core/teams/170") {
      return jsonResponse({
        team: { agent_id: 170, display_name: "RadAI", wallet: MY, claim_wallet: MY, created_at: "t" },
        services: [{ service_id: "svc_rad", manifest: manifestOf("svc_rad", 170), status: "active", manifest_hash: "x" }],
        revenue: { total_raw: 0, charged_count: 0, wallets: [], proof: "" },
        fulfillment: { services: [] },
        feedback: { services: [] },
        degraded: [],
      });
    }
    return jsonResponse({ error: "not_mocked", detail: url }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
}

beforeEach(() => {
  cleanup();
  sessionStorage.clear();
  localStorage.clear();
  installFetch();
});
afterEach(() => vi.unstubAllGlobals());

function fillPublishBasics() {
  fireEvent.change(screen.getByPlaceholderText("例如 svc_my_translate"), { target: { value: "svc_new" } });
  fireEvent.change(screen.getByPlaceholderText("例如 中英技术翻译"), { target: { value: "Demo" } });
  fireEvent.change(screen.getByPlaceholderText("https://your-host/endpoint"), { target: { value: "https://httpbin.org/post" } });
  fireEvent.change(screen.getByLabelText("服务收款钱包地址"), { target: { value: MY } });
}

describe("团队默认认证头弹窗（Team 详情）", () => {
  it("打开即显示掩码 chips；更新→替换全部确认→PUT 体断言→chips 刷新", async () => {
    render(
      <WalletProvider>
        <TeamHome agentId={170} onBack={vi.fn()} onPublish={vi.fn()} />
      </WalletProvider>
    );
    fireEvent.click(await screen.findByRole("button", { name: "团队默认认证头" }));
    const dlg = await screen.findByRole("dialog", { name: "团队默认认证头" });
    expect(dlg.textContent).toContain("X-API-KEY ✓");
    expect(dlg.textContent).toContain("（仅头名，值不回显）");
    expect(dlg.textContent).toContain("服务自身未配置凭证时自动生效"); // 顶部说明

    // 更新 → 编辑器 → 替换全部 1 个头 → 确认
    fireEvent.click(within(dlg).getByText("更新凭证"));
    fireEvent.click(within(dlg).getByText("+ 添加认证头"));
    fireEvent.change(within(dlg).getByLabelText("头名 1"), { target: { value: "X-API-KEY" } });
    fireEvent.change(within(dlg).getByLabelText("凭证值 1"), { target: { value: "sk-team-new" } });
    fireEvent.click(within(dlg).getByText("替换全部 1 个头"));
    const confirmDialog = screen.getAllByRole("dialog").map((d) => d as HTMLElement).find((d) => d.textContent?.includes("替换团队全部"))!;
    expect(confirmDialog.textContent).toContain("1 个默认认证头");
    fireEvent.click(screen.getByText("确认替换"));
    await waitFor(() => expect(within(dlg).getAllByText("X-API-KEY ✓").length).toBeGreaterThanOrEqual(1));
    // PUT 体
    const put = calls.find((c) => /\/teams\/170\/credentials$/.test(c.url) && c.method === "PUT")!;
    expect(JSON.parse(put.body!)).toEqual({ headers: { "X-API-KEY": "sk-team-new" } });
  });

  it("清除：二次确认 → DELETE → 空态", async () => {
    render(
      <WalletProvider>
        <TeamHome agentId={170} onBack={vi.fn()} onPublish={vi.fn()} />
      </WalletProvider>
    );
    fireEvent.click(await screen.findByRole("button", { name: "团队默认认证头" }));
    const dlg = await screen.findByRole("dialog", { name: "团队默认认证头" });
    fireEvent.click(within(dlg).getByText("清除凭证"));
    fireEvent.click(screen.getByText("确认清除"));
    await waitFor(() => expect(dlg.textContent).toContain("尚未配置团队默认认证头"));
    expect(calls.some((c) => /\/teams\/170\/credentials$/.test(c.url) && c.method === "DELETE")).toBe(true);
  });
});

describe("发布页复用开关三态", () => {
  it("团队已配 → 开关默认开：显示复用说明+隐藏服务级编辑器；发布不发服务级 PUT", async () => {
    render(
      <WalletProvider>
        <PublishStep claimed={{ agent_id: 170, display_name: "RadAI", wallet: MY }} onNext={vi.fn()} onBack={vi.fn()} />
      </WalletProvider>
    );
    fireEvent.click(screen.getByText("http_json（自运营 URL）"));
    await waitFor(() => expect(screen.getByLabelText("默认复用团队认证头")).toBeTruthy());
    const toggle = screen.getByLabelText("默认复用团队认证头") as HTMLInputElement;
    expect(toggle.checked).toBe(true); // 默认开
    expect(screen.getByText(/将复用团队默认头/)).toBeTruthy();
    expect(screen.getByText(/在团队详情管理/)).toBeTruthy();
    expect(screen.queryByLabelText("头名 1")).toBeNull(); // 编辑器隐藏

    // 发布 → 不发服务级 PUT（留空回退）
    fillPublishBasics();
    fireEvent.click(screen.getByRole("button", { name: "发布服务" }));
    await waitFor(() => {
      const ok = [...document.querySelectorAll(".alert.ok")].find((w) => w.textContent?.includes("已发布"));
      expect(ok).toBeTruthy();
    });
    expect(document.body.textContent).toContain("自动复用团队默认头");
    expect(calls.some((c) => /\/services\/svc_new\/credentials$/.test(c.url) && c.method === "PUT")).toBe(false); // 未发服务级
  });

  it("开关关 + 填写 → 照旧链式 PUT 服务级", async () => {
    render(
      <WalletProvider>
        <PublishStep claimed={{ agent_id: 170, display_name: "RadAI", wallet: MY }} onNext={vi.fn()} onBack={vi.fn()} />
      </WalletProvider>
    );
    fireEvent.click(screen.getByText("http_json（自运营 URL）"));
    await waitFor(() => expect(screen.getByLabelText("默认复用团队认证头")).toBeTruthy());
    fireEvent.click(screen.getByLabelText("默认复用团队认证头")); // 关
    fireEvent.click(screen.getByText("+ 添加认证头"));
    fireEvent.change(screen.getByLabelText("头名 1"), { target: { value: "Authorization" } });
    fireEvent.change(screen.getByLabelText("凭证值 1"), { target: { value: "Bearer tk" } });
    fillPublishBasics();
    fireEvent.click(screen.getByRole("button", { name: "发布服务" }));
    await waitFor(() => {
      const put = calls.find((c) => /\/services\/svc_new\/credentials$/.test(c.url) && c.method === "PUT");
      expect(put).toBeTruthy();
    });
    const put = calls.find((c) => /\/services\/svc_new\/credentials$/.test(c.url) && c.method === "PUT")!;
    expect(JSON.parse(put.body!)).toEqual({ headers: { Authorization: "Bearer tk" } });
  });

  it("团队未配 → 无开关，提示去团队详情配置；编辑器照常", async () => {
    teamHeaders = [];
    render(
      <WalletProvider>
        <PublishStep claimed={{ agent_id: 170, display_name: "RadAI", wallet: MY }} onNext={vi.fn()} onBack={vi.fn()} />
      </WalletProvider>
    );
    fireEvent.click(screen.getByText("http_json（自运营 URL）"));
    await waitFor(() => expect(screen.getByText(/团队还没有默认认证头/)).toBeTruthy());
    expect(screen.queryByLabelText("默认复用团队认证头")).toBeNull(); // 开关隐藏
    expect(screen.getByText("+ 添加认证头")).toBeTruthy(); // 编辑器照常
  });
});
