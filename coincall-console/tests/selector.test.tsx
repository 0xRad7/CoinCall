// @vitest-environment jsdom
/**
 * 钱包选择器交互测试（jsdom + RTL）：
 * 多注入（OKX 公告 6963 + Core 抢注 window.ethereum）→ 选择器出现 → 选 OKX 并记忆；
 * rdns 记忆恢复（不弹选择器直连上次的钱包）；单注入直连；取消=不连接。
 */
import { beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WalletProvider, WALLET_RDNS_KEY, useWallet } from "../src/state/WalletContext";
import type { Eip1193Provider } from "../src/chain/injected";

const ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8";
const hex968 = "0x3c8";
const OKX_RDNS = "com.okex.wallet";

type Handler = (method: string) => unknown;

function mockWallet(handler: Handler, flags: { isMetaMask?: boolean; isOkxWallet?: boolean } = {}): Eip1193Provider {
  const p: Eip1193Provider = {
    request: ({ method }: { method: string }) => Promise.resolve(handler(method)),
    isMetaMask: flags.isMetaMask,
    isOkxWallet: flags.isOkxWallet,
  };
  return p;
}

/** 探针组件：连接按钮 + 已连接摘要 + eth_requestAccounts 调用计数。 */
function makeProbe() {
  const calls: { okx: number; core: number } = { okx: 0, core: 0 };
  function Probe() {
    const w = useWallet();
    return (
      <div>
        <button onClick={() => void w.connect()}>连接钱包</button>
        <div data-testid="summary">{w.address ? `${w.walletName ?? ""}:${w.address.slice(0, 6)}` : "未连接"}</div>
        <div data-testid="candidates">{w.candidates.map((c) => c.name).join(",")}</div>
      </div>
    );
  }
  return { Probe, calls };
}

/** 装一个 OKX provider（默认授权 ADDR、在 968 链）。 */
function okxProvider(calls?: { okx: number }) {
  return mockWallet((m) => {
    if (m === "eth_requestAccounts") {
      calls && (calls.okx += 1);
      return [ADDR];
    }
    if (m === "eth_accounts") return [ADDR];
    if (m === "eth_chainId") return hex968;
    return null;
  });
}

/** Core 抢注 window.ethereum（无 is* 身份标志，legacy 通用兜底）。 */
function coreProvider(calls?: { core: number }) {
  return mockWallet((m) => {
    if (m === "eth_requestAccounts") {
      calls && (calls.core += 1);
      return ["0xc0Re000000000000000000000000000000000000".slice(0, 42)];
    }
    if (m === "eth_accounts") return [];
    if (m === "eth_chainId") return "0x1";
    return null;
  });
}

/** 让 WalletProvider 收到一条 6963 公告。 */
function announce(provider: Eip1193Provider, rdns: string, name: string) {
  window.dispatchEvent(
    new CustomEvent("eip6963:announceProvider", {
      detail: { info: { uuid: `u-${rdns}`, name, icon: "data:image/svg+xml,x", rdns }, provider },
    })
  );
}

beforeEach(() => {
  cleanup();
  sessionStorage.clear();
  delete (window as { ethereum?: unknown }).ethereum;
});

describe("多注入钱包选择器（OKX 6963 公告 + Core 抢注 ethereum 槽位）", () => {
  it("连接 → 弹选择器（两行）→ 点 OKX → 只用 OKX 连接并记忆 rdns", async () => {
    const { Probe, calls } = makeProbe();
    const okx = okxProvider(calls);
    const core = coreProvider(calls);
    (window as { ethereum?: unknown }).ethereum = core; // Core 抢注通用槽位

    render(
      <WalletProvider>
        <Probe />
      </WalletProvider>
    );
    announce(okx, OKX_RDNS, "OKX Wallet");
    await waitFor(() => expect(screen.getByTestId("candidates").textContent).toContain("OKX Wallet"));
    expect(screen.getByTestId("candidates").textContent).toContain("浏览器钱包"); // Core（legacy 兜底行）

    fireEvent.click(screen.getByText("连接钱包"));
    // 选择器出现：两行钱包
    expect(await screen.findByRole("dialog", { name: "选择钱包" })).toBeTruthy();
    expect(screen.getByText("OKX Wallet")).toBeTruthy();
    expect(screen.getByText("浏览器钱包")).toBeTruthy();

    // 点 OKX 行 → 连接走 OKX 的 provider（Core 从未被 eth_requestAccounts）
    fireEvent.click(screen.getByText("OKX Wallet"));
    await waitFor(() => expect(screen.getByTestId("summary").textContent).toContain("OKX Wallet"));
    expect(screen.getByTestId("summary").textContent).toContain(ADDR.slice(0, 6));
    expect(calls.okx).toBe(1);
    expect(calls.core).toBe(0);
    // 记忆按 rdns 存
    expect(sessionStorage.getItem(WALLET_RDNS_KEY)).toBe(OKX_RDNS);
  });

  it("取消选择器 → 不连接", async () => {
    const { Probe } = makeProbe();
    const okx = okxProvider();
    (window as { ethereum?: unknown }).ethereum = coreProvider();
    render(
      <WalletProvider>
        <Probe />
      </WalletProvider>
    );
    announce(okx, OKX_RDNS, "OKX Wallet");
    await waitFor(() => expect(screen.getByTestId("candidates").textContent).toContain("OKX Wallet"));

    fireEvent.click(screen.getByText("连接钱包"));
    await screen.findByRole("dialog", { name: "选择钱包" });
    fireEvent.click(screen.getByText("取消（不连接）"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByTestId("summary").textContent).toBe("未连接");
  });
});

describe("rdns 记忆恢复", () => {
  it("上次选过 OKX → 再次连接不弹选择器、直接用 OKX", async () => {
    sessionStorage.setItem(WALLET_RDNS_KEY, OKX_RDNS);
    const { Probe, calls } = makeProbe();
    const okx = okxProvider(calls);
    (window as { ethereum?: unknown }).ethereum = coreProvider(calls);
    render(
      <WalletProvider>
        <Probe />
      </WalletProvider>
    );
    announce(okx, OKX_RDNS, "OKX Wallet");
    await waitFor(() => expect(screen.getByTestId("candidates").textContent).toContain("OKX Wallet"));

    fireEvent.click(screen.getByText("连接钱包"));
    await waitFor(() => expect(screen.getByTestId("summary").textContent).toContain("OKX Wallet"));
    expect(screen.queryByRole("dialog")).toBeNull(); // 没弹选择器
    expect(calls.okx).toBe(1);
    expect(calls.core).toBe(0);
  });
});

describe("单注入直连", () => {
  it("只有 OKX 公告且无 legacy 槽位 → 连接不弹选择器", async () => {
    const { Probe, calls } = makeProbe();
    const okx = okxProvider(calls);
    render(
      <WalletProvider>
        <Probe />
      </WalletProvider>
    );
    announce(okx, OKX_RDNS, "OKX Wallet");
    await waitFor(() => expect(screen.getByTestId("candidates").textContent).toContain("OKX Wallet"));

    fireEvent.click(screen.getByText("连接钱包"));
    await waitFor(() => expect(screen.getByTestId("summary").textContent).toContain("OKX Wallet"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.okx).toBe(1);
  });
});
