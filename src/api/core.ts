/** coincall-core（注册面，8020）API 封装。 */
import { apiFetch, jsonInit } from "./client";
import { CORE_BASE } from "../chain/constants";

export interface ServiceManifest {
  service_id: string;
  name: string;
  description: string;
  version: string;
  provider: { agent_id: number; wallet: string; display_name: string };
  endpoint: { type: "http_json" | "internal"; url: string | null; timeout_ms: number; method?: "GET" | "POST" };
  pricing: { token: string; model: string; amount: string; amount_raw: string };
  chain: { network: number };
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  status: string;
  created_at?: string;
  category?: string;
  tags?: string[];
}

export interface CatalogService {
  service_id: string;
  manifest: ServiceManifest;
  status: string;
  manifest_hash: string;
}

export interface Catalog {
  services: CatalogService[];
  count: number;
}

export interface StatsOverview {
  gmv_raw: number;
  gmv: string;
  charged_count: number;
  calls_success_total: number | null;
  calls_aborted_total: number | null;
  services_total: number;
  services_active: number;
  providers_registered: number;
  providers_with_revenue: number;
  synced_to_block: number;
  degraded: string[];
}

export interface ProviderRevenueRow {
  wallet: string;
  display_name: string | null;
  agent_id: number | null;
  revenue_raw: number;
  revenue: string;
  charged_count: number;
}

export interface ServiceStatsRow {
  service_id: string;
  name: string;
  status: string;
  provider_wallet: string;
  provider_agent_id: number | null;
  display_name: string | null;
  revenue_raw: number;
  revenue: string;
  charged_count: number;
  calls_success: number | null;
  calls_aborted: number | null;
  fail_rate: number | null;
  last_call_at: string | null;
}

export interface ProofEvent {
  tx_hash: string;
  explorer_url: string;
  log_index: number;
  block_number: number;
  value_raw: number;
  value: string;
  nonce: string;
}

export interface ProofResponse {
  provider_wallet: string;
  revenue_raw: number;
  revenue: string;
  count: number;
  events: ProofEvent[];
  explorer_url_base?: string;
}

export interface ApiKeyRow {
  key_id: string;
  consumer_wallet: string;
  quota_raw: number | null;
  status: string;
  created_at: string;
}

/** 服务端实检结果（明文不可找回，只回元数据）。 */
export interface ApiKeyValidateResponse {
  key_id: string;
  consumer_wallet: string;
  quota_raw: number | null;
  status: string;
}

export interface ApiKeyIssueResponse {
  key_id: string;
  api_key: string; // 明文只回一次
  consumer_wallet: string;
  quota_raw: number | null;
  created_at: string;
}

export interface ClaimState {
  agent_id: number;
  identity_found: boolean;
  agent_wallet: string | null;
  claimed_by_wallet: string | null;
  platform_custodian: string;
}

export interface ProviderRow {
  agent_id: number;
  display_name: string;
  wallet: string;
  claim_wallet: string | null; // 认领钱包（NULL=存量未认领）
  created_at: string;
}

export const coreApi = {
  catalog: () => apiFetch<Catalog>(`${CORE_BASE}/catalog`).then((r) => r.data),
  overview: () => apiFetch<StatsOverview>(`${CORE_BASE}/stats/overview`).then((r) => r.data),
  leaderboardProviders: () =>
    apiFetch<{ order: string; providers: ProviderRevenueRow[] }>(`${CORE_BASE}/leaderboard/providers`).then((r) => r.data),
  leaderboardServices: () =>
    apiFetch<{ order: string; services: ServiceStatsRow[] }>(`${CORE_BASE}/leaderboard/services`).then((r) => r.data),
  providerProof: (wallet: string) =>
    apiFetch<ProofResponse>(`${CORE_BASE}/leaderboard/providers/${wallet}/proof`).then((r) => r.data),
  providers: () => apiFetch<{ providers: ProviderRow[] }>(`${CORE_BASE}/providers`).then((r) => r.data),
  /** 认领（认证先行）：claim_wallet 必填=连接钱包地址；一身份一认领（他人已认领 409），链上 agentWallet 须等于认领钱包（否则 422 claim_requires_binding）。 */
  registerProvider: (agent_id: number, display_name: string, claim_wallet: string) =>
    apiFetch<ProviderRow>(`${CORE_BASE}/providers`, jsonInit("POST", { agent_id, display_name, claim_wallet })).then((r) => r.data),
  /** 认领三态预检（服务端对 identity 查询绕缓存）。 */
  claimState: (agent_id: number) =>
    apiFetch<ClaimState>(`${CORE_BASE}/providers/${agent_id}/claim-state`).then((r) => r.data),
  publishManifest: (manifest: ServiceManifest) =>
    apiFetch<{ service_id: string; status: string; manifest_hash: string; manifest: ServiceManifest }>(
      `${CORE_BASE}/manifests`,
      jsonInit("POST", manifest)
    ).then((r) => r.data),
  issueApiKey: (consumer_wallet: string) =>
    apiFetch<ApiKeyIssueResponse>(`${CORE_BASE}/apikeys`, jsonInit("POST", { consumer_wallet })).then((r) => r.data),
  listApiKeys: () => apiFetch<{ keys: ApiKeyRow[] }>(`${CORE_BASE}/apikeys`).then((r) => r.data),
  /** 按钱包过滤（换机辅助信息：名下已有多少 key）。 */
  listApiKeysForWallet: (wallet: string) =>
    apiFetch<{ keys: ApiKeyRow[] }>(`${CORE_BASE}/apikeys?wallet=${encodeURIComponent(wallet)}`).then((r) => r.data),
  /** 服务端实检：带明文 key 验证（只回元数据）。 */
  validateApiKey: (api_key: string) =>
    apiFetch<ApiKeyValidateResponse>(`${CORE_BASE}/internal/apikeys/validate`, jsonInit("POST", { api_key })).then((r) => r.data),
  /** 本机管理面内部通道：全量 manifest（含真实上游 url）。公开 API（/catalog、/manifests/{id}）恒脱敏 url。 */
  internalManifest: (serviceId: string) =>
    apiFetch<{ service_id: string; manifest: ServiceManifest }>(`${CORE_BASE}/internal/manifests/${serviceId}`).then((r) => r.data.manifest),
};

