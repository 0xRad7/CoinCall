import { useEffect } from "react";
// @vitest-environment jsdom
/**
 * 新动线与视觉系统护栏测试：
 * /welcome 单页两态（登录=深色叙事面 / 已连接=身份二选一）→ 双工作台；
 * 模式粘滞（localStorage）与顶栏 pill 切换；主题切换持久化；
 * 「数字+证据」成对组件存在；/ 总览未连接默认落 /welcome、?browse=1 可免连浏览。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "../src/AppShell";
import { RootIndex } from "../src/App";
import Overview from "../src/pages/Overview";
import Welcome from "../src/pages/Welcome";
import { ModeProvider } from "../src/state/ModeContext";
import ConsumerWorkbench from "../src/pages/ConsumerWorkbench";
import { ThemeProvider } from "../src/state/ThemeContext";
import { WalletProvider, useWallet } from "../src/state/WalletContext";
import type { Eip1193Provider } from "../src/chain/injected";

const ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8";

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
    new CustomEvent("eip6963:announceProvider", {
      detail: { info: { uuid: "com.okx.wallet", name: "OKX Wallet", icon: "", rdns: "com.okx.wallet" }, provider },
    })
  );
}

function jsonResponse(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body, headers: new Map(), text: async () => JSON.stringify(body) } as unknown as Response;
}

function installFetch(overviewBody?: unknown) {
  return vi.fn(async (input: RequestInfo | URL): Promise<Response> => {
    const url = String(input);
    if (url === "/api/core/stats/overview") return jsonResponse(overviewBody ?? { gmv_raw: 1234500, gmv: "1.2345", charged_count: 37, calls_success_total: 30, calls_aborted_total: 7, services_total: 5, services_active: 4, providers_registered: 3, providers_with_revenue: 2, synced_to_block: 999, degraded: [] });
    return jsonResponse({ error: "not_mocked", detail: url }, 500);
  });
}

/** 应用骨架（工作台以轻量桩代替——本文件测动线与视觉钩子，不测工作台内部）。 */
function renderApp(initial: string) {
  return render(
    <ThemeProvider>
      <ModeProvider>
        <WalletProvider>
          <MemoryRouter initialEntries={[initial]}>
            <Routes>
              <Route path="/welcome" element={<Welcome />} />
              <Route path="/" element={<AppShell />}>
                <Route index element={<RootIndex />} />
                <Route path="provider" element={<div data-testid="wb-provider">PROVIDER_WORKBENCH</div>} />
                <Route path="consumer" element={<div data-testid="wb-consumer">CONSUMER_WORKBENCH</div>} />
                <Route path="help" element={<div>HELP</div>} />
              </Route>
            </Routes>
          </MemoryRouter>
        </WalletProvider>
      </ModeProvider>
    </ThemeProvider>
  );
}

beforeEach(() => {
  cleanup();
  sessionStorage.clear();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  delete document.documentElement.dataset.mode;
  delete (window as { ethereum?: unknown }).ethereum;
});
afterEach(() => {
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.theme;
  delete document.documentElement.dataset.mode;
});

