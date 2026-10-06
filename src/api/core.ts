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

export interface ApiKeyIssueResponse {
  key_id: string;
  api_key: string; // 明文只回一次
  consumer_wallet: string;
  quota_raw: number | null;
  created_at: string;
}

export interface ProviderRow {
  agent_id: number;
  display_name: string;
  wallet: string;
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
  registerProvider: (agent_id: number, display_name: string) =>
    apiFetch<ProviderRow>(`${CORE_BASE}/providers`, jsonInit("POST", { agent_id, display_name })).then((r) => r.data),
  publishManifest: (manifest: ServiceManifest) =>
    apiFetch<{ service_id: string; status: string; manifest_hash: string; manifest: ServiceManifest }>(
      `${CORE_BASE}/manifests`,
      jsonInit("POST", manifest)
    ).then((r) => r.data),
  issueApiKey: (consumer_wallet: string) =>
    apiFetch<ApiKeyIssueResponse>(`${CORE_BASE}/apikeys`, jsonInit("POST", { consumer_wallet })).then((r) => r.data),
  listApiKeys: () => apiFetch<{ keys: ApiKeyRow[] }>(`${CORE_BASE}/apikeys`).then((r) => r.data),
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
