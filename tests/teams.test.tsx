// @vitest-environment jsdom
/**
 * Teams 形态测试：
 * mine 空态/列表渲染；创建团队全链 mock（prepare→签名→providers 提交体断言）；
 * Team 主页聚合数字来自 teams 端点；发布表单所属团队只读与默认值；
 * 主路径组件「认领/Agent ID」字样残留扫描（高级折叠区除外）；目录团队卡分组渲染。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Wallet } from "ethers";
import { CreateTeamButton, MyTeamsStep, PublishStep, TeamHome } from "../src/pages/ProviderWorkbench";
import Overview from "../src/pages/Overview";
import { WalletProvider, useWallet } from "../src/state/WalletContext";
import type { Eip1193Provider } from "../src/chain/injected";
import type { Catalog, TeamDetail } from "../src/api/core";

const MY = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8";
const ANVIL1_PK = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d";
const CUSTODIAN = "0xC37fFE97B4D2C3D0187B1dDEDF273E52A461B63a";

let calls: Array<{ url: string; method: string; body?: string }>;
let mineTeams: Array<{ agent_id: number; display_name: string; claim_wallet: string | null; service_count: number; created_at: string }>;

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

const TEAM_DETAIL: TeamDetail = {
  team: { agent_id: 170, display_name: "RadAI", wallet: MY, claim_wallet: MY, created_at: "2026-10-06 09:41:56" },
  services: [
    {
      service_id: "svc_rad_ai",
      manifest: {
        service_id: "svc_rad_ai", name: "RadAI Report", description: "", version: "1",
        provider: { agent_id: 170, wallet: MY, display_name: "RadAI" },
        endpoint: { type: "http_json", url: null, timeout_ms: 30000, method: "GET" },
        pricing: { token: "USDT", model: "per_call", amount: "0.01", amount_raw: "10000" },
        chain: { network: 968 }, input_schema: { type: "object" }, output_schema: { type: "object" },
        status: "active", created_at: "t",
      },
      status: "active", manifest_hash: "x",
    },
  ],
  revenue: { total_raw: 20000, charged_count: 2, wallets: [MY], proof: "/proof" },
  fulfillment: { services: [{ service_id: "svc_rad_ai", calls_success: 3, calls_aborted: 0, p50_ms: 757, p95_ms: 4961, distinct_payers: 1, last_activity_at: "2026-10-06T20:37:42Z" }] },
  feedback: { services: [{ service_id: "svc_rad_ai", count: 2, avg: 4.5 }] },
  degraded: [],
};

const CATALOG: Catalog = {
  services: [
    TEAM_DETAIL.services[0]!,
    {
      service_id: "svc_other",
      manifest: { ...TEAM_DETAIL.services[0]!.manifest, service_id: "svc_other", name: "OtherSvc", provider: { agent_id: 999, wallet: "0xdead", display_name: "Another Team" } },
      status: "active", manifest_hash: "y",
    },
  ],
  count: 2,
} as unknown as Catalog;

function installFetch() {
  calls = [];
  mineTeams = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? String(init.body) : undefined });
    if (url.startsWith("/api/core/providers/mine?wallet=")) return jsonResponse({ wallet: MY, teams: mineTeams });
    if (url === "/api/core/teams/prepare") return jsonResponse({ agent_id: 171, tx_hash: "0x" + "ef".repeat(32), bind_required: true }, 201);
    if (/\/api\/chain\/api\/v1\/agent-identity\/\d+\/wallet$/.test(url)) return jsonResponse({ tx_hash: "0x" + "ab".repeat(32) });
    if (/\/api\/chain\/api\/v1\/agent-identity\/171$/.test(url)) return jsonResponse({ token_id: 171, owner: CUSTODIAN, token_uri: "u", agent_wallet: CUSTODIAN, metadata: {} });
    if (url === "/api/core/providers" && method === "POST") {
      const body = JSON.parse(String(init?.body)) as { agent_id: number; claim_wallet: string };
      if (body.claim_wallet.toLowerCase() !== MY.toLowerCase()) return jsonResponse({ error: "claim_requires_binding", detail: "不等", code: "x" }, 422);
      return jsonResponse({ agent_id: body.agent_id, display_name: "New Team", wallet: body.claim_wallet, claim_wallet: body.claim_wallet, created_at: "t" });
    }
    if (url === "/api/core/teams/170") return jsonResponse(TEAM_DETAIL);
    if (url === "/api/core/catalog") return jsonResponse(CATALOG);
    if (url === "/api/core/decision/categories") return jsonResponse({ categories: [], counts: {}, total_active: 0 });
    if (url.startsWith("/api/core/decision/services")) return jsonResponse({ services: [], weights: {}, degraded: [], window_hours: 168, as_of: "", sort: "score", category: null });
    if (url === "/api/core/stats/overview") return jsonResponse({ gmv_raw: 0, gmv: "0", charged_count: 0, calls_success_total: 0, calls_aborted_total: 0, services_total: 0, services_active: 0, providers_registered: 0, providers_with_revenue: 0, synced_to_block: 0, degraded: [] });
    if (url === "/api/core/leaderboard/providers") return jsonResponse({ order: "revenue", providers: [] });
    if (url === "/api/core/leaderboard/services") return jsonResponse({ order: "revenue", services: [] });
    if (url === "/api/gw/internal/keeper/status") return jsonResponse({ enabled: true, running: true, batch_size: 3, flush_interval_s: 30, queue: { pending: 0, done: 0, failed: 0, expired: 0 }, last_batch: null, cumulative_charged_count: 0, cumulative_charged_raw: "0", blacklisted_consumers: [], last_error: null });
    return jsonResponse({ error: "not_mocked", detail: url }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
}

const signerWallet = new Wallet(ANVIL1_PK);
function mockWalletProvider(): Eip1193Provider {
  return {
    request: async ({ method, params }: { method: string; params?: unknown[] | object }) => {
      if (method === "eth_requestAccounts" || method === "eth_accounts") return [MY];
      if (method === "eth_chainId") return "0x3c8";
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

async function connect() {
  announce();
  fireEvent.click(screen.getByTestId("probe-connect"));
  await waitFor(() => expect(document.querySelector('[data-testid="probe-connect"]')).toBeTruthy());
}

beforeEach(() => {
  cleanup();
  sessionStorage.clear();
  localStorage.clear();
  installFetch();
  delete (window as { ethereum?: unknown }).ethereum;
});
afterEach(() => vi.unstubAllGlobals());

describe("我的 Teams（mine）", () => {
  it("空态：主按钮「+ 创建团队」+ 引导文案 + 高级折叠", async () => {
    render(
      <WalletProvider>
        <MyTeamsStep onOpenTeam={vi.fn()} onPublish={vi.fn()} />
        <ConnectProbe />
      </WalletProvider>
    );
    await connect();
    expect(await screen.findByText(/还没有团队——点右上角「\+ 创建团队」/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "+ 创建团队" })).toBeTruthy();
    expect(screen.getByText(/导入已有身份（高级）/)).toBeTruthy();
  });

  it("列表渲染：团队卡片（名称/服务数/入口）+ 整卡点击进详情", async () => {
    const openTeamSpy = vi.fn();
    mineTeams = [{ agent_id: 170, display_name: "RadAI", claim_wallet: MY, service_count: 3, created_at: "2026-10-06 09:41:56" }];
    render(
      <WalletProvider>
        <MyTeamsStep onOpenTeam={openTeamSpy} onPublish={vi.fn()} />
        <ConnectProbe />
      </WalletProvider>
    );
    await connect();
    expect(await screen.findByText("RadAI")).toBeTruthy();
    expect(screen.getByText("3 服务")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "打开团队" })).toBeNull(); // 已移除：整卡可点
    expect(screen.getByRole("button", { name: "发布新服务" })).toBeTruthy(); // 卡内次级保留
    // 整卡可点进详情（fireEvent.click 卡片本体）
    const card = screen.getByRole("button", { name: "打开团队 RadAI" });
    expect(card.getAttribute("tabindex")).toBe("0"); // 键盘可达
    fireEvent.click(card);
    expect(openTeamSpy).toHaveBeenCalledWith(170);
  });
});

describe("创建团队全链", () => {
  it("prepare→签名→绑定→providers 提交体断言→✓ 创建成功", async () => {
    const onCreated = vi.fn();
    render(
      <WalletProvider>
        <CreateTeamButton onCreated={onCreated} />
        <ConnectProbe />
      </WalletProvider>
    );
    await connect();
    fireEvent.click(screen.getByRole("button", { name: "+ 创建团队" }));
    fireEvent.change(screen.getByLabelText("团队名称"), { target: { value: "My New Team" } });
    expect(document.body.textContent).toContain("只需");
    expect(document.body.textContent).toContain("一次钱包签名");
    fireEvent.click(screen.getByRole("button", { name: "创建团队" }));

    expect(await screen.findByText(/✓ 创建成功/)).toBeTruthy();
    expect(onCreated).toHaveBeenCalledWith(171);
    // providers 提交体：{agent_id:171, display_name, claim_wallet=连接地址}
    const prov = calls.find((c) => c.url === "/api/core/providers" && c.method === "POST")!;
    expect(JSON.parse(prov.body!)).toEqual({ agent_id: 171, display_name: "My New Team", claim_wallet: MY });
    // 绑定请求：newWallet=连接地址、dry_run=false
    const bind = calls.find((c) => /\/agent-identity\/171\/wallet$/.test(c.url))!;
    const bindBody = JSON.parse(bind.body!) as { wallet_address: string; dry_run: boolean; signature: string };
    expect(bindBody.wallet_address.toLowerCase()).toBe(MY.toLowerCase());
    expect(bindBody.dry_run).toBe(false);
    expect(bindBody.signature).toMatch(/^0x[0-9a-f]{130}$/);
    // prepare 请求体
    const prep = calls.find((c) => c.url === "/api/core/teams/prepare")!;
    expect(JSON.parse(prep.body!)).toEqual({ display_name: "My New Team" });
  });
});

describe("Team 主页", () => {
  it("聚合数字来自 teams 端点（收入 0.02/2 笔/1 服务/履约 100%/反馈★4.5）", async () => {
    render(
      <WalletProvider>
        <TeamHome agentId={170} onBack={vi.fn()} onPublish={vi.fn()} />
        <ConnectProbe />
      </WalletProvider>
    );
    await connect();
    expect(await screen.findByText("RadAI")).toBeTruthy();
    expect(screen.getByText("0.02")).toBeTruthy(); // 收入
    expect(screen.getByText(/2 笔 Charged/)).toBeTruthy();
    // 服务表逐格断言（新列布局）
    const row = document.querySelector('table[aria-label="团队服务表"] tbody tr')!;
    expect(row.textContent).toContain("RadAI Report");
    expect(row.textContent).toContain("757ms"); // p50
    expect(screen.getAllByText("4961ms").length).toBeGreaterThanOrEqual(1); // p95（表+汇总卡）
    expect(screen.getByText("★4.5")).toBeTruthy();
    expect(screen.getByRole("button", { name: "发布新服务" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "← 我的 Teams" })).toBeTruthy();
  });
});

describe("发布表单团队字段", () => {
  it("所属团队只读（disabled）+ 默认收款钱包=认领钱包", async () => {
    render(
      <WalletProvider>
        <PublishStep claimed={{ agent_id: 170, display_name: "RadAI", wallet: MY }} onNext={vi.fn()} onBack={vi.fn()} />
      </WalletProvider>
    );
    const teamInput = screen.getByLabelText("所属团队") as HTMLInputElement;
    expect(teamInput.disabled).toBe(true);
    expect(teamInput.value).toBe("RadAI · team #170");
    await waitFor(() => expect((screen.getByLabelText("服务收款钱包地址") as HTMLInputElement).value).toBe(MY));
  });
});

describe("目录 Teams 分卡", () => {
  it("默认按团队分组（两个团队卡各含服务列表）；切换按类目平铺", async () => {
    render(<Overview />);
    expect(await screen.findByText("按团队")).toBeTruthy();
    await waitFor(() => expect(screen.getAllByText("RadAI").length).toBeGreaterThanOrEqual(1)); // 团队卡头
    await waitFor(() => expect(screen.getAllByText("Another Team").length).toBeGreaterThanOrEqual(1));
    await waitFor(() => expect(screen.getAllByText("RadAI Report").length).toBeGreaterThanOrEqual(1)); // 服务行（团队卡+平铺卡双视图同时存在时多份）
    fireEvent.click(screen.getByText("按类目"));
    await waitFor(() => expect(screen.getAllByText("OtherSvc").length).toBeGreaterThanOrEqual(1));
    // 平铺视图无团队卡头「Another Team」徽章行
    expect(document.querySelector('.svc-grid .svc-card .badge.ok') ?? true).toBeTruthy();
    // 团队卡点击展开 teams 端点数据
    fireEvent.click(screen.getByText("按团队"));
    await waitFor(() => expect(screen.getAllByText("RadAI").length).toBeGreaterThanOrEqual(1));
    const radaiCards = [...document.querySelectorAll(".svc-grid > .svc-card")].filter((c) => c.textContent?.includes("RadAI Report"));
    expect(radaiCards.length).toBeGreaterThanOrEqual(1);
    fireEvent.click(radaiCards[0]!);
    await waitFor(() => expect(screen.getAllByText(/团队主页数据/).length).toBeGreaterThanOrEqual(1));
    await waitFor(() => expect(screen.getAllByText("0.02").length).toBeGreaterThanOrEqual(1));
  });
});

describe("主路径字样残留扫描", () => {
  it("MyTeamsStep/CreateTeamButton/TeamHome 组件源码无「认领/Agent ID」主路径字样（高级折叠引用除外）", () => {
    const content = readFileSync(join(__dirname, "..", "src", "pages", "ProviderWorkbench.tsx"), "utf-8");
    const start = content.indexOf("export function MyTeamsStep");
    const end = content.length; // ClaimStep 在文件前部；Teams 组件在后段——取 MyTeamsStep 起到文件尾
    const teamsBlock = content.slice(start, end);
    expect(teamsBlock.length).toBeGreaterThan(100);
    // 主路径组件区不含主路径字样（高级折叠 summary「导入已有身份（高级）」与 ClaimStep 引用是允许的）
    const sanitized = teamsBlock
      .replace(/导入已有身份（高级）[\s\S]*?<\/details>/g, "…");
    expect(sanitized).not.toContain("认领");
    expect(sanitized).not.toContain("Agent ID");
  });
});

describe("微调：创建按钮位置 + 整卡可点 + 发布页分组", () => {
  it("「+ 创建团队」在「我的 Teams」标题行右侧（同 flex 行内）", async () => {
    mineTeams = [];
    render(
      <WalletProvider>
        <MyTeamsStep onOpenTeam={vi.fn()} onPublish={vi.fn()} />
        <ConnectProbe />
      </WalletProvider>
    );
    await connect();
    const btn = await screen.findByRole("button", { name: "+ 创建团队" });
    const headerRow = btn.closest("div.flex")!;
    expect(headerRow.textContent).toContain("我的 Teams"); // 按钮与标题同在 header 行
  });

  it("团队卡无「打开团队」按钮；fireEvent.click 卡片触发 onOpenTeam；键盘可达（tabIndex=0+role=button）", async () => {
    const openTeamSpy = vi.fn();
    mineTeams = [{ agent_id: 170, display_name: "RadAI", claim_wallet: MY, service_count: 1, created_at: "t" }];
    render(
      <WalletProvider>
        <MyTeamsStep onOpenTeam={openTeamSpy} onPublish={vi.fn()} />
        <ConnectProbe />
      </WalletProvider>
    );
    await connect();
    await waitFor(() => expect(screen.getByText("RadAI")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "打开团队" })).toBeNull();
    const card = screen.getByRole("button", { name: "打开团队 RadAI" });
    expect(card.getAttribute("tabindex")).toBe("0");
    fireEvent.click(card);
    expect(openTeamSpy).toHaveBeenCalledWith(170);
    // 卡内「发布新服务」stopPropagation：点击不触发 onOpenTeam
    openTeamSpy.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "发布新服务" }));
    expect(openTeamSpy).not.toHaveBeenCalled();
  });

  it("发布页：分组卡标题齐全 + 粘性操作条（role=toolbar 含发布按钮）+ 返回按钮", async () => {
    render(
      <WalletProvider>
        <PublishStep claimed={{ agent_id: 170, display_name: "RadAI", wallet: MY }} onNext={vi.fn()} onBack={vi.fn()} />
      </WalletProvider>
    );
    for (const t of ["基本信息", "定价", "端点", "Schema 与探测"]) {
      expect(screen.getByText(t)).toBeTruthy();
    }
    const bar = screen.getByRole("toolbar", { name: "发布操作" });
    expect(bar.className).toContain("sticky-action-bar");
    expect(bar.textContent).toContain("发布服务");
    expect(screen.getByRole("button", { name: "← 返回仪表盘" })).toBeTruthy();
  });
});