describe("/welcome 登录态（未连接 = 落地页）", () => {
  it("深色叙事面：标题+三个记忆点+Connect Wallet 大按钮+先逛逛目录+底部链上自证（读 overview）", async () => {
    vi.stubGlobal("fetch", installFetch());
    renderApp("/welcome");
    // 一句话标题（产品叙事；<br/> 分行，用整文断言）
    expect(document.body.textContent).toContain("让 Agent 能力按次收费，");
    expect(document.body.textContent).toContain("让 Agent 调用按次付费");
    // 三个记忆点
    expect(screen.getByText("30 秒上架")).toBeTruthy();
    expect(screen.getByText("0.01 USDT 一次")).toBeTruthy();
    expect(screen.getByText("失败不扣款")).toBeTruthy();
    // 通栏大按钮（同一 6963 连接流）
    expect(screen.getByRole("button", { name: "Connect Wallet" })).toBeTruthy();
    // 次级入口：先逛逛目录（免连进入 /）
    const browse = screen.getByText("先逛逛目录 →");
    expect(browse.closest("a")?.getAttribute("href")).toBe("#/?browse=1");
    // 底部数据自证：GMV + Charged 笔数 + 在售服务（overview mock）+ scan 外链小图标（证据成对）
    await waitFor(() => expect(screen.getByText("1.2345")).toBeTruthy());
    expect(screen.getByText(/37 笔 Charged/)).toBeTruthy();
    expect(screen.getByText(/在售服务/)).toBeTruthy();
    const ev = document.querySelector(".welcome-stats .evidence a.evidence-link");
    expect(ev?.getAttribute("href")).toBe("https://scan.bohr.life");
    // 未连接不出现身份卡
    expect(screen.queryByText("I'm a provider")).toBeNull();
    expect(screen.queryByText("I'm a consumer")).toBeNull();
  });

  it("连接成功后同屏切换为身份选择（I'm a provider / I'm a consumer）", async () => {
    vi.stubGlobal("fetch", installFetch());
    renderApp("/welcome");
    const btn = screen.getByRole("button", { name: "Connect Wallet" });
    announce(mockWalletProvider());
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByText("选择你的身份")).toBeTruthy());
    expect(screen.getByRole("button", { name: /I'm a provider/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /I'm a consumer/ })).toBeTruthy();
    // 身份卡带中文副语
    expect(screen.getByText("创建团队、上架服务、收入直进你的钱包。")).toBeTruthy();
    expect(screen.getByText("浏览服务、授权一次、按次付费调用。")).toBeTruthy();
  });
});

