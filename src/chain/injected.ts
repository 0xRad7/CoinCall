/**
 * 浏览器注入钱包发现与操作（EIP-6963 优先 + legacy 回退 + 多钱包选择）。
 *
 * 检测决策树：
 *   ① 页面 dispatch eip6963:requestProvider，收集 announceProvider 事件（现代钱包都支持）；
 *   ② legacy 槽位（okxwallet → ethereum.isOkxWallet → ethereum.isMetaMask → ethereum）仅在
 *      该 provider 没被任何 6963 公告覆盖时（引用相等或 OKX/MetaMask rdns 指纹去重）作为额外候选；
 *   ③ 候选 0 个 → 未装扩展提示；1 个 → 直连；≥2 个 → 钱包选择器（记住 rdns，下次默认选中、仍可重选）；
 *   ④ 一切链操作（switch/add/send）都走用户选中的那个 provider 引用，不再重查全局槽位。
 *
 * BOT Chain 测试网参数（权威来源 ../coincall-bot-chain-api：
 *   app/core/chains.py（chain_id/rpc/explorer）+ app/modules/accounts.py
 *   的 NATIVE_SYMBOL="BOT"、NATIVE_DECIMALS=18）。
 * 铁律不变：控制台不接触任何私钥；地址只读，签名/交易在扩展弹窗内完成。
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
  isOkxWallet?: boolean;
  isMetaMask?: boolean;
}

export interface Eip6963Info {
  uuid: string;
  name: string;
  icon: string;
  rdns: string;
}

export interface Eip6963Announcement {
  info: Eip6963Info;
  provider: Eip1193Provider;
}

/** 选择器里的候选（6963 公告或 legacy 槽位推断）。 */
export interface WalletCandidate {
  name: string;
  icon?: string;
  rdns: string | null; // null = legacy 槽位推断，无 rdns
  provider: Eip1193Provider;
  source: "eip6963" | "legacy-okxwallet" | "legacy-ethereum-okx" | "legacy-ethereum-metamask" | "legacy-ethereum";
}

/** 用户实际选中的钱包：持有 provider 引用，后续链操作全部走它。 */
export interface SelectedWallet {
  name: string;
  rdns: string | null;
  provider: Eip1193Provider;
}

// ---- EIP-6963 多注入发现 ----

/** 挂 announceProvider 监听并广播 requestProvider；返回清理函数。target 可注入（测试用）。 */
export function startEip6963Discovery(onAnnounce: (a: Eip6963Announcement) => void, target: EventTarget = defaultTarget()): () => void {
  const handler = (e: Event) => {
    const detail = (e as CustomEvent<Eip6963Announcement>).detail;
    if (detail && detail.info && typeof detail.info.rdns === "string" && detail.provider) onAnnounce(detail);
  };
  target.addEventListener("eip6963:announceProvider", handler as EventListener);
  target.dispatchEvent(new Event("eip6963:requestProvider"));
  return () => target.removeEventListener("eip6963:announceProvider", handler as EventListener);
}

/** 一次性收集（窗口期内到达的全部公告）。 */
export function discoverEip6963(target: EventTarget = defaultTarget(), timeoutMs = 300): Promise<Eip6963Announcement[]> {
  return new Promise((resolve) => {
    const found: Eip6963Announcement[] = [];
    const stop = startEip6963Discovery((a) => found.push(a), target);
    setTimeout(() => {
      stop();
      resolve(found);
    }, timeoutMs);
  });
}

function defaultTarget(): EventTarget {
  if (typeof window !== "undefined") return window;
  return globalThis; // Node 测试里由调用方显式传 EventTarget
}

// ---- EIP-1193 错误分类 ----

