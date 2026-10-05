/**
 * 链上交互：只读 eth_call（余额/授权/credits）、本地私钥直签交易（mint/approve）、
 * 注入钱包（window.ethereum）交易（providerWithdraw / signTypedData）。
 * 私钥只在本页面进程内使用，绝不出现在任何网络请求体中（签名/RPC 消息除外，均为标准操作）。
 */
import { JsonRpcProvider, TransactionReceipt, Wallet, formatEther, isHexString } from "ethers";
import { GAS_PRICE_GWEI, MOCK_USDT, PAY_VAULT, RPC_URL, SEL } from "./constants";

export interface TxProgress {
  status: "pending" | "confirmed" | "failed";
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
  const r = await getRpcProvider().call({ to: MOCK_USDT, data: encodeAddr(SEL.balanceOf, wallet) });
  return decodeUintResult(r);
}

export async function fetchAllowance(owner: string, spender: string = PAY_VAULT): Promise<bigint> {
  const a = owner.toLowerCase().replace(/^0x/, "").padStart(64, "0");
  const b = spender.toLowerCase().replace(/^0x/, "").padStart(64, "0");
  const r = await getRpcProvider().call({ to: MOCK_USDT, data: SEL.allowance + a + b });
  return decodeUintResult(r);
}

export async function fetchProviderCredits(provider: string): Promise<bigint> {
  const r = await getRpcProvider().call({ to: PAY_VAULT, data: encodeAddr(SEL.credits, provider) });
  return decodeUintResult(r);
}

export async function fetchNativeBalance(wallet: string): Promise<string> {
  return formatEther(await getRpcProvider().getBalance(wallet));
}

/** 用本地 ethers Wallet 直签并发送交易（gas 恒 20 gwei，等待 1 确认）。 */
export async function sendFromLocalWallet(
  wallet: Wallet,
  to: string,
  data: string,
  onProgress?: (p: TxProgress) => void
): Promise<TransactionReceipt> {
  const connected = wallet.connect(getRpcProvider());
  const gasPrice = (BigInt(GAS_PRICE_GWEI) * 10n ** 9n).toString();
  const est = await connected.estimateGas({ to, data }).catch(() => 120000n);
  const tx = await connected.sendTransaction({ to, data, gasLimit: est * 13n / 10n, gasPrice });
  onProgress?.({ status: "pending", hash: tx.hash });
  const receipt = await tx.wait(1);
  onProgress?.({ status: receipt?.status === 1 ? "confirmed" : "failed", hash: tx.hash, receipt });
  if (!receipt || receipt.status !== 1) throw new Error(`交易上链失败（status=${receipt?.status ?? "null"}）`);
  return receipt;
}

/** 铸造 amountRaw MockUSDT（公开 mint，仅测试网）。 */
export function mintMockUsdt(wallet: Wallet, to: string, amountRaw: bigint, onProgress?: (p: TxProgress) => void) {
  return sendFromLocalWallet(wallet, MOCK_USDT, encodeAddrUint(SEL.mint, to, amountRaw), onProgress);
}

/** 授权 PayVault 可花费 amountRaw MockUSDT。 */
export function approveVault(wallet: Wallet, amountRaw: bigint, onProgress?: (p: TxProgress) => void) {
  return sendFromLocalWallet(wallet, MOCK_USDT, encodeAddrUint(SEL.approve, PAY_VAULT, amountRaw), onProgress);
}

// ---- 注入钱包（window.ethereum，如 OKX） ----

export interface InjectedProvider {
  request(args: { method: string; params?: unknown[] | object }): Promise<unknown>;
  isMetaMask?: boolean;
}

export function getInjected(): InjectedProvider | null {
  const eth = (window as unknown as { ethereum?: InjectedProvider }).ethereum;
  return eth ?? null;
}

export async function injectedSignTypedData(
  signerAddress: string,
  typedData: {
    domain: Record<string, unknown>;
    types: Record<string, Array<{ name: string; type: string }>>;
    primaryType: string;
    message: Record<string, unknown>;
  }
): Promise<string> {
  const inj = getInjected();
  if (!inj) throw new Error("未检测到浏览器注入钱包（window.ethereum）。请安装 OKX/MetaMask，或改用「粘贴本地签名」模式。");
  return (await inj.request({
    method: "eth_signTypedData_v4",
    params: [signerAddress, JSON.stringify(typedData)],
  })) as string;
}

export async function injectedSendProviderWithdraw(to: string, amountRaw: bigint): Promise<string> {
  const inj = getInjected();
  if (!inj) throw new Error("未检测到浏览器注入钱包。providerWithdraw 需要用收款钱包本人发起（铁律 P8），请安装 OKX/MetaMask 后重试。");
  const accounts = (await inj.request({ method: "eth_requestAccounts" })) as string[];
  const from = accounts?.[0];
  if (!from) throw new Error("注入钱包未返回账户。");
  const params = [
    {
      from,
      to: PAY_VAULT,
      data: encodeAddrUint(SEL.providerWithdraw, to, amountRaw),
      gasPrice: (BigInt(GAS_PRICE_GWEI) * 10n ** 9n).toString(16).replace(/^0x/, "0x"),
    },
  ];
  return (await inj.request({ method: "eth_sendTransaction", params })) as string;
}

export async function waitReceipt(hash: string): Promise<TransactionReceipt | null> {
  return getRpcProvider().waitForTransaction(hash, 1, 180_000);
}

/** 跟踪一笔（注入钱包发起的）交易：pending → confirmed/failed。 */
export async function trackInjectedTx(hash: string, onProgress?: (p: TxProgress) => void): Promise<TransactionReceipt> {
  onProgress?.({ status: "pending", hash });
  const r = await waitReceipt(hash);
  onProgress?.({ status: r?.status === 1 ? "confirmed" : "failed", hash, receipt: r });
  if (!r || r.status !== 1) throw new Error(`交易上链失败: ${hash}`);
  return r;
}