// ---- 上游认证头凭证（http_json 专用；Fernet 加密落盘，值永不回显） ----

/** GET/PUT 响应：只含头名，绝不包含值。 */
export interface ServiceCredentialsInfo {
  service_id: string;
  header_names: string[];
  updated?: boolean;
  deleted?: boolean;
}

/** 上游探测结果（core 平台代发，护栏：仅 http/https、私网 403、20s、不跟随重定向）。 */
export interface ProbeResult {
  status_code: number;
  content_type: string | null;
  elapsed_ms: number;
  body: unknown;
}

export interface ProbeRequest {
  url: string;
  method: "GET" | "POST";
  query?: Record<string, string>;
  body?: Record<string, unknown>;
  headers?: Record<string, string>;
}

export const probeApi = {
  probe: (req: ProbeRequest) =>
    apiFetch<ProbeResult>(`${CORE_BASE}/services/probe`, { ...jsonInit("POST", req), timeoutMs: 30_000 }).then((r) => r.data),
};

export const credentialsApi = {
  /** 全量替换语义：headers 为空对象 = 清空全部。 */
  put: (serviceId: string, headers: Record<string, string>) =>
    apiFetch<ServiceCredentialsInfo>(`${CORE_BASE}/services/${serviceId}/credentials`, {
      ...jsonInit("PUT", { headers }),
    }).then((r) => r.data),
  list: (serviceId: string) =>
    apiFetch<ServiceCredentialsInfo>(`${CORE_BASE}/services/${serviceId}/credentials`).then((r) => r.data),
  remove: (serviceId: string) =>
    apiFetch<ServiceCredentialsInfo & { deleted?: boolean }>(`${CORE_BASE}/services/${serviceId}/credentials`, {
      method: "DELETE",
    }).then((r) => r.data),
};

// ---- 决策层（G1 core）----

export interface DecisionCategories {
  categories: string[];
  counts: Record<string, number>;
  total_active: number;
}

export interface DecisionWeights {
  revenue: number;
  fulfillment: number;
  freshness: number;
  feedback?: number; // 兼容旧形状（决策层已移除反馈分量）
}

export interface DecisionRow {
  service_id: string;
  name: string;
  status: string;
  category: string;
  tags: string[];
  provider_wallet: string;
  provider_agent_id: number | null;
  price_raw: string;
  price: string;
  score: number;
  components: {
    revenue: {
      total_raw: number;
      total: string;
      charged_count: number;
      distinct_payers: number;
      score_component: number;
      formula?: string;
      proof?: string;
    };
    fulfillment: {
      available: boolean;
      calls_success?: number;
      calls_settled?: number;
      calls_aborted?: number;
      success_rate: number | null;
      p50_ms?: number;
      p95_ms?: number | null;
      window_hours?: number;
      score_component: number | null;
      formula?: string;
      proof?: { digest: string; anchor_tx: string } | string | null;
    };
    feedback?: {
      count: number;
      avg: number | null;
      bayesian_avg?: number;
      score_component: number | null;
      formula?: string;
      proof?: string;
    }; // 可选（决策层已移除）
    freshness: {
      last_activity_at: string | null;
      age_h?: number;
      half_life_h?: number;
      score_component: number | null;
      formula?: string;
    };
  };
}

export interface DecisionServicesResponse {
  category: string | null;
  window_hours: number;
  as_of: string;
  sort: string;
  weights: DecisionWeights;
  formula?: string;
  services: DecisionRow[];
  degraded: string[];
}

export interface FeedbackEntry {
  receipt_id: string;
  rating: number;
  comment: string | null;
  created_at: string;
}

export interface FeedbackSummary {
  service_id: string;
  count: number;
  avg: number | null;
  verified_paid?: boolean;
  entries: FeedbackEntry[];
}