export function isUserRejected(e: unknown): boolean {
  const err = e as { code?: unknown; error?: { code?: number }; info?: { error?: { code?: number } } };
  return (
    err?.code === 4001 ||
    err?.code === "ACTION_REJECTED" || // ethers v6 把 4001 包装成此码
    err?.error?.code === 4001 ||
    err?.info?.error?.code === 4001
  );
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

// ---- legacy 槽位回退（EIP-6963 无响应的旧扩展） ----

export function detectLegacyCandidate(
  g: { okxwallet?: Eip1193Provider; ethereum?: Eip1193Provider } = globalThis as { okxwallet?: Eip1193Provider; ethereum?: Eip1193Provider }
): WalletCandidate | null {
  // 顺序即优先级：OKX 专属槽位 > 通用槽位的 OKX 自标识 > MetaMask > 通用兜底（Core 这类只有独占时才命中）
  if (g.okxwallet) return { name: "OKX Wallet", rdns: null, provider: g.okxwallet, source: "legacy-okxwallet" };
  const eth = g.ethereum;
  if (!eth) return null;
  if (eth.isOkxWallet === true) return { name: "OKX Wallet", rdns: null, provider: eth, source: "legacy-ethereum-okx" };
  if (eth.isMetaMask === true) return { name: "MetaMask", rdns: null, provider: eth, source: "legacy-ethereum-metamask" };
  return { name: "浏览器钱包", rdns: null, provider: eth, source: "legacy-ethereum" };
}

// ---- 候选合并与自动决策（纯函数，测试锁定） ----

function looksLike(s: string | null | undefined, needle: string): boolean {
  return typeof s === "string" && s.toLowerCase().includes(needle);
}

/** 6963 公告（到达序）+ 未被公告覆盖的 legacy 候选（追加在末尾）。 */
export function collectCandidates(announcements: Eip6963Announcement[], legacy: WalletCandidate | null): WalletCandidate[] {
  const from6963: WalletCandidate[] = announcements.map((a) => ({
    name: a.info.name,
    icon: a.info.icon,
    rdns: a.info.rdns,
    provider: a.provider,
    source: "eip6963" as const,
  }));
  if (!legacy) return from6963;
  const covered = from6963.some((c) => {
    if (c.provider === legacy.provider) return true; // 同一对象（钱包既公告又占槽位）
    if (legacy.source === "legacy-okxwallet" || legacy.source === "legacy-ethereum-okx") {
      return looksLike(c.rdns, "okx") || looksLike(c.name, "okx");
    }
    if (legacy.source === "legacy-ethereum-metamask") {
      return looksLike(c.rdns, "metamask") || looksLike(c.name, "metamask");
    }
    return false; // 无身份的通用槽位不猜指纹，保留为候选（用户在选择器里自行辨认）
  });
  return covered ? from6963 : [...from6963, legacy];
}

export type Choice =
  | { type: "none" }
  | { type: "auto"; candidate: WalletCandidate }
  | { type: "choice" };

/** 候选 → 决策：0 个 none；1 个 auto；记住的 rdns 命中 auto（默认选中）；否则弹选择器。 */
export function resolveAutoChoice(candidates: WalletCandidate[], rememberedRdns: string | null): Choice {
  if (candidates.length === 0) return { type: "none" };
  if (candidates.length === 1) return { type: "auto", candidate: candidates[0]! };
  if (rememberedRdns) {
    const hit = candidates.find((c) => c.rdns === rememberedRdns);
    if (hit) return { type: "auto", candidate: hit };
  }
  return { type: "choice" };
}

// ---- 连接与链操作（全部显式传入用户选中的 provider） ----

export async function connectInjected(p: Eip1193Provider): Promise<{ address: string; chainId: number }> {
  const accounts = (await p.request({ method: "eth_requestAccounts" })) as string[];
  const address = accounts?.[0];
  if (!address) throw new Error("钱包没有返回账户（可能被拒绝或扩展锁定）。");
  const chainId = await getInjectedChainId(p);
  return { address: getAddress(address), chainId };
}

export async function silentAccounts(p: Eip1193Provider): Promise<string | null> {
  try {
    const accounts = (await p.request({ method: "eth_accounts" })) as string[];
    return accounts?.[0] ? getAddress(accounts[0]) : null;
  } catch {
    return null;
  }
}

export async function getInjectedChainId(p: Eip1193Provider): Promise<number> {
  const hex = (await p.request({ method: "eth_chainId" })) as string;
  return parseInt(hex, 16);
}

/**
 * 确保用户选中的钱包在 968 链：先 wallet_switchEthereumChain；
 * 4902（未添加）则 wallet_addEthereumChain（BOT Chain 测试网参数）。
 */
export async function ensureChain968(p: Eip1193Provider): Promise<number> {
  const current = await getInjectedChainId(p);
  if (current === CHAIN_ID) return current;
  try {
    await p.request({ method: "wallet_switchEthereumChain", params: [{ chainId: BOT_CHAIN_HEX }] });
  } catch (e) {
    if (isUnrecognizedChain(e) || (e as { code?: number })?.code === -32603) {
      await p.request({ method: "wallet_addEthereumChain", params: [BOT_CHAIN_PARAMS] });
    } else {
      throw e;
    }
  }
  const after = await getInjectedChainId(p);
  if (after !== CHAIN_ID) throw new Error(`切链后仍不是 968（当前 ${after}）。请在钱包里手动切换到 BOT Chain Testnet。`);
  return after;
}

/** ethers BrowserProvider（signTypedData 走扩展弹窗，digest 由扩展计算）。 */
export function browserProvider(p: Eip1193Provider): BrowserProvider {
  return new BrowserProvider(p);
}

/**
 * 扩展弹窗发交易（eth_sendTransaction，走用户选中的 provider）：恒定气价 POA，
 * 显式 maxFeePerGas = maxPriorityFeePerGas = 20 gwei；gas 用 eth_estimateGas 上浮 30%。
 */
export async function sendInjectedTx(p: Eip1193Provider, from: string, to: string, data: string): Promise<string> {
  const fee = "0x" + (BigInt(GAS_PRICE_GWEI) * 10n ** 9n).toString(16); // 20 gwei
  let gasLimit = 120_000n;
  try {
    const est = (await p.request({ method: "eth_estimateGas", params: [{ from, to, data }] })) as string;
    gasLimit = (BigInt(est) * 13n) / 10n;
  } catch {
    // 估计失败用保守默认值，由链/钱包终裁
  }
  return (await p.request({
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