describe("身份选择持久化与动线", () => {
  function renderConnected(initial: string) {
    sessionStorage.setItem("coincall.connected.addr", ADDR); // 已连接（地址恢复路径）
    return renderApp(initial);
  }

  it("选 I'm a consumer → localStorage 粘滞 + 进入 /consumer；再访 /welcome 直落 Consumer 工作台", async () => {
    const { unmount } = renderConnected("/welcome");
    fireEvent.click(screen.getByRole("button", { name: /I'm a consumer/ }));
    expect(screen.getByTestId("wb-consumer")).toBeTruthy();
    expect(localStorage.getItem("coincall.mode")).toBe("consumer");
    expect(document.documentElement.dataset.mode).toBe("consumer");
    unmount();

    // 已连接且已选过身份：直接进对应工作台（/welcome 不再出现）
    sessionStorage.setItem("coincall.connected.addr", ADDR);
    renderApp("/welcome");
    await waitFor(() => expect(screen.getByTestId("wb-consumer")).toBeTruthy());
    expect(screen.queryByText("选择你的身份")).toBeNull();
  });

  it("选 I'm a provider 同理粘滞进 /provider", async () => {
    renderConnected("/welcome");
    fireEvent.click(screen.getByRole("button", { name: /I'm a provider/ }));
    expect(screen.getByTestId("wb-provider")).toBeTruthy();
    expect(localStorage.getItem("coincall.mode")).toBe("provider");
  });

  it("手动返回（?switch=1）：已选身份也不自动跳，停留身份选择可换身份", async () => {
    localStorage.setItem("coincall.mode", "provider");
    sessionStorage.setItem("coincall.connected.addr", ADDR);
    renderApp("/welcome?switch=1");
    expect(screen.getByText("选择你的身份")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /I'm a consumer/ }));
    expect(screen.getByTestId("wb-consumer")).toBeTruthy();
    expect(localStorage.getItem("coincall.mode")).toBe("consumer");
  });
});

describe("顶栏模式 pill 与主题切换", () => {
  it("未连接不出现 pill；经顶栏连接并选过身份后显示身份 pill，点击回 /welcome?switch=1 换身份", async () => {
    vi.stubGlobal("fetch", installFetch());
    localStorage.setItem("coincall.mode", "provider");
    renderApp("/?browse=1");
    // 未连接：无 pill（登录动线在 /welcome），但主题切换常驻
    expect(screen.queryByRole("button", { name: /^(Provider|Consumer|选择身份)$/ })).toBeNull();
    expect(screen.getByRole("button", { name: "切换主题" })).toBeTruthy();

    // 顶栏真实连接路径（announce + 连接钱包按钮）→ 已连接分支渲染模式 pill
    announce(mockWalletProvider());
    fireEvent.click(screen.getByRole("button", { name: "连接钱包" }));
    const pill = await screen.findByRole("button", { name: /^Provider$/ });
    expect(pill.classList.contains("provider")).toBe(true);
    fireEvent.click(pill);
    // 回到身份选择（switch=1 不自动跳，可换身份）
    await waitFor(() => expect(screen.getByText("选择你的身份")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /I'm a consumer/ }));
    await waitFor(() => expect(screen.getByTestId("wb-consumer")).toBeTruthy());
    expect(localStorage.getItem("coincall.mode")).toBe("consumer");
  });

  it("主题切换：点按即翻 data-theme 并持久化 localStorage，再点回亮色", async () => {
    vi.stubGlobal("fetch", installFetch());
    renderApp("/welcome");
    const toggle = screen.getByRole("button", { name: "切换主题" });
    expect(document.documentElement.dataset.theme ?? "light").toBe("light");
    fireEvent.click(toggle);
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("dark"));
    expect(localStorage.getItem("coincall.theme")).toBe("dark");
    fireEvent.click(toggle);
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
    expect(localStorage.getItem("coincall.theme")).toBe("light");
  });
});

describe("/ 总览公共动线", () => {
  it("未连接访问 / 默认落 /welcome；?browse=1 免连进入总览", async () => {
    vi.stubGlobal("fetch", installFetch());
    const { unmount } = renderApp("/");
    await waitFor(() => expect(screen.getByRole("button", { name: "Connect Wallet" })).toBeTruthy());
    unmount();

    renderApp("/?browse=1");
    await waitFor(() => expect(document.querySelector("h1.page-title")?.textContent).toBe("总览"));
    // 侧栏任务分组 + 副标注同屏可见
    expect(screen.getByText("目录与比价")).toBeTruthy();
    expect(screen.getByText("团队与服务")).toBeTruthy();
    expect(screen.getByText("钱包与调用")).toBeTruthy();
  });

  it("总览指标排的 GMV 关键数字与证据成对（哈希缩写 + scan 外链小图标）", async () => {
    vi.stubGlobal("fetch", installFetch());
    render(
      <ThemeProvider>
        <ModeProvider>
          <WalletProvider>
            <Overview />
          </WalletProvider>
        </ModeProvider>
      </ThemeProvider>
    );
    await waitFor(() => expect(screen.getByText("链上 GMV（Charged 总额）")).toBeTruthy());
    const gmvCard = screen.getByText("链上 GMV（Charged 总额）").closest(".stat-card");
    expect(gmvCard?.querySelector(".v")?.textContent).toContain("1.2345"); // 关键数字独立层级
    const link = gmvCard?.querySelector(".evidence a.evidence-link");
    expect(link?.getAttribute("href")).toBe("https://scan.bohr.life");
    expect(link?.getAttribute("aria-label")).toContain("核对");
  });
});


it("守卫：已连接未选身份直进工作台 → 送回 /welcome 不渲染", async () => {
  localStorage.removeItem("coincall.mode");
  window.location.hash = "#/consumer";
  const mockProvider = {
    request: async (args: { method: string }) => {
      if (args.method === "eth_requestAccounts" || args.method === "eth_accounts")
        return ["0x70997970c51812dc3a010c7d01b50e0d17dc79c8"];
      if (args.method === "eth_chainId") return "0x3c8";
      return null;
    },
  };
  let connect: undefined | (() => void);
  render(
    <WalletProvider>
      <CaptureConnect onReady={(c) => (connect = c)} />
      <ModeProvider>
        <ConsumerWorkbench />
      </ModeProvider>
    </WalletProvider>,
  );
  await waitFor(() => expect(connect).toBeTruthy());
  // 6963 公告必须在 WalletProvider 挂载后发出（挂载前的公告会被错过）
  window.dispatchEvent(
    new CustomEvent("eip6963:announceProvider", {
      detail: { info: { uuid: "com.okx.wallet", name: "OKX Wallet", icon: "", rdns: "com.okx.wallet" }, provider: mockProvider },
    }),
  );
  connect!();
  await waitFor(() => expect(window.location.hash).toBe("#/welcome"));
  cleanup();
});


function CaptureConnect({ onReady }: { onReady: (c: () => void) => void }) {
  const w = useWallet();
  useEffect(() => onReady(() => void w.connect()), [w]);
  return null;
}