export const decisionApi = {
  categories: () => apiFetch<DecisionCategories>(`${CORE_BASE}/decision/categories`).then((r) => r.data),
  services: (q: { category?: string; window_hours?: number; as_of?: string; sort?: "score" | "price" }) => {
    const params = new URLSearchParams();
    if (q.category) params.set("category", q.category);
    if (q.window_hours != null) params.set("window_hours", String(q.window_hours));
    if (q.as_of) params.set("as_of", q.as_of);
    if (q.sort) params.set("sort", q.sort);
    return apiFetch<DecisionServicesResponse>(`${CORE_BASE}/decision/services?${params.toString()}`).then((r) => r.data);
  },
  explain: (serviceId: string) =>
    apiFetch<DecisionServicesResponse & { feedback_entries: FeedbackEntry[]; anchor: { digest: string; anchor_tx: string; anchored_at: string } | null }>(
      `${CORE_BASE}/decision/explain/${serviceId}`
    ).then((r) => r.data),
};

/** 收据五元组（规范串 receipt_id|service_id|amount_raw|status|ts 的成分；sig 为 Ed25519 hex，来自响应头）。 */
export interface ReceiptTuple {
  receipt_id: string;
  service_id: string;
  amount_raw: string;
  status: string; // 网关侧恒 "success"（feedback 核验要求）
  ts: number;
  receipt_sig_hex: string;
}

export const feedbackApi = {
  submit: (serviceId: string, receipt: ReceiptTuple, rating: number, comment?: string) =>
    apiFetch<{ ok: true }>(`${CORE_BASE}/feedback`, {
      ...jsonInit("POST", { service_id: serviceId, receipt, rating, ...(comment ? { comment } : {}) }),
      retries: 0, // 评价有 409/429 语义，失败不该盲目重试
    }).then((r) => r.data),
  summary: (serviceId: string) => apiFetch<FeedbackSummary>(`${CORE_BASE}/feedback/services/${serviceId}`).then((r) => r.data),
};

// ---- Teams 产品形态（钱包 1 → Teams N → 服务 N） ----

export interface MyTeam {
  agent_id: number;
  display_name: string;
  claim_wallet: string | null;
  service_count: number;
  created_at: string;
}

export interface MineResponse {
  wallet: string;
  teams: MyTeam[];
}

export interface PrepareTeamResponse {
  agent_id: number;
  tx_hash: string;
  bind_required: boolean;
  next?: string;
}

export interface TeamFulfillmentService {
  service_id: string;
  calls_success: number;
  calls_aborted: number;
  p50_ms: number;
  p95_ms: number;
  distinct_payers: number;
  last_activity_at: string | null;
}

export interface TeamDetail {
  team: {
    agent_id: number;
    display_name: string;
    wallet: string;
    claim_wallet: string | null;
    created_at: string;
  };
  services: Array<CatalogService>;
  revenue: { total_raw: number; charged_count: number; wallets: string[]; proof: string };
  fulfillment: { services: TeamFulfillmentService[] };
  degraded: string[];
}

export const teamsApi = {
  /** 连接钱包 → 反查"我的团队"（大小写不敏感）。 */
  mine: (wallet: string) => apiFetch<MineResponse>(`${CORE_BASE}/providers/mine?wallet=${encodeURIComponent(wallet)}`).then((r) => r.data),
  /** 平台代发铸造新身份（≤30s 上链）→ 前端走 AgentWalletSet 签名→绑定→认领。origin 进 agentURI（内网/局域网可达）。 */
  prepare: (display_name: string, origin?: string) =>
    apiFetch<PrepareTeamResponse>(`${CORE_BASE}/teams/prepare`, {
      ...jsonInit("POST", { display_name, ...(origin ? { origin } : {}) }),
      timeoutMs: 60_000,
    }).then((r) => r.data),
  /** Team 主页聚合（收入/履约/反馈，全链上+流水口径）。 */
  detail: (agentId: number) => apiFetch<TeamDetail>(`${CORE_BASE}/teams/${agentId}`).then((r) => r.data),
};

/** 团队级默认认证头（掩码 GET 只回头名；internal 出明文——控制台只用掩码+写入）。 */
export interface TeamCredentialsInfo {
  agent_id: number;
  header_names: string[];
  updated?: boolean;
  deleted?: boolean;
}

export const teamCredentialsApi = {
  list: (agentId: number) =>
    apiFetch<TeamCredentialsInfo>(`${CORE_BASE}/teams/${agentId}/credentials`).then((r) => r.data),
  put: (agentId: number, headers: Record<string, string>) =>
    apiFetch<TeamCredentialsInfo>(`${CORE_BASE}/teams/${agentId}/credentials`, { ...jsonInit("PUT", { headers }) }).then((r) => r.data),
  remove: (agentId: number) =>
    apiFetch<TeamCredentialsInfo>(`${CORE_BASE}/teams/${agentId}/credentials`, { method: "DELETE" }).then((r) => r.data),
};
