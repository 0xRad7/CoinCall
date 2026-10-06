/** 链上常量（BOT Chain 测试网 968）。 */

export const CHAIN_ID = 968;
export const CHAIN_NAME = "BOT Chain (bohr testnet)";
export const RPC_URL = "https://rpc.bohr.life/";
export const GAS_PRICE_GWEI = "20"; // 该链 gas 恒 20 gwei
export const EXPLORER_TX = (hash: string) => `https://scan.bohr.life/tx/${hash}`;

/** epoch2（唯一事实源 ../coincall-contracts/deployments/testnet-968.json）。旧 mock 币与旧金库已退役，勿再引用。 */
export const PAY_VAULT = "0xa6E82Fd6648F9Ea8f695c37Edf89f2E5FDb89ff0";
export const USDT = "0x75edC9335175Fc0552D51D48439F229c10420fe3";
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
