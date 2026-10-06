/**
 * 钱包态（EIP-6963 多注入 + 选择器 + 连接状态机）：
 *
 * 挂载：dispatch eip6963:requestProvider 收集公告 + legacy 槽位回退 → 候选列表
 *   候选 0 → 未装扩展（demo 兜底可见）；1 → 直连；≥2 → 钱包选择器
 *   （记住 rdns：sessionStorage，下次默认选中、仍可「重选钱包」）
 * 连接：requireProvider() → 选中 provider 引用 → eth_requestAccounts（弹窗）→
 *   链 ≠ 968 → ensureChain968（switch → add 兑底；4001 → 友好态保持 connected+wrongChain）
 * 之后一切签名/交易都走选中的 provider（switch/add/send 不再重查全局槽位）。
 *
 * 主路径 = 浏览器扩展（OKX/MetaMask/…）：控制台不接触任何私钥，地址只读。
 * demo 模式 = 一次性随机钱包（无扩展机器兜底，仅测试网、关页即焚）。
 */
import { Wallet, getAddress } from "ethers";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  browserProvider,
  collectCandidates,
  connectInjected,
  detectLegacyCandidate,
  ensureChain968,
  friendlyInjectedError,
  getInjectedChainId,
  resolveAutoChoice,
  sendInjectedTx,
  silentAccounts,
  startEip6963Discovery,
  waitForInjectedReceipt,
  type Eip6963Announcement,
  type SelectedWallet,
  type WalletCandidate,
} from "../chain/injected";
import type { Authorization, PaymentSignature } from "../chain/signing";
import { authorizationTypedData, sigFromJoined, signAuthorization } from "../chain/signing";
import { CHAIN_ID } from "../chain/constants";
import { sendFromLocalWallet, type TxProgress } from "../chain/rpc";
import { TransactionReceipt } from "ethers";

const DEMO_SK_KEY = "coincall.demo.sk";
const CONNECTED_ADDR_KEY = "coincall.connected.addr";
export const WALLET_RDNS_KEY = "coincall.wallet.rdns";
const WALLET_NAME_KEY = "coincall.wallet.name";

export type WalletMode = "injected" | "demo";

export interface FriendlyIssue {
  title: string;
  hint: string;
}

export interface WalletState {
  mode: WalletMode | null;
  address: string | null;
  walletName: string | null; // 实际连上的钱包名（6963 info.name 或 is* 推断）
  chainId: number | null;
  chainOk: boolean;
  candidates: WalletCandidate[];
  rememberedRdns: string | null;
  connecting: boolean;
  switching: boolean;
  issue: FriendlyIssue | null;
  connect: () => Promise<void>;
  /** 供 Provider 工作台等处取「用户选中的 provider」：未选则走同一选择器流程。 */
  requireProvider: () => Promise<SelectedWallet | null>;
  rechoose: () => void; // 清除记忆并重开选择器（仍可换）
  ensureChain: () => Promise<void>;
  disconnect: () => void;
  createDemo: () => void;
  clearIssue: () => void;
  signTypedDataAuth: (auth: Authorization, verifyingContract: string) => Promise<PaymentSignature>;
  sendTransaction: (to: string, data: string, onProgress?: (p: TxProgress) => void) => Promise<TransactionReceipt>;
  /** 仅 demo 模式：导出一次性演示钱包私钥（本机 key 文件下载/水龙头领 gas 用） */
  demoPrivateKey: string | null;
}

const Ctx = createContext<WalletState | null>(null);

function loadDemoWallet(): Wallet | null {
  try {
    const sk = sessionStorage.getItem(DEMO_SK_KEY);
    return sk ? new Wallet(sk) : null;
  } catch {
    return null;
  }
}

