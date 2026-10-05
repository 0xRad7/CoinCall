/**
 * 钱包态（连接状态机）：
 *
 *   disconnected ──connect()──▶ connecting ──eth_requestAccounts──▶ connected
 *        │                          │ 4001 拒绝                        │  ├─ chainOk(968)
 *        │                          ▼                                  │  └─ wrongChain ──ensureChain()──▶ chainOk
 *        │                     connected(拒绝提示)                      │ accountsChanged([]) / disconnect()
 *        │                                                             ▼
 *        └──────────── 一次性演示钱包（无扩展机器兜底，demo 模式）◀──── disconnected
 *
 * 主路径 = 浏览器扩展（OKX/MetaMask）：控制台不接触任何私钥，
 * 地址只读获取（eth_requestAccounts），签名/交易全部在扩展弹窗内完成。
 * demo 模式 = 一次性随机钱包，仅测试网演示（关页即焚），与主路径视觉隔离。
 */
import { Wallet, getAddress } from "ethers";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import {
  connectInjected,
  ensureChain968,
  friendlyInjectedError,
  getInjected,
  getInjectedChainId,
  silentAccounts,
} from "../chain/injected";
import type { Authorization, PaymentSignature } from "../chain/signing";
import { authorizationTypedData, sigFromJoined, signAuthorization } from "../chain/signing";
import { CHAIN_ID } from "../chain/constants";
import { browserProvider, sendInjectedTx, waitForInjectedReceipt } from "../chain/injected";
import { sendFromLocalWallet, type TxProgress } from "../chain/rpc";
import { TransactionReceipt } from "ethers";

const DEMO_SK_KEY = "coincall.demo.sk";
const CONNECTED_ADDR_KEY = "coincall.connected.addr";

export type WalletMode = "injected" | "demo";

export interface FriendlyIssue {
  title: string;
  hint: string;
}

