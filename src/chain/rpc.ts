/**
 * 链上交互（只读 + 一次性演示钱包本地直签）：
 * - 只读 eth_call：余额/授权/credits（经公开 RPC，任何模式都用）
 * - sendFromLocalWallet：demo 模式本地直签 raw tx（gas 恒 20 gwei）
 * 扩展（OKX/MetaMask）路径见 injected.ts；控制台主路径不接触任何私钥。
 */
import { JsonRpcProvider, TransactionReceipt, Wallet, formatEther, isHexString } from "ethers";
import { GAS_PRICE_GWEI, USDT, PAY_VAULT, RPC_URL, SEL } from "./constants";

export interface TxProgress {
  /** waiting = 等待钱包扩展弹窗确认；pending = 已广播等待打包 */
  status: "waiting" | "pending" | "confirmed" | "failed";
  hash?: string;
  receipt?: TransactionReceipt | null;
  error?: string;
}

let rpcProvider: JsonRpcProvider | null = null;
export function getRpcProvider(): JsonRpcProvider {
  if (!rpcProvider) rpcProvider = new JsonRpcProvider(RPC_URL, undefined, { staticNetwork: true });
  return rpcProvider;
}

/** 打包 wallet 地址参数 → data。 */
function encodeAddr(fnSel: string, addr: string): string {
  const clean = addr.toLowerCase().replace(/^0x/, "").padStart(64, "0");
  return fnSel + clean;
}

/** 打包 (address, uint256) 参数 → data。 */
export function encodeAddrUint(fnSel: string, addr: string, amount: bigint): string {
  const a = addr.toLowerCase().replace(/^0x/, "").padStart(64, "0");
  const v = amount.toString(16).padStart(64, "0");
  return fnSel + a + v;
}

function decodeUintResult(data: string): bigint {
  if (!isHexString(data) || data.length < 66) throw new Error(`链上返回格式异常: ${data}`);
  return BigInt(data);
}

export async function fetchTokenBalance(wallet: string): Promise<bigint> {
  const r = await getRpcProvider().call({ to: USDT, data: encodeAddr(SEL.balanceOf, wallet) });
  return decodeUintResult(r);
}

export async function fetchAllowance(owner: string, spender: string = PAY_VAULT): Promise<bigint> {
  const a = owner.toLowerCase().replace(/^0x/, "").padStart(64, "0");
  const b = spender.toLowerCase().replace(/^0x/, "").padStart(64, "0");
  const r = await getRpcProvider().call({ to: USDT, data: SEL.allowance + a + b });
  return decodeUintResult(r);
}

export async function fetchProviderCredits(provider: string): Promise<bigint> {
  const r = await getRpcProvider().call({ to: PAY_VAULT, data: encodeAddr(SEL.credits, provider) });
  return decodeUintResult(r);
}

export async function fetchNativeBalance(wallet: string): Promise<string> {
  return formatEther(await getRpcProvider().getBalance(wallet));
}

/** 用本地 ethers Wallet 直签并发送交易（demo 兜底路径；gas 恒 20 gwei，等待 1 确认）。 */
export async function sendFromLocalWallet(
  wallet: Wallet,
  to: string,
  data: string,
  onProgress?: (p: TxProgress) => void
): Promise<TransactionReceipt> {
  const connected = wallet.connect(getRpcProvider());
  const gasPrice = (BigInt(GAS_PRICE_GWEI) * 10n ** 9n).toString();
  const est = await connected.estimateGas({ to, data }).catch(() => 120000n);
  const tx = await connected.sendTransaction({ to, data, gasLimit: (est * 13n) / 10n, gasPrice });
  onProgress?.({ status: "pending", hash: tx.hash });
  const receipt = await tx.wait(1);
  onProgress?.({ status: receipt?.status === 1 ? "confirmed" : "failed", hash: tx.hash, receipt });
  if (!receipt || receipt.status !== 1) throw new Error(`交易上链失败（status=${receipt?.status ?? "null"}）`);
  return receipt;
}

/** 授权 PayVault 可花费 amountRaw USDT（demo 路径 & E2E 使用；epoch2 真 USDT 无公开 mint，资金从水龙头领）。 */
export function approveVault(wallet: Wallet, amountRaw: bigint, onProgress?: (p: TxProgress) => void) {
  return sendFromLocalWallet(wallet, USDT, encodeAddrUint(SEL.approve, PAY_VAULT, amountRaw), onProgress);
}