export function WalletProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<WalletMode | null>(() => (loadDemoWallet() ? "demo" : null));
  const [demoWallet, setDemoWallet] = useState<Wallet | null>(() => loadDemoWallet());
  const [address, setAddress] = useState<string | null>(() => sessionStorage.getItem(CONNECTED_ADDR_KEY));
  const [chainId, setChainId] = useState<number | null>(null);
  const [candidates, setCandidates] = useState<WalletCandidate[]>([]);
  const [rememberedRdns, setRememberedRdns] = useState<string | null>(() => sessionStorage.getItem(WALLET_RDNS_KEY));
  const [walletName, setWalletName] = useState<string | null>(() => sessionStorage.getItem(WALLET_NAME_KEY));
  const [selected, setSelected] = useState<SelectedWallet | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [issue, setIssue] = useState<FriendlyIssue | null>(null);
  // 选择器：弹层 + 等待用户选择的 promise
  const [selectorOpen, setSelectorOpen] = useState(false);
  const pendingResolveRef = useRef<((s: SelectedWallet | null) => void) | null>(null);
  const mountedRef = useRef(true);
  const selectedRef = useRef<SelectedWallet | null>(null);
  selectedRef.current = selected;
  const modeRef = useRef<WalletMode | null>(mode);
  modeRef.current = mode;
  const candidatesRef = useRef(candidates);
  candidatesRef.current = candidates;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // ---- 挂载：EIP-6963 发现（公告到达序）+ legacy 回退 → 候选 ----
  useEffect(() => {
    const stop = startEip6963Discovery((a) => {
      setCandidates((prev) => {
        if (prev.some((c) => c.source === "eip6963" && c.rdns === a.info.rdns)) return prev;
        const announced: Eip6963Announcement[] = [
          ...prev
            .filter((c) => c.source === "eip6963")
            .map((c) => ({ info: { uuid: "", name: c.name, icon: c.icon ?? "", rdns: c.rdns ?? "" }, provider: c.provider })),
          a,
        ];
        return collectCandidates(announced, detectLegacyCandidate());
      });
    });
    // 立即跑一次 legacy 槽位（0 公告时也要有候选；公告到达后会与 legacy 合并去重）
    setCandidates(collectCandidates([], detectLegacyCandidate()));
    return stop;
  }, []);

  // ---- 挂载：用记住的 rdns 静默恢复连接（eth_accounts，无弹窗） ----
  useEffect(() => {
    const restore = async () => {
      const cands = candidatesRef.current;
      const choice = resolveAutoChoice(cands, rememberedRdns);
      if (choice.type !== "auto") return;
      const sel: SelectedWallet = { name: choice.candidate.name, rdns: choice.candidate.rdns, provider: choice.candidate.provider };
      const addr = await silentAccounts(sel.provider);
      if (!mountedRef.current || !addr) return;
      selectedRef.current = sel;
      setSelected(sel);
      sessionStorage.setItem(CONNECTED_ADDR_KEY, addr);
      sessionStorage.setItem(WALLET_NAME_KEY, sel.name);
      setAddress(addr);
      setWalletName(sel.name);
      setMode((m) => (m === "demo" ? m : "injected"));
      try {
        const cid = await getInjectedChainId(sel.provider);
        if (mountedRef.current) setChainId(cid);
      } catch {
        /* 链 id 读不到时顶栏显示未知链 */
      }
    };
    void restore();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rememberedRdns, candidates.length]);

  // ---- 1193 事件：账户/链变化（只对当前选中的 provider 生效） ----
  useEffect(() => {
    const sel = selected;
    if (!sel) return;
    const onAccounts = (...args: unknown[]) => {
      const accounts = args[0];
      if (!Array.isArray(accounts) || typeof accounts[0] !== "string") return;
      if (accounts.length === 0) {
        if (modeRef.current === "demo") return;
        sessionStorage.removeItem(CONNECTED_ADDR_KEY);
        setAddress(null);
        setMode(null);
        setChainId(null);
      } else {
        const a = getAddress(accounts[0]);
        sessionStorage.setItem(CONNECTED_ADDR_KEY, a);
        setAddress(a);
        setMode((m) => (m === "demo" ? m : "injected"));
      }
    };
    const onChain = (...args: unknown[]) => {
      const hex = args[0];
      if (typeof hex === "string") setChainId(parseInt(hex, 16));
    };
    sel.provider.on?.("accountsChanged", onAccounts);
    sel.provider.on?.("chainChanged", onChain);
    return () => {
      sel.provider.removeListener?.("accountsChanged", onAccounts);
      sel.provider.removeListener?.("chainChanged", onChain);
    };
  }, [selected]);

  // ---- 选择核心：requireProvider（选中 provider 引用，后续链操作全走它） ----
  const requireProvider = useCallback(async (): Promise<SelectedWallet | null> => {
    const cur = selectedRef.current;
    if (cur) return cur;
    let cands = candidatesRef.current;
    if (cands.length === 0) {
      // 公告可能迟到：短暂等一窗再判一次
      await new Promise((r) => setTimeout(r, 350));
      cands = candidatesRef.current;
    }
    const choice = resolveAutoChoice(cands, sessionStorage.getItem(WALLET_RDNS_KEY));
    if (choice.type === "none") {
      setIssue({ title: "未检测到浏览器钱包", hint: "请安装 OKX / MetaMask 扩展后刷新页面重试（未装扩展的演示机可用消费端工作台 ① 的一次性演示钱包兜底）。" });
      return null;
    }
    if (choice.type === "auto") {
      const sel: SelectedWallet = { name: choice.candidate.name, rdns: choice.candidate.rdns, provider: choice.candidate.provider };
      selectedRef.current = sel;
      setSelected(sel);
      return sel;
    }
    // 多候选且无记忆 → 弹选择器，等待用户点选（取消 = null）
    return await new Promise<SelectedWallet | null>((resolve) => {
      pendingResolveRef.current = resolve;
      setSelectorOpen(true);
    });
  }, []);

  const pickCandidate = useCallback((c: WalletCandidate) => {
    const sel: SelectedWallet = { name: c.name, rdns: c.rdns, provider: c.provider };
    if (c.rdns) sessionStorage.setItem(WALLET_RDNS_KEY, c.rdns);
    sessionStorage.setItem(WALLET_NAME_KEY, c.name);
    setRememberedRdns(c.rdns);
    setWalletName(c.name);
    selectedRef.current = sel;
    setSelected(sel);
    setSelectorOpen(false);
    pendingResolveRef.current?.(sel);
    pendingResolveRef.current = null;
  }, []);

  const cancelSelector = useCallback(() => {
    setSelectorOpen(false);
    pendingResolveRef.current?.(null);
    pendingResolveRef.current = null;
  }, []);

  const connect = useCallback(async () => {
    setIssue(null);
    const sel = await requireProvider();
    if (!sel) return;
    setConnecting(true);
    try {
      const { address: addr, chainId: cid } = await connectInjected(sel.provider);
      sessionStorage.setItem(CONNECTED_ADDR_KEY, addr);
      sessionStorage.setItem(WALLET_NAME_KEY, sel.name);
      setAddress(addr);
      setWalletName(sel.name);
      setMode("injected");
      setChainId(cid);
      if (cid !== CHAIN_ID) {
        // 连接后链不对 → 引导切链；切链失败保持 connected+wrongChain（顶栏可重试）
        try {
          setSwitching(true);
          const after = await ensureChain968(sel.provider);
          if (mountedRef.current) setChainId(after);
        } catch (e) {
          if (mountedRef.current) setIssue(friendlyInjectedError(e));
        } finally {
          if (mountedRef.current) setSwitching(false);
        }
      }
    } catch (e) {
      if (mountedRef.current) setIssue(friendlyInjectedError(e));
    } finally {
      if (mountedRef.current) setConnecting(false);
    }
  }, [requireProvider]);

  /** 清除记忆并重开选择器（「仍可换」入口）。 */
  const rechoose = useCallback(() => {
    sessionStorage.removeItem(WALLET_RDNS_KEY);
    sessionStorage.removeItem(WALLET_NAME_KEY);
    setRememberedRdns(null);
    setWalletName(null);
    selectedRef.current = null;
    setSelected(null);
    sessionStorage.removeItem(CONNECTED_ADDR_KEY);
    setAddress(null);
    setMode(null);
    setChainId(null);
    pendingResolveRef.current = null; // 旧等待作废
    if (candidatesRef.current.length >= 2) setSelectorOpen(true);
  }, []);

  const ensureChain = useCallback(async () => {
    const sel = selectedRef.current;
    if (!sel) return;
    setIssue(null);
    setSwitching(true);
    try {
      const cid = await ensureChain968(sel.provider);
      if (mountedRef.current) setChainId(cid);
    } catch (e) {
      if (mountedRef.current) setIssue(friendlyInjectedError(e));
    } finally {
      if (mountedRef.current) setSwitching(false);
    }
  }, []);

  const disconnect = useCallback(() => {
    sessionStorage.removeItem(CONNECTED_ADDR_KEY);
    sessionStorage.removeItem(DEMO_SK_KEY);
    setAddress(null);
    setMode(null);
    setChainId(null);
    setDemoWallet(null);
    setIssue(null);
    selectedRef.current = null;
    setSelected(null);
  }, []);

  const createDemo = useCallback(() => {
    const w = Wallet.createRandom();
    sessionStorage.setItem(DEMO_SK_KEY, w.privateKey);
    sessionStorage.removeItem(CONNECTED_ADDR_KEY);
    setDemoWallet(new Wallet(w.privateKey));
    setAddress(w.address);
    setMode("demo");
    setChainId(null);
    setIssue(null);
  }, []);

  const clearIssue = useCallback(() => setIssue(null), []);

  const signTypedDataAuth = useCallback(
    async (auth: Authorization, verifyingContract: string): Promise<PaymentSignature> => {
      if (mode === "injected" && address) {
        const sel = await requireProvider();
        if (!sel) throw new Error("没有可用的浏览器钱包。");
        // 扩展弹窗签名：digest 由扩展计算；组包时做 v 归一化（0/1→27/28）
        const signer = await browserProvider(sel.provider).getSigner(address);
        const td = authorizationTypedData(auth, verifyingContract);
        const joined = await signer.signTypedData(td.domain, td.types, td.message);
        return sigFromJoined(joined);
      }
      if (mode === "demo" && demoWallet) {
        return signAuthorization(demoWallet, auth, verifyingContract); // 手工 digest（黄金向量锁死）
      }
      throw new Error("钱包未连接：先「连接钱包」，或展开兜底区块创建一次性演示钱包。");
    },
    [mode, address, demoWallet, requireProvider]
  );

  const sendTransaction = useCallback(
    async (to: string, data: string, onProgress?: (p: TxProgress) => void): Promise<TransactionReceipt> => {
      if (mode === "injected" && address) {
        const sel = await requireProvider();
        if (!sel) throw new Error("没有可用的浏览器钱包。");
        onProgress?.({ status: "waiting" });
        const hash = await sendInjectedTx(sel.provider, address, to, data);
        onProgress?.({ status: "pending", hash });
        const receipt = await waitForInjectedReceipt(hash);
        onProgress?.({ status: "confirmed", hash, receipt });
        return receipt;
      }
      if (mode === "demo" && demoWallet) {
        return sendFromLocalWallet(demoWallet, to, data, onProgress);
      }
      throw new Error("钱包未连接。");
    },
    [mode, address, demoWallet, requireProvider]
  );

  const value = useMemo<WalletState>(
    () => ({
      mode,
      address,
      walletName,
      chainId,
      chainOk: mode === "demo" || chainId === CHAIN_ID,
      candidates,
      rememberedRdns,
      connecting,
      switching,
      issue,
      connect,
      requireProvider,
      rechoose,
      ensureChain,
      disconnect,
      createDemo,
      clearIssue,
      signTypedDataAuth,
      sendTransaction,
      demoPrivateKey: mode === "demo" && demoWallet ? demoWallet.privateKey : null,
    }),
    [mode, address, walletName, chainId, candidates, rememberedRdns, connecting, switching, issue, connect, requireProvider, rechoose, ensureChain, disconnect, createDemo, clearIssue, signTypedDataAuth, sendTransaction, demoWallet]
  );

  return (
    <Ctx.Provider value={value}>
      {children}
      {selectorOpen && (
        <WalletSelectorModal candidates={candidates} rememberedRdns={rememberedRdns} onPick={pickCandidate} onCancel={cancelSelector} />
      )}
    </Ctx.Provider>
  );
}

