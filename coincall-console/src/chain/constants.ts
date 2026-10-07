/**
 * 链上常量：测试网缺省 + Vite 环境变量覆盖（主网切换不改代码；变量清单与主网值见 .env.example）。
 * 铁律：不设任何 VITE_ 变量时 = BOT Chain 测试网现值，行为与参数化前完全一致。
 * 缺省唯一事实源：../coincall-contracts/deployments/testnet-968.json（epoch2）。
 */

/** 字符串 env：未设/空白回落缺省（trim 后判空）。 */
function envStr(value: string | undefined, fallback: string): string {
  const s = typeof value === "string" ? value.trim() : "";
  return s !== "" ? s : fallback;
}

/** 地址 env：设了就必须是 0x+40hex——拼错启动即红，绝不静默回落测试网地址。 */
function envAddr(value: string | undefined, fallback: string): string {
  const s = envStr(value, fallback);
  if (!/^0x[0-9a-fA-F]{40}$/.test(s)) throw new Error(`链地址常量非法：${s}（期望 0x + 20 字节 hex）`);
  return s;
}

/** chainId env：未设回落缺省；设了必须是十进制正整数。 */
function envChainId(value: string | undefined, fallback: number): number {
  const s = typeof value === "string" ? value.trim() : "";
  if (!s) return fallback;
  const n = Number.parseInt(s, 10);
  if (!/^\d+$/.test(s) || !Number.isInteger(n) || n <= 0) {
    throw new Error(`VITE_CHAIN_ID 非法：${s}（期望十进制正整数，如 968 / 677）`);
  }
  return n;
}

/** 取 URL 主机名（展示文案用），兼做 URL 合法性快筛（启动即红优于运行期红）。 */
function hostOf(u: string): string {
  try {
    return new URL(u).host;
  } catch {
    throw new Error(`URL 常量非法：${u}（期望完整 http(s) URL）`);
  }
}

export const CHAIN_ID = envChainId(import.meta.env.VITE_CHAIN_ID, 968);
export const CHAIN_NAME = envStr(import.meta.env.VITE_CHAIN_NAME, "BOT Chain (bohr testnet)");
export const RPC_URL = envStr(import.meta.env.VITE_RPC_URL, "https://rpc.bohr.life/");
export const GAS_PRICE_GWEI = "20"; // 该链 gas 恒 20 gwei（测试网/主网同口径）
/** 浏览器基址；EXPLORER_TX 由它派生。 */
export const EXPLORER_URL = envStr(import.meta.env.VITE_EXPLORER_URL, "https://scan.bohr.life").replace(/\/+$/, "");
export const EXPLORER_TX = (hash: string) => `${EXPLORER_URL}/tx/${hash}`;
/** 展示文案用的主机名（如 scan.bohr.life / scan.botchain.ai）。 */
export const EXPLORER_HOST = hostOf(EXPLORER_URL);
export const RPC_HOST = hostOf(RPC_URL);

/** epoch2 测试网缺省（事实源见文件头注释）。旧 mock 币与旧金库已退役，勿再引用。 */
export const PAY_VAULT = envAddr(import.meta.env.VITE_PAY_VAULT, "0xa6E82Fd6648F9Ea8f695c37Edf89f2E5FDb89ff0");
export const USDT = envAddr(import.meta.env.VITE_USDT, "0x75edC9335175Fc0552D51D48439F229c10420fe3");
/** 测试网水龙头（权威：bot-chain-api chains.py faucet_url）。真 USDT 无公开 mint，从这里领。 */
export const FAUCET_URL = "https://faucet.bohr.life/basic";
export const IDENTITY_REGISTRY = "0xec8fFbC3c9A34AdDCbB3A14F91db2bd26A8b99c0"; // ERC8004 代理

export const USDT_DECIMALS = 6;

/** 后端地址（经 vite 同源代理，浏览器不跨域）。 */
export const CORE_BASE = "/api/core";
export const GATEWAY_BASE = "/api/gw";
export const BOTCHAIN_BASE = "/api/chain";
/**
 * 网关对外基址（单一来源，展示与 SDK 指引共用）：
 * - 默认 = window.location.origin + "/api/gw"（同源代理——三服务只绑 127.0.0.1，局域网唯一可达路径；
 *   本机是 http://127.0.0.1:5173/api/gw，局域网访客自动是其访问 origin，将来域名/反代部署零配置）；
 * - 对外部署时用 .env 的 VITE_GATEWAY_PUBLIC_URL 覆盖（如 https://api.coincall.example），
 *   此时调用端点 = {覆盖值}/call/{id}。
 */
export function gatewayBaseUrl(origin?: string): string {
  const override = import.meta.env.VITE_GATEWAY_PUBLIC_URL as string | undefined;
  if (override) return override.replace(/\/+$/, "");
  const o = origin ?? (typeof window !== "undefined" ? window.location.origin : "");
  return `${o}/api/gw`;
}

export function gatewayCallUrl(serviceId: string, origin?: string): string {
  return `${gatewayBaseUrl(origin)}/call/${serviceId}`;
}

/** ERC-20 / PayVault 函数选择器（构造时由 ethers 计算，避免硬编码漂移）。 */
import { id } from "ethers";
export const SEL = {
  balanceOf: id("balanceOf(address)").slice(0, 10),
  allowance: id("allowance(address,address)").slice(0, 10),
  approve: id("approve(address,uint256)").slice(0, 10),
  credits: id("credits(address)").slice(0, 10),
  providerWithdraw: id("providerWithdraw(address,uint256)").slice(0, 10),
} as const;

/** 人类可读金额 ↔ 最小单位（6 位精度）。 */
export function toRaw(amountHuman: string, decimals = USDT_DECIMALS): bigint {
  const t = amountHuman.trim();
  if (!/^\d+(\.\d+)?$/.test(t)) throw new Error(`金额格式非法: ${t}（应为非负十进制数）`);
  const [i, f = ""] = t.split(".");
  if (f.length > decimals) throw new Error(`精度超出：最多 ${decimals} 位小数`);
  const frac = (f + "0".repeat(decimals)).slice(0, decimals);
  return BigInt(i + frac);
}

export function fromRaw(amountRaw: bigint | string, decimals = USDT_DECIMALS): string {
  const v = BigInt(amountRaw);
  const neg = v < 0n;
  const abs = neg ? -v : v;
  const base = 10n ** BigInt(decimals);
  const i = abs / base;
  const f = (abs % base).toString().padStart(decimals, "0").replace(/0+$/, "");
  return `${neg ? "-" : ""}${i.toString()}${f ? "." + f : ""}`;
}

/** 管理员地址（VITE_ADMIN_ADDRESS 注入；空=不启用门禁，总览对所有人可见——本地开发模式） */
export const ADMIN_ADDRESS = (import.meta.env.VITE_ADMIN_ADDRESS ?? "").toLowerCase();
