// @vitest-environment jsdom
/**
 * 全站连接入口测试：未连接时顶栏出现「连接钱包」且点击走 mock 连接后 chip 出现；
 * 发布表单内联按钮（未连接=连接自动填 / 已连接=使用当前钱包快填，手改后仍可一键填回）；
 * 身份钱包绑定未连接时的内联入口；「去消费端」类指路文案残留扫描。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { MemoryRouter } from "react-router-dom";
import { AppShell } from "../src/AppShell";
import { BindIdentityWallet } from "../src/components/BindIdentityWallet";
import { PublishStep } from "../src/pages/ProviderWorkbench";
import { WalletProvider, useWallet } from "../src/state/WalletContext";
import type { Eip1193Provider } from "../src/chain/injected";

const ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8";
const OWNER = "0xC37fFE97B4D2C3D0187B1dDEDF273E52A461B63a";

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

/** 连接探针：调 w.connect()（与顶栏/内联按钮同一代码路径）。 */
function ConnectProbe() {
  const w = useWallet();
  return (
    <button onClick={() => void w.connect()} data-testid="probe-connect">
      探针连接
    </button>
  );
}

beforeEach(() => {
  cleanup();
  sessionStorage.clear();
  delete (window as { ethereum?: unknown }).ethereum;
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("顶栏全局连接入口", () => {
  it("未连接：顶栏出现「连接钱包」；点击后连接成功 → chip（地址+链徽标+断开）出现", async () => {
    render(
      <WalletProvider>
        <MemoryRouter>
          <AppShell />
        </MemoryRouter>
      </WalletProvider>
    );
    const barBtn = screen.getByRole("button", { name: "连接钱包" });
    expect(barBtn).toBeTruthy();
    expect(screen.getByText(/未连接钱包——签名与交易需要浏览器钱包/)).toBeTruthy();

    announce(mockWalletProvider());
    fireEvent.click(barBtn);
    await waitFor(() => expect(screen.getByText(new RegExp(ADDR.slice(0, 8)))).toBeTruthy());
    expect(screen.getByText(`链 968 ✓`)).toBeTruthy();
    expect(screen.getByText("断开")).toBeTruthy();
    // 连接后顶栏不再显示「连接钱包」按钮
    expect(screen.queryByRole("button", { name: "连接钱包" })).toBeNull();
  });
});

describe("顶栏钱包簇右对齐", () => {
  it("wallet-bar 为右对齐 header：钱包控件收拢在 wallet-cluster 钩子内贴右上角（小屏可换行仍右对齐）", () => {
    render(
      <WalletProvider>
        <MemoryRouter>
          <AppShell />
        </MemoryRouter>
      </WalletProvider>
    );
    const bar = document.querySelector(".wallet-bar");
    expect(bar).toBeTruthy();
    // 钱包簇是顶栏容器的直接子节点，「连接钱包」按钮收拢在簇内（布局钩子存在）
    const cluster = bar?.firstElementChild ?? null;
    expect(cluster?.classList.contains("wallet-cluster")).toBe(true);
    const connectBtn = screen.getByRole("button", { name: "连接钱包" });
    expect(cluster?.contains(connectBtn)).toBe(true);
    // jsdom 不做真布局：断言样式表钩子——顶栏右对齐、簇内换行后仍右对齐
    const css = readFileSync(join(__dirname, "..", "src", "styles.css"), "utf-8");
    const barRule = css.match(/\.wallet-bar\s*\{[^}]*\}/)?.[0] ?? "";
    expect(barRule).toContain("justify-content: flex-end");
    const clusterRule = css.match(/\.wallet-cluster\s*\{[^}]*\}/)?.[0] ?? "";
    expect(clusterRule).toContain("justify-content: flex-end");
    expect(clusterRule).toContain("flex-wrap: wrap");
  });
});

describe("发布表单内联入口", () => {
  function renderPublish() {
    return render(
      <WalletProvider>
        <PublishStep claimed={{ agent_id: 169, display_name: "Demo Booth", wallet: ADDR }} onNext={vi.fn()} onBack={vi.fn()} />
        <ConnectProbe />
      </WalletProvider>
    );
  }

  it("未连接：字段旁「连接钱包自动填」；连接成功后自动填入地址", async () => {
    renderPublish();
    expect(screen.getByRole("button", { name: "连接钱包自动填" })).toBeTruthy();
    expect(screen.getByPlaceholderText("0x…（连接钱包自动填入，或手动填写）")).toBeTruthy();

    announce(mockWalletProvider());
    fireEvent.click(screen.getByTestId("probe-connect"));
    await waitFor(() => expect((screen.getByLabelText("服务收款钱包地址") as HTMLInputElement).value).toBe(ADDR));
    // 已连接形态：快填按钮
    expect(screen.getByRole("button", { name: new RegExp(`使用当前钱包 ${ADDR.slice(0, 6)}`) })).toBeTruthy();
  });

  it("「使用当前钱包」快填与手改互不冲突：改走别的地址后按钮仍在，一键填回", async () => {
    renderPublish();
    announce(mockWalletProvider());
    fireEvent.click(screen.getByTestId("probe-connect"));
    const input = screen.getByLabelText("服务收款钱包地址") as HTMLInputElement;
    await waitFor(() => expect(input.value).toBe(ADDR));

    // 手改成别的地址
    fireEvent.change(input, { target: { value: "0x000000000000000000000000000000000000dEaD" } });
    expect(input.value).toBe("0x000000000000000000000000000000000000dEaD");
    // 按钮仍在
    const quick = screen.getByRole("button", { name: new RegExp("使用当前钱包") });
    expect(quick).toBeTruthy();
    // 一键填回当前连接地址
    fireEvent.click(quick);
    expect(input.value).toBe(ADDR);
  });
});

describe("身份钱包绑定的内联入口", () => {
  it("未连接：显示内联「连接钱包」按钮（无跨页指路文案）", () => {
    render(
      <WalletProvider>
        <BindIdentityWallet agentId={169} owner={OWNER} onBound={vi.fn()} />
      </WalletProvider>
    );
    expect(screen.getByRole("button", { name: "连接钱包" })).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/先到.+消费端|去消费端/);
  });
});

describe("「去消费端」类指路文案残留扫描", () => {
  const root = join(__dirname, "..", "src");
  const files = [
    "pages/ProviderWorkbench.tsx",
    "components/BindIdentityWallet.tsx",
    "components/IdentityRegister.tsx",
    "components/ConnectWalletButton.tsx",
    "state/WalletContext.tsx",
    "AppShell.tsx",
  ];
  it("Provider 侧流程文件无跨页连接指路（连接一律内联/顶栏）", () => {
    for (const f of files) {
      const content = readFileSync(join(root, f), "utf-8");
      const hits = content.match(/(先到|去|可先到)消费端|(或本页刷新后的顶栏)/);
      expect(hits, `${f} 残留指路文案: ${hits?.join(",")}`).toBeNull();
    }
  });
});