/** 钱包选择器：居中小弹层，按公告到达序，行 = 图标 + 名称 + 「已记住」标记；取消 = 不连接。 */
function WalletSelectorModal({
  candidates,
  rememberedRdns,
  onPick,
  onCancel,
}: {
  candidates: WalletCandidate[];
  rememberedRdns: string | null;
  onPick: (c: WalletCandidate) => void;
  onCancel: () => void;
}) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="选择钱包"
      style={{ position: "fixed", inset: 0, background: "rgba(10,16,28,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 60 }}
      onClick={(e) => e.target === e.currentTarget && onCancel()}
    >
      <div className="card" style={{ maxWidth: 400, margin: 0, width: "92vw" }}>
        <h3 className="mt-0">选择钱包</h3>
        <p className="card-desc" style={{ margin: "0 0 12px" }}>
          检测到 {candidates.length} 个浏览器钱包，选择要用哪个连接（仅读取地址；之后签名/交易都由它弹窗确认）。
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {candidates.map((c) => (
            <button
              key={c.rdns ?? c.name}
              className="wallet-row"
              onClick={() => onPick(c)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "10px 12px",
                borderRadius: 8,
                border: "1px solid var(--border-strong)",
                background: "var(--surface)",
                cursor: "pointer",
                textAlign: "left",
                fontSize: 14,
                fontFamily: "inherit",
              }}
            >
              {c.icon ? (
                <img src={c.icon} alt="" width={22} height={22} style={{ borderRadius: 4 }} />
              ) : (
                <span style={{ width: 22, height: 22, borderRadius: 4, background: "var(--code-bg)", display: "inline-block" }} />
              )}
              <span style={{ fontWeight: 600 }}>{c.name}</span>
              {c.rdns && c.rdns === rememberedRdns && <span className="badge muted">已记住</span>}
              <span className="dim" style={{ marginLeft: "auto", fontSize: 11 }}>
                {c.rdns ?? "legacy 槽位"}
              </span>
            </button>
          ))}
        </div>
        <div className="btn-row" style={{ marginTop: 14, justifyContent: "flex-end" }}>
          <button className="btn secondary" onClick={onCancel}>
            取消（不连接）
          </button>
        </div>
      </div>
    </div>
  );
}

export function useWallet(): WalletState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useWallet 必须在 WalletProvider 内使用");
  return v;
}