export interface WalletState {
  mode: WalletMode | null;
  address: string | null;
  chainId: number | null; // 仅 injected 模式有意义
  chainOk: boolean;
  hasInjected: boolean;
  connecting: boolean;
  switching: boolean;
  issue: FriendlyIssue | null; // 人话问题（拒绝/切链失败等），非红屏错误
  connect: () => Promise<void>;
  ensureChain: () => Promise<void>;
  disconnect: () => void;
  createDemo: () => void;
  clearIssue: () => void;
  /** 支付授权签名：injected → 扩展弹窗 signTypedData；demo → 本地手工 digest（黄金向量路径） */
  signTypedDataAuth: (auth: Authorization, verifyingContract: string) => Promise<PaymentSignature>;
  /** 发交易：injected → eth_sendTransaction（20gwei 固定费率）；demo → 本地直签 raw tx */
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

export function WalletProvider({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<WalletMode | null>(() => (loadDemoWallet() ? "demo" : null));
  const [demoWallet, setDemoWallet] = useState<Wallet | null>(() => loadDemoWallet());
  const [address, setAddress] = useState<string | null>(() => sessionStorage.getItem(CONNECTED_ADDR_KEY));
  const [chainId, setChainId] = useState<number | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [issue, setIssue] = useState<FriendlyIssue | null>(null);
  const [hasInjectedTick, setHasInjectedTick] = useState(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // 启动：静默恢复注入会话（eth_accounts，无弹窗）+ 读链 id + 订阅 1193 事件
  useEffect(() => {
    let cancelled = false;
    const restore = async () => {
      if (!getInjected()) {
        if (mountedRef.current) setHasInjectedTick((t) => t + 1); // 触发重渲染以刷新 hasInjected
        return;
      }
      const addr = await silentAccounts();
      if (cancelled) return;
      if (addr) {
        sessionStorage.setItem(CONNECTED_ADDR_KEY, addr);
        setAddress(addr);
        setMode((m) => (m === "demo" ? m : "injected"));
        try {
          const cid = await getInjectedChainId();
          if (!cancelled && mountedRef.current) setChainId(cid);
        } catch {
          /* 链 id 读不到时顶栏显示未知链 */
        }
      } else if (!loadDemoWallet()) {
        sessionStorage.removeItem(CONNECTED_ADDR_KEY);
        if (mountedRef.current) {
          setAddress(null);
          setMode(null);
        }
      }
    };
    void restore();

    const inj = getInjected();
    const onAccounts = (...args: unknown[]) => {
      const accounts = args[0] as string[];
      if (!Array.isArray(accounts)) return;
      if (accounts.length === 0) {
        sessionStorage.removeItem(CONNECTED_ADDR_KEY);
        setMode((m) => (m === "demo" ? m : null));
        setAddress((a) => (loadDemoWallet() ? a : null));
        setChainId(null);
      } else if (typeof accounts[0] === "string") {
        const a = getAddress(accounts[0]);
        sessionStorage.setItem(CONNECTED_ADDR_KEY, a);
        setMode((m) => (m === "demo" ? m : "injected"));
        setAddress(a);
      }
    };
    const onChain = (...args: unknown[]) => {
      const hex = args[0] as string;
      if (typeof hex === "string") setChainId(parseInt(hex, 16));
    };
    inj?.on?.("accountsChanged", onAccounts);
    inj?.on?.("chainChanged", onChain);
    return () => {
      cancelled = true;
      inj?.removeListener?.("accountsChanged", onAccounts);
      inj?.removeListener?.("chainChanged", onChain);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const connect = useCallback(async () => {
    setIssue(null);
    setConnecting(true);
    try {
      const { address: addr, chainId: cid } = await connectInjected();
      sessionStorage.setItem(CONNECTED_ADDR_KEY, addr);
      setAddress(addr);
      setMode("injected");
      setChainId(cid);
      if (cid !== CHAIN_ID) {
        // 连接后链不对 → 引导切链；切链失败保持 connected+wrongChain（顶栏可重试）
        try {
          setSwitching(true);
          const after = await ensureChain968();
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
  }, []);

  const ensureChain = useCallback(async () => {
    setIssue(null);
    setSwitching(true);
    try {
      const cid = await ensureChain968();
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
        // 扩展弹窗签名：digest 由扩展计算；组包时做 v 归一化（0/1→27/28）
        const signer = await browserProvider().getSigner(address);
        const td = authorizationTypedData(auth, verifyingContract);
        const joined = await signer.signTypedData(td.domain, td.types, td.message);
        return sigFromJoined(joined);
      }
      if (mode === "demo" && demoWallet) {
        return signAuthorization(demoWallet, auth, verifyingContract); // 手工 digest（黄金向量锁死）
      }
      throw new Error("钱包未连接：先「连接钱包」，或展开兜底区块创建一次性演示钱包。");
    },
    [mode, address, demoWallet]
  );

  const sendTransaction = useCallback(
    async (to: string, data: string, onProgress?: (p: TxProgress) => void): Promise<TransactionReceipt> => {
      if (mode === "injected" && address) {
        onProgress?.({ status: "pending" });
        const hash = await sendInjectedTx(address, to, data);
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
    [mode, address, demoWallet]
  );

  const value = useMemo<WalletState>(
    () => ({
      mode,
      address,
      chainId,
      chainOk: mode === "demo" || chainId === CHAIN_ID,
      hasInjected: getInjected() !== null,
      connecting,
      switching,
      issue,
      connect,
      ensureChain,
      disconnect,
      createDemo,
      clearIssue,
      signTypedDataAuth,
      sendTransaction,
      demoPrivateKey: mode === "demo" && demoWallet ? demoWallet.privateKey : null,
    }),
    [mode, address, chainId, connecting, switching, issue, connect, ensureChain, disconnect, createDemo, clearIssue, signTypedDataAuth, sendTransaction, hasInjectedTick, demoWallet]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWallet(): WalletState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useWallet 必须在 WalletProvider 内使用");
  return v;
}
