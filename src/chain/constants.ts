/** 链上常量（BOT Chain 测试网 968）。 */

export const CHAIN_ID = 968;
export const CHAIN_NAME = "BOT Chain (bohr testnet)";
export const RPC_URL = "https://rpc.bohr.life/";
export const GAS_PRICE_GWEI = "20"; // 该链 gas 恒 20 gwei
export const EXPLORER_TX = (hash: string) => `https://scan.bohr.life/tx/${hash}`;

export const PAY_VAULT = "0xFe91F55C0e7Ccbc4A6619C67544Ab453cf79C471";
export const MOCK_USDT = "0x4F8f2eaAA3988E9f59B72C93262DDC1084E540fb";
export const IDENTITY_REGISTRY = "0xec8fFbC3c9A34AdDCbB3A14F91db2bd26A8b99c0"; // ERC8004 代理

export const USDT_DECIMALS = 6;

/** 后端地址（经 vite 同源代理，浏览器不跨域）。 */
export const CORE_BASE = "/api/core";
export const GATEWAY_BASE = "/api/gw";
export const BOTCHAIN_BASE = "/api/chain";
/** 展示给消费者的网关直连地址（SDK/调用方实际请求的入口）。 */
export const GATEWAY_PUBLIC_URL = "http://127.0.0.1:8030";

/** ERC-20 / PayVault 函数选择器（构造时由 ethers 计算，避免硬编码漂移）。 */
import { id } from "ethers";
export const SEL = {
  balanceOf: id("balanceOf(address)").slice(0, 10),
  allowance: id("allowance(address,address)").slice(0, 10),
  approve: id("approve(address,uint256)").slice(0, 10),
  mint: id("mint(address,uint256)").slice(0, 10),
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
