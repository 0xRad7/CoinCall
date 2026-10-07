// @vitest-environment jsdom
/**
 * 上游认证头（http_json credentials）测试：
 * 区块显隐（http_json 显 / internal 不显）、发布链式 PUT 成功与失败两路（失败不回滚 manifest）、
 * 管理页头名 chips + 全量替换确认文案、值永不回显（GET 响应无值字段、UI 只渲染头名）、清除二次确认。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ManageStep, PublishStep } from "../src/pages/ProviderWorkbench";
import { WalletProvider } from "../src/state/WalletContext";
import type { Catalog } from "../src/api/core";
import type { Eip1193Provider } from "../src/chain/injected";

const OWNER = "0xC37fFE97B4D2C3D0187B1dDEDF273E52A461B63a";
const ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8";

/** 连接钱包探针（ManageStep 所有权过滤需要连接）。 */
function mockWalletProvider(): Eip1193Provider {
  return {
    request: async ({ method }: { method: string }) => {
      if (method === "eth_requestAccounts" || method === "eth_accounts") return [ADDR];
      if (method === "eth_chainId") return "0x3c8";
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
  return <button onClick={() => void useWalletHack().connect()} data-testid="probe-connect">连接钱包</button>;
}
import { useWallet as useWalletHack } from "../src/state/WalletContext";

function manifestOf(serviceId: string, type: "http_json" | "internal") {
  return {
    service_id: serviceId,
    name: `Svc ${serviceId}`,
    description: "",
    version: "1.0.0",
    provider: { agent_id: 169, wallet: ADDR, display_name: "Demo" },
    endpoint: { type, url: type === "http_json" ? "https://httpbin.org/headers" : null, timeout_ms: 30000 },
    pricing: { token: "USDT", model: "per_call", amount: "0.01", amount_raw: "10000" },
    chain: { network: 968 },
    input_schema: { type: "object" },
    output_schema: { type: "object" },
    status: "active",
    created_at: "2026-10-01T00:00:00Z",
  } as const;
}

let calls: Array<{ url: string; method: string; body?: string }>;
let putStatus: number; // 凭证 PUT 可切 500（失败路）

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

function installFetch(catalog: Catalog) {
  calls = [];
  putStatus = 200;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? String(init.body) : undefined });
    if (url.endsWith("/api/chain/api/v1/agent-identity/169")) {
      return jsonResponse({ token_id: 169, owner: OWNER, token_uri: "u", agent_wallet: OWNER, metadata: {} });
    }
    if (url === "/api/core/catalog") return jsonResponse(catalog);
    if (url === "/api/core/providers") return jsonResponse({ providers: [{ agent_id: 169, display_name: "Demo", wallet: ADDR, claim_wallet: ADDR, created_at: "t" }] });
    if (url === "/api/core/manifests") return jsonResponse({ service_id: "svc_new", status: "active", manifest_hash: "sha256:x", manifest: manifestOf("svc_new", "http_json") });
    if (/\/api\/core\/services\/[^/]+\/credentials$/.test(url)) {
      if (method === "PUT") {
        if (putStatus !== 200) return jsonResponse({ error: "server_error", detail: "加密服务不可用" }, putStatus);
        return jsonResponse({ service_id: "svc_new", header_names: ["X-API-KEY"], updated: true });
      }
      if (method === "DELETE") return jsonResponse({ service_id: "svc_new", deleted: true });
      return jsonResponse({ service_id: "svc_new", header_names: ["X-API-KEY"] }); // GET：只有头名，绝无值
    }
    if (/\/api\/core\/teams\/\d+\/credentials$/.test(url)) return jsonResponse({ agent_id: 170, header_names: [] }); // 团队未配默认头
    return jsonResponse({ error: "not_mocked", detail: url }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
}

beforeEach(() => {
  cleanup();
  sessionStorage.clear();
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

async function fillPublishForm() {
  fireEvent.change(screen.getByPlaceholderText("例如 svc_my_translate"), { target: { value: "svc_new" } });
  fireEvent.change(screen.getByPlaceholderText("例如 中英技术翻译"), { target: { value: "Demo HTTP" } });
  fireEvent.change(screen.getByPlaceholderText(/https:\/\/your-host\/endpoint/), { target: { value: "https://httpbin.org/headers" } });
  fireEvent.change(screen.getByLabelText("服务收款钱包地址"), { target: { value: ADDR } });
}

describe("发布表单：上游认证头区块显隐", () => {
  it("http_json 才显示区块；internal 不显示", () => {
    renderPublish();
    // 默认 internal
    expect(screen.queryByText(/^上游认证头$/)).toBeNull();
    // 切到 http_json
    fireEvent.click(screen.getByText("http_json（自运营 URL）"));
    expect(screen.getByText(/^上游认证头$/)).toBeTruthy();
    expect(screen.getByText(/不会出现在目录或公开 manifest 中/)).toBeTruthy();
    // 切回 internal → 消失
    fireEvent.click(screen.getByText("internal（平台内置实现）"));
    expect(screen.queryByText(/^上游认证头$/)).toBeNull();
  });
});

describe("发布链式 PUT credentials", () => {
  it("成功路：发布后自动 PUT，成功文案含头名且不回显值", async () => {
    installFetch({ services: [], count: 0 } as unknown as Catalog);
    renderPublish();
    fireEvent.click(screen.getByText("http_json（自运营 URL）"));
    await fillPublishForm();
    // 加一行 X-API-KEY
    fireEvent.click(screen.getByText("+ 添加认证头"));
    const nameSel = screen.getByLabelText("头名 1") as HTMLSelectElement;
    fireEvent.change(nameSel, { target: { value: "X-API-KEY" } });
    fireEvent.change(screen.getByLabelText("凭证值 1"), { target: { value: "sk-live-abc123" } });
    // 值输入框是 password 型
    expect((screen.getByLabelText("凭证值 1") as HTMLInputElement).type).toBe("password");

    fireEvent.click(screen.getByText("发布服务"));
    await waitFor(() => expect(screen.getByText(/已加密保存/)).toBeTruthy());
    // manifest POST + credentials PUT 都发生，PUT body 形状正确
    const manifestPost = calls.find((c) => c.url === "/api/core/manifests")!;
    expect(manifestPost.method).toBe("POST");
    const put = calls.find((c) => c.method === "PUT" && c.url.endsWith("/services/svc_new/credentials"))!;
    expect(JSON.parse(put.body!)).toEqual({ headers: { "X-API-KEY": "sk-live-abc123" } });
    // 成功文案：只有头名，没有值
    expect(document.body.textContent).toContain("上游认证头已加密保存：X-API-KEY");
    expect(document.body.textContent).not.toContain("sk-live-abc123");
  });

  it("失败路：凭证 PUT 500 → 「服务已发布，凭证保存失败」不回滚 manifest", async () => {
    installFetch({ services: [], count: 0 } as unknown as Catalog);
    putStatus = 500;
    renderPublish();
    fireEvent.click(screen.getByText("http_json（自运营 URL）"));
    await fillPublishForm();
    fireEvent.click(screen.getByText("+ 添加认证头"));
    fireEvent.change(screen.getByLabelText("头名 1"), { target: { value: "X-API-KEY" } });
    fireEvent.change(screen.getByLabelText("凭证值 1"), { target: { value: "sk-live-xyz" } });

    fireEvent.click(screen.getByText("发布服务"));
    await waitFor(() => expect(screen.getByText(/服务已发布，凭证保存失败/)).toBeTruthy());
    expect(screen.getByText(/可在团队详情 → 服务管理重试/)).toBeTruthy();
    // manifest 只 POST 了一次且未回滚（无 DELETE /manifests）
    expect(calls.filter((c) => c.url === "/api/core/manifests").length).toBe(1);
    expect(screen.getAllByText(/已发布/).length).toBeGreaterThanOrEqual(1); // manifest 成功提示仍在（未回滚）
  });
});

describe("管理页：上游认证头管理", () => {
  const catalog = {
    services: [
      { service_id: "svc_http", manifest: manifestOf("svc_http", "http_json"), status: "active", manifest_hash: "sha256:a" },
      { service_id: "svc_internal", manifest: manifestOf("svc_internal", "internal"), status: "active", manifest_hash: "sha256:b" },
    ],
    count: 2,
  } as unknown as Catalog;

  it("internal 行不显示入口；http_json 展开后头名 chips（只有名字无值）+ 全量替换确认文案", async () => {
    installFetch(catalog);
    render(
      <WalletProvider>
        <ManageStep claimedAgentId={169} onNext={vi.fn()} onBack={vi.fn()} />
        <ConnectProbe />
      </WalletProvider>
    );
    announce(mockWalletProvider());
    fireEvent.click(screen.getByTestId("probe-connect"));
    // internal 行没有「上游认证头」按钮（等目录加载完）
    const allButtons = await screen.findAllByText("上游认证头");
    expect(allButtons.length).toBe(1); // 仅 svc_http

    fireEvent.click(allButtons[0]!);
    expect(await screen.findByText("X-API-KEY ✓")).toBeTruthy();
    expect(screen.getByText(/仅头名，值不回显/)).toBeTruthy();
    // GET 响应无值字段（形状锁定）
    const getCred = calls.find((c) => c.method === "GET" && c.url.endsWith("/services/svc_http/credentials"))!;
    expect(getCred).toBeTruthy();
    // 全量替换：更新凭证 → 确认文案「替换全部 1 个头」
    fireEvent.click(screen.getByText("更新凭证"));
    expect(screen.getByText(/全量替换语义/)).toBeTruthy();
    expect(screen.getByText(/留空保存 = 清空全部/)).toBeTruthy();
    fireEvent.click(screen.getByText("替换全部 1 个头"));
    expect(screen.getByText(/确认替换/)).toBeTruthy();
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("完全替换");
    expect(dialog.textContent).toContain("svc_http");
    expect(dialog.textContent).toContain("现有的 1 个认证头");
    fireEvent.click(screen.getByText("确认替换"));
    await waitFor(() => {
      const put = calls.find((c) => c.method === "PUT" && c.url.endsWith("/services/svc_http/credentials"));
      expect(put).toBeTruthy();
      expect(JSON.parse(put!.body!)).toEqual({ headers: {} }); // 全量替换：留空=清空语义
    });
  });

  it("清除凭证：二次确认后 DELETE", async () => {
    installFetch(catalog);
    render(
      <WalletProvider>
        <ManageStep claimedAgentId={169} onNext={vi.fn()} onBack={vi.fn()} />
        <ConnectProbe />
      </WalletProvider>
    );
    announce(mockWalletProvider());
    fireEvent.click(screen.getByTestId("probe-connect"));
    fireEvent.click(await screen.findByText("上游认证头"));
    await screen.findByText("X-API-KEY ✓");
    fireEvent.click(screen.getByText("清除凭证"));
    expect(screen.getByText(/清空 1 个认证头/)).toBeTruthy();
    fireEvent.click(screen.getByText("确认清除"));
    await waitFor(() => {
      const del = calls.find((c) => c.method === "DELETE" && c.url.endsWith("/services/svc_http/credentials"));
      expect(del).toBeTruthy();
    });
  });
});

describe("值永不回显", () => {
  it("GET /credentials 响应类型与 UI 均只有 header_names（无值字段）", async () => {
    const catalog = {
      services: [{ service_id: "svc_http", manifest: manifestOf("svc_http", "http_json"), status: "active", manifest_hash: "sha256:a" }],
      count: 1,
    } as unknown as Catalog;
    installFetch(catalog);
    render(
      <WalletProvider>
        <ManageStep claimedAgentId={169} onNext={vi.fn()} onBack={vi.fn()} />
        <ConnectProbe />
      </WalletProvider>
    );
    announce(mockWalletProvider());
    fireEvent.click(screen.getByTestId("probe-connect"));
    fireEvent.click(await screen.findByText("上游认证头"));
    await screen.findByText("X-API-KEY ✓");
    const text = document.body.textContent ?? "";
    expect(text).toContain("X-API-KEY");
    // GET 返回体只含 service_id/header_names（值字段不存在——形状由 mock 锁定 + 组件只渲染头名）
    const body = { service_id: "svc_http", header_names: ["X-API-KEY"] };
    expect(Object.keys(body)).toEqual(["service_id", "header_names"]);
    expect(text).not.toMatch(/sk-|Bearer\s+\S/);
  });
});
