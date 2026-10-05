/**
 * 浏览器注入钱包（window.ethereum / window.okxwallet，如 OKX、MetaMask）共享模块。
 * 两个工作台（消费端 / Provider）复用同一套连接、切链、签名、发交易逻辑。
 *
 * 铁律：控制台不接触任何私钥——地址只读获取（eth_requestAccounts/eth_accounts），
 * 签名与交易全部由扩展弹窗在本地完成（EIP-1193）。
 *
 * BOT Chain 测试网参数（权威来源 ../coincall-bot-chain-api：
 *   app/core/chains.py（chain_id/rpc/explorer）+ app/modules/accounts.py
 *   的 NATIVE_SYMBOL="BOT"、NATIVE_DECIMALS=18）。
 */
import { BrowserProvider, TransactionReceipt, getAddress } from "ethers";
import { CHAIN_ID, GAS_PRICE_GWEI } from "./constants";
import { getRpcProvider } from "./rpc";

export const BOT_CHAIN_HEX = "0x" + CHAIN_ID.toString(16); // 0x3C8

export const BOT_CHAIN_PARAMS = {
  chainId: BOT_CHAIN_HEX,
  chainName: "BOT Chain Testnet",
  nativeCurrency: { name: "BOT", symbol: "BOT", decimals: 18 },
  rpcUrls: ["https://rpc.bohr.life/"],
  blockExplorerUrls: ["https://scan.bohr.life"],
} as const;

export interface Eip1193Provider {
  request(args: { method: string; params?: unknown[] | object }): Promise<unknown>;
  on?(event: string, handler: (...args: unknown[]) => void): void;
  removeListener?(event: string, handler: (...args: unknown[]) => void): void;
}

/** 检测注入钱包（浏览器里 window === globalThis；测试里可直接挂 globalThis.ethereum）。 */
export function getInjected(): Eip1193Provider | null {
  const g = globalThis as unknown as { ethereum?: Eip1193Provider; okxwallet?: Eip1193Provider };
  return g.ethereum ?? g.okxwallet ?? null;
}

// ---- EIP-1193 错误分类 ----

export function isUserRejected(e: unknown): boolean {
  return (e as { code?: number })?.code === 4001;
}

/** 链未添加（4902；或被包在 -32603 内层，OKX 常见）。 */
export function isUnrecognizedChain(e: unknown): boolean {
  const err = e as { code?: number; data?: { originalError?: { code?: number } } };
  if (err?.code === 4902) return true;
  if (err?.code === -32603 && err?.data?.originalError?.code === 4902) return true;
  return false;
}

export function friendlyInjectedError(e: unknown): { title: string; hint: string } {
  if (isUserRejected(e)) return { title: "你取消了钱包请求", hint: "没有产生任何签名或交易。需要时重试，扩展弹窗里确认即可。" };
  if (isUnrecognizedChain(e)) return { title: "钱包里还没有 BOT Chain 测试网", hint: "将自动调起「添加网络」，请在扩展里确认。" };
  const msg = e instanceof Error ? e.message : String(e);
  return { title: `钱包请求失败：${msg.slice(0, 120)}`, hint: "请确认扩展已解锁且当前账户可用；持续失败可刷新页面重连。" };
}

// ---- 连接与链 ----

/** 主动连接（弹窗）：eth_requestAccounts → [address]，eth_chainId → 968? */
export async function connectInjected(): Promise<{ address: string; chainId: number }> {
  const inj = getInjected();
  if (!inj) throw new Error("未检测到浏览器钱包扩展（window.ethereum / window.okxwallet）。请安装 OKX 或 MetaMask，或展开「没有浏览器钱包？」使用一次性演示钱包。");
  const accounts = (await inj.request({ method: "eth_requestAccounts" })) as string[];
  const address = accounts?.[0];
  if (!address) throw new Error("钱包没有返回账户（可能被拒绝或扩展锁定）。");
  const chainId = await getInjectedChainId();
  return { address: getAddress(address), chainId };
}

/** 静默读取已授权账户（无弹窗；页面刷新恢复会话用）。 */
export async function silentAccounts(): Promise<string | null> {
  const inj = getInjected();
  if (!inj) return null;
  try {
    const accounts = (await inj.request({ method: "eth_accounts" })) as string[];
    return accounts?.[0] ? getAddress(accounts[0]) : null;
  } catch {
    return null;
  }
}

export async function getInjectedChainId(): Promise<number> {
  const inj = getInjected();
  if (!inj) throw new Error("未检测到浏览器钱包扩展。");
  const hex = (await inj.request({ method: "eth_chainId" })) as string;
  return parseInt(hex, 16);
}

/**
 * 确保当前链是 968：先 wallet_switchEthereumChain；钱包报 4902（未添加）
 * 则 wallet_addEthereumChain（BOT Chain 测试网参数）。返回切换后的 chainId。
 */
export async function ensureChain968(): Promise<number> {
  const inj = getInjected();
  if (!inj) throw new Error("未检测到浏览器钱包扩展。");
  const current = await getInjectedChainId();
  if (current === CHAIN_ID) return current;
  try {
    await inj.request({ method: "wallet_switchEthereumChain", params: [{ chainId: BOT_CHAIN_HEX }] });
  } catch (e) {
    if (isUnrecognizedChain(e) || (e as { code?: number })?.code === -32603) {
      await inj.request({ method: "wallet_addEthereumChain", params: [BOT_CHAIN_PARAMS] });
    } else {
      throw e;
    }
  }
  const after = await getInjectedChainId();
  if (after !== CHAIN_ID) throw new Error(`切链后仍不是 968（当前 ${after}）。请在钱包里手动切换到 BOT Chain Testnet。`);
  return after;
}

// ---- 签名与交易 ----

/** ethers BrowserProvider（signTypedData 走扩展，digest 由扩展计算）。 */
export function browserProvider(): BrowserProvider {
  const inj = getInjected();
  if (!inj) throw new Error("未检测到浏览器钱包扩展。");
  return new BrowserProvider(inj);
}

/**
 * 扩展弹窗发交易（eth_sendTransaction）：恒定气价 POA，显式
 * maxFeePerGas = maxPriorityFeePerGas = 20 gwei；gas 用 eth_estimateGas 上浮 30%。
 * 返回交易哈希（等待确认由调用方/ waitForInjectedReceipt 完成）。
 */
export async function sendInjectedTx(from: string, to: string, data: string): Promise<string> {
  const inj = getInjected();
  if (!inj) throw new Error("未检测到浏览器钱包扩展。");
  const fee = "0x" + (BigInt(GAS_PRICE_GWEI) * 10n ** 9n).toString(16); // 20 gwei
  let gasLimit = 120_000n;
  try {
    const est = (await inj.request({ method: "eth_estimateGas", params: [{ from, to, data }] })) as string;
    gasLimit = (BigInt(est) * 13n) / 10n;
  } catch {
    // 估计失败用保守默认值，由链/钱包终裁
  }
  return (await inj.request({
    method: "eth_sendTransaction",
    params: [{ from, to, data, gasLimit: "0x" + gasLimit.toString(16), maxFeePerGas: fee, maxPriorityFeePerGas: fee }],
  })) as string;
}

/** 用公开 RPC 等待注入钱包交易上链（1 确认）。 */
export async function waitForInjectedReceipt(hash: string): Promise<TransactionReceipt> {
  const r = await getRpcProvider().waitForTransaction(hash, 1, 180_000);
  if (!r || r.status !== 1) throw new Error(`交易上链失败：${hash}`);
  return r;
}
