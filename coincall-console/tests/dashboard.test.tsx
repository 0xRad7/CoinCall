// @vitest-environment jsdom
/**
 * Provider 仪表盘测试：
 * 收入三卡自动查询（mine+teams+credits 求和与口径公式断言）；提现按钮在未提现卡上；
 * 未连接不渲染 Grid；团队卡列表；Team 详情服务表逐格断言（新列布局含 — 缺失值）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RevenueGrid, TeamHome } from "../src/pages/ProviderWorkbench";
import { WalletProvider, useWallet } from "../src/state/WalletContext";
import type { Eip1193Provider } from "../src/chain/injected";
import { fetchProviderCredits } from "../src/chain/rpc";
import type { TeamDetail } from "../src/api/core";

const MY = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8";
const OTHER_WALLET = "0xAdeE6D874ceC4b8Dba0C969585bd2E593ddfba3D";

const creditsMock = vi.mocked(fetchProviderCredits);
vi.mock("../src/chain/rpc", async (importOriginal) => {
  const mod = await importOriginal<typeof import("../src/chain/rpc")>();
  return { ...mod, fetchProviderCredits: vi.fn() };
});

function teamDetail(agentId: number, name: string, revRaw: number, wallets: string[]): TeamDetail {
  return {
    team: { agent_id: agentId, display_name: name, wallet: wallets[0] ?? MY, claim_wallet: MY, created_at: "2026-10-06 09:41:56" },
    services: [
      {
        service_id: `svc_${agentId}`,
        manifest: {
          service_id: `svc_${agentId}`, name: `${name} Svc`, description: "", version: "1",
          provider: { agent_id: agentId, wallet: wallets[0] ?? MY, display_name: name },
          endpoint: { type: "http_json", url: null, timeout_ms: 30000, method: "GET" },
          pricing: { token: "USDT", model: "per_call", amount: "0.01", amount_raw: "10000" },
          chain: { network: 968 }, input_schema: { type: "object" }, output_schema: { type: "object" },
          status: "active", created_at: "t",
        },
        status: "active", manifest_hash: "x",
      },
    ],
    revenue: { total_raw: revRaw, charged_count: 2, wallets, proof: "/proof" },
    fulfillment: {
      services: [
        { service_id: `svc_${agentId}`, calls_success: 3, calls_aborted: 1, p50_ms: 757, p95_ms: 4961, distinct_payers: 2, last_activity_at: "2026-10-06T20:37:42Z" },
      ],
    },
    degraded: [],
  };
}

const DETAIL_A = teamDetail(170, "RadAI", 30000, [MY]); // 0.03 USDT
const DETAIL_B = teamDetail(171, "Beta", 20000, [OTHER_WALLET]); // 0.02

function jsonResponse(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });
}

function installFetch() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = String(input);
    if (url.startsWith("/api/core/providers/mine?wallet=")) {
      return jsonResponse({
        wallet: MY,
        teams: [
          { agent_id: 170, display_name: "RadAI", claim_wallet: MY, service_count: 1, created_at: "t" },
          { agent_id: 171, display_name: "Beta", claim_wallet: MY, service_count: 1, created_at: "t" },
        ],
      });
    }
    if (url === "/api/core/teams/170") return jsonResponse(DETAIL_A);
    if (url === "/api/core/teams/171") return jsonResponse(DETAIL_B);
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
  localStorage.clear();
  installFetch();
  creditsMock.mockReset();
  delete (window as { ethereum?: unknown }).ethereum;
});
afterEach(() => vi.unstubAllGlobals());

describe("收入 Grid", () => {
  it("未连接不渲染", () => {
    const { container } = render(
      <WalletProvider>
        <RevenueGrid />
      </WalletProvider>
    );
    expect(container.textContent).not.toContain("收入总览");
  });

  it("两卡自动查询：Σ Charged / Σ credits；提现按钮在未提现卡上", async () => {
    // A 收款钱包=MY credits=0.01（10000）；B 收款钱包=OTHER credits=0.005（5000）
    creditsMock.mockImplementation(async (w: string) => (w.toLowerCase() === MY.toLowerCase() ? 10000n : 5000n));

    render(
      <WalletProvider>
        <RevenueGrid />
        <ConnectProbe />
      </WalletProvider>
    );
    announce();
    fireEvent.click(screen.getByTestId("probe-connect"));

    // 总收入 = 30000+20000 = 50000 (0.05)
    expect(await screen.findByText("0.05 USDT")).toBeTruthy();
    expect(screen.getByText(/raw=50000 · 4 笔/)).toBeTruthy(); // 2 teams × 2 笔
    // 未提现 = 10000+5000 = 15000 (0.015)
    expect(screen.getByText("0.015 USDT")).toBeTruthy();
    expect(screen.getByText(/raw=15000 · PayVault credits/)).toBeTruthy();
    // 提现按钮在未提现卡上
    const withdrawBtn = screen.getByRole("button", { name: "提现" });
    expect(withdrawBtn.closest(".stat-card")!.textContent).toContain("未提现收入");
    // credits 逐钱包调用（两个去重后的钱包，小写归一）
    expect(creditsMock).toHaveBeenCalledWith(MY.toLowerCase());
    expect(creditsMock).toHaveBeenCalledWith(OTHER_WALLET.toLowerCase());
    expect(new Set(creditsMock.mock.calls.map((c) => c[0])).size).toBe(2); // 无重复
  });

  it("credits=0 时提现按钮禁用（无收入可提）", async () => {
    creditsMock.mockResolvedValue(0n);
    render(
      <WalletProvider>
        <RevenueGrid />
        <ConnectProbe />
      </WalletProvider>
    );
    announce();
    fireEvent.click(screen.getByTestId("probe-connect"));
    await waitFor(() => expect(screen.getByText("0 USDT")).toBeTruthy());
    expect((screen.getByRole("button", { name: "提现" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("点提现展开逐钱包面板（每个收款钱包一行 credits + 提现全额按钮）", async () => {
    creditsMock.mockImplementation(async (w: string) => (w.toLowerCase() === MY.toLowerCase() ? 10000n : 5000n));
    render(
      <WalletProvider>
        <RevenueGrid />
        <ConnectProbe />
      </WalletProvider>
    );
    announce();
    fireEvent.click(screen.getByTestId("probe-connect"));
    await screen.findByText("0.015 USDT");
    fireEvent.click(screen.getByRole("button", { name: "提现" }));
    expect(await screen.findByText(/提现（PayVault credits → 钱包/)).toBeTruthy();
    const rows = document.querySelectorAll("table tbody tr");
    expect(rows.length).toBe(2); // 两个收款钱包
    expect(document.body.textContent).toContain("0.01 USDT"); // MY 的 credits
    expect(document.body.textContent).toContain("0.005 USDT"); // OTHER 的
    expect(screen.getAllByText("提现全额").length).toBe(2);
  });

  it("credits 查询失败降级：按 0 处理并显示总额（不崩）", async () => {
    creditsMock.mockRejectedValue(new Error("RPC down"));
    render(
      <WalletProvider>
        <RevenueGrid />
        <ConnectProbe />
      </WalletProvider>
    );
    announce();
    fireEvent.click(screen.getByTestId("probe-connect"));
    await waitFor(() => expect(screen.getAllByText("0.05 USDT").length).toBeGreaterThanOrEqual(1)); // 总收入正常
    await waitFor(() => expect(screen.getAllByText("0 USDT").length).toBeGreaterThanOrEqual(1)); // credits 失败按 0
  });
});

describe("Team 详情服务表（新列布局）", () => {
  it("逐格断言：成功/中止/p50/p95/支付者/最近活跃；缺失显示 —", async () => {
    // 一条有数据 + 一条无履约数据（新团队场景）
    const detail: TeamDetail = {
      ...DETAIL_A,
      services: [...DETAIL_A.services, {
        service_id: "svc_new",
        manifest: { ...DETAIL_A.services[0]!.manifest, service_id: "svc_new", name: "Brand New Svc" },
        status: "active", manifest_hash: "y",
      }],
      fulfillment: { services: [...DETAIL_A.fulfillment.services] }, // svc_new 无履约 → —
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
      if (String(input) === "/api/core/teams/170") return jsonResponse(detail);
      return jsonResponse({ error: "x" }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <WalletProvider>
        <TeamHome agentId={170} onBack={vi.fn()} onPublish={vi.fn()} />
        <ConnectProbe />
      </WalletProvider>
    );
    announce();
    fireEvent.click(screen.getByTestId("probe-connect"));

    await waitFor(() => {
      const rows = document.querySelectorAll('table[aria-label="团队服务表"] tbody tr');
      expect(rows.length).toBe(2);
    });
    const [rowA, rowB] = document.querySelectorAll('table[aria-label="团队服务表"] tbody tr');
    // Row A（有履约数据）逐格
    const cellsA = rowA!.querySelectorAll("td");
    expect(cellsA[2]!.textContent).toBe("3"); // 成功
    expect(cellsA[3]!.textContent).toBe("1"); // 中止
    expect(cellsA[4]!.textContent).toBe("757ms"); // p50
    expect(cellsA[5]!.textContent).toBe("4961ms"); // p95
    expect(cellsA[6]!.textContent).toBe("2"); // 支付者
    expect(cellsA[7]!.textContent).toMatch(/小时前|天前/); // 最近活跃
    // Row B（无履约数据）全部 —
    const cellsB = rowB!.querySelectorAll("td");
    expect(cellsB[2]!.textContent).toBe("—");
    expect(cellsB[4]!.textContent).toBe("—");
    expect(cellsB[7]!.textContent).toBe("—");
  });
});
