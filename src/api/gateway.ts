/** coincall-gateway（数据面，8030）与 coincall-bot-chain-api（链 API，8010）封装。 */
import { apiFetch, jsonInit } from "./client";
import { BOTCHAIN_BASE, GATEWAY_BASE } from "../chain/constants";
import type { Authorization } from "../chain/signing";

// ---- gateway ----

export interface CallReceipt {
  receiptId: string | null;
  chargedRaw: string | null;
  receiptSig: string | null;
}

export interface Gateway402Challenge {
  error: string;
  detail: string;
  code: string; // payment_missing | insufficient_balance | insufficient_allowance | …
  service_id: string;
  pricing: { amount: string; amount_raw: string; token: string };
  wallet_balance_raw: string;
  payment: {
    scheme: string;
    header: string;
    domain: { name: string; version: string; chainId: number; verifyingContract: string };
    approve_to: string;
  };
  trace_id: string;
}

export interface CallOutcome {
  ok: boolean;
  status: number;
  body: unknown;
  receipt: CallReceipt;
  challenge?: Gateway402Challenge;
}

export const gatewayApi = {
  /** 付费调用：headers 由调用方（消费者工作台）组装，这里只发请求并归一结果。 */
  call: async (serviceId: string, params: unknown, headers: Record<string, string>): Promise<CallOutcome> => {
    const r = await apiFetch<unknown>(`${GATEWAY_BASE}/call/${serviceId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers },
      body: JSON.stringify(params),
      timeoutMs: 60_000,
    });
    return {
      ok: true,
      status: r.status,
      body: r.data,
      receipt: {
        receiptId: r.headers.get("X-Receipt-Id"),
        chargedRaw: r.headers.get("X-Charged-Raw"),
        receiptSig: r.headers.get("X-Receipt-Sig"),
      },
    };
  },
  keeperStatus: () =>
    apiFetch<{
      enabled: boolean;
      running: boolean;
      batch_size: number;
      flush_interval_s: number;
      queue: { pending: number; done: number; failed: number; expired: number };
      last_batch: unknown;
      cumulative_charged_count: number;
      cumulative_charged_raw: string;
      blacklisted_consumers: string[];
      last_error: string | null;
    }>(`${GATEWAY_BASE}/internal/keeper/status`).then((r) => r.data),
};

// ---- bot-chain-api ----

export interface AgentIdentity {
  token_id: number;
  owner: string;
  token_uri: string;
  agent_wallet: string;
  metadata: Record<string, unknown>;
}

/** 真实注册（dry_run=false）的回执摘要（8010 TxService TxReceiptSummary）。 */
export interface IdentityRegisterReceipt {
  dry_run: boolean;
  tx_hash: string;
  status: number;
  block_number: number;
  gas_used?: number;
  from_address?: string;
}

/** register-result：Transfer mint 解析（未上链 found=false）。 */
export interface IdentityRegisterResult {
  found: boolean;
  tx_hash: string;
  status?: number | null;
  block_number?: number | null;
  agent_ids: number[];
  owner?: string | null;
  agent_wallet?: string | null;
}

export interface WalletBindingResult {
  tx_hash: string;
  explorer_url?: string;
  deadline?: number;
  dry_run?: boolean;
  [k: string]: unknown;
}

export const botChainApi = {
  identity: (tokenId: number) =>
    apiFetch<AgentIdentity>(`${BOTCHAIN_BASE}/api/v1/agent-identity/${tokenId}`).then((r) => r.data),
  bindWallet: (tokenId: number, walletAddress: string, signature: string, deadline: number) =>
    apiFetch<WalletBindingResult>(`${BOTCHAIN_BASE}/api/v1/agent-identity/${tokenId}/wallet`, {
      ...jsonInit("POST", {
        wallet_address: walletAddress,
        signature,
        deadline,
        dry_run: false, // 默认 true 只预览；控制台目标是真绑定
      }),
      timeoutMs: 60_000,
    }).then((r) => r.data),
  /** 注册新 agent 身份：接口默认 dry_run=true 仅预览，这里传 false 真实上链（服务端出资账户代发）。 */
  registerIdentity: (agentUri: string) =>
    apiFetch<IdentityRegisterReceipt>(`${BOTCHAIN_BASE}/api/v1/agent-identity/register`, {
      ...jsonInit("POST", { agent_uri: agentUri, dry_run: false }),
      timeoutMs: 60_000,
    }).then((r) => r.data),
  registerResult: (txHash: string) =>
    apiFetch<IdentityRegisterResult>(`${BOTCHAIN_BASE}/api/v1/agent-identity/register-result/${txHash}`).then((r) => r.data),
};

/** EIP-712 typed data：AgentWalletSet（钱包绑定，签名者 = 新钱包本人）。 */
export function agentWalletSetTypedData(args: {
  agentId: number;
  newWallet: string;
  owner: string;
  deadline: number;
  verifyingContract: string;
  chainId: number;
}) {
  return {
    domain: {
      name: "ERC8004IdentityRegistry",
      version: "1",
      chainId: args.chainId,
      verifyingContract: args.verifyingContract,
    },
    primaryType: "AgentWalletSet",
    types: {
      AgentWalletSet: [
        { name: "agentId", type: "uint256" },
        { name: "newWallet", type: "address" },
        { name: "owner", type: "address" },
        { name: "deadline", type: "uint256" },
      ],
    } as Record<string, Array<{ name: string; type: string }>>,
    message: {
      agentId: args.agentId,
      newWallet: args.newWallet,
      owner: args.owner,
      deadline: args.deadline,
    },
  };
}

/** 六元组 → X-Api-Key + X-PAYMENT + X-Idempotency-Key 请求头（签名由 signing 层完成）。 */
export function buildCallHeaders(apiKey: string, paymentHeader: string, idempotencyKey: string): Record<string, string> {
  return { "X-Api-Key": apiKey, "X-PAYMENT": paymentHeader, "X-Idempotency-Key": idempotencyKey };
}

export type { Authorization };
