/**
 * 消费者本地付费钱包（仅测试网演示用途）：
 * - 私钥只存 sessionStorage（关页即清），绝不写 localStorage、绝不上传；
 * - 任何网络请求不携带私钥本体；签名在浏览器进程内完成。
 */
import { Wallet, getAddress } from "ethers";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

const SK_KEY = "coincall.consumer.sk";
const ADDR_KEY = "coincall.consumer.addr";

export interface WalletState {
  wallet: Wallet | null;
  address: string | null;
  create: () => void;
  importKey: (privateKey: string) => void; // throws 人话错误
  disconnect: () => void;
}

const Ctx = createContext<WalletState | null>(null);

function loadWallet(): Wallet | null {
  try {
    const sk = sessionStorage.getItem(SK_KEY);
    if (!sk) return null;
    return new Wallet(sk);
  } catch {
    return null;
  }
}

export function WalletProvider({ children }: { children: React.ReactNode }) {
  const [wallet, setWallet] = useState<Wallet | null>(() => loadWallet());

  useEffect(() => {
    const onStorage = () => setWallet(loadWallet());
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const create = useCallback(() => {
    const w = Wallet.createRandom();
    sessionStorage.setItem(SK_KEY, w.privateKey);
    sessionStorage.setItem(ADDR_KEY, w.address);
    setWallet(new Wallet(w.privateKey));
  }, []);

  const importKey = useCallback((privateKey: string) => {
    const t = privateKey.trim();
    if (!/^0x[0-9a-fA-F]{64}$/.test(t)) {
      throw new Error("私钥格式不正确：应为 0x 开头的 64 位十六进制（32 字节）。");
    }
    let w: Wallet;
    try {
      w = new Wallet(t);
    } catch {
      throw new Error("私钥无法解析为有效钱包。");
    }
    sessionStorage.setItem(SK_KEY, w.privateKey);
    sessionStorage.setItem(ADDR_KEY, w.address);
    setWallet(new Wallet(w.privateKey));
  }, []);

  const disconnect = useCallback(() => {
    sessionStorage.removeItem(SK_KEY);
    sessionStorage.removeItem(ADDR_KEY);
    setWallet(null);
  }, []);

  const value = useMemo<WalletState>(
    () => ({
      wallet,
      address: wallet ? getAddress(wallet.address) : null,
      create,
      importKey,
      disconnect,
    }),
    [wallet, create, importKey, disconnect]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWallet(): WalletState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useWallet 必须在 WalletProvider 内使用");
  return v;
}
