/**
 * 统一 HTTP 封装：所有后端经 vite 同源代理（/api/core、/api/gw、/api/chain）。
 * 错误不裸抛 JSON：ApiError 携带「人话 + 下一步动作 + 可折叠原始详情」。
 */

/** 服务端三段错误模型 {error, detail, code, trace_id} + FastAPI 校验错误两种形态。 */
export interface ApiErrorShape {
  status: number;
  error: string;
  detail: string;
  code: string;
  traceId: string;
  /** pydantic 422 的逐字段定位（loc 去掉 body 前缀） */
  fieldErrors: Record<string, string>;
  raw: unknown;
}

export class ApiError extends Error implements ApiErrorShape {
  status: number;
  error: string;
  detail: string;
  code: string;
  traceId: string;
  fieldErrors: Record<string, string>;
  raw: unknown;

  constructor(shape: ApiErrorShape) {
    super(shape.detail || shape.error);
    this.name = "ApiError";
    this.status = shape.status;
    this.error = shape.error;
    this.detail = shape.detail;
    this.code = shape.code;
    this.traceId = shape.traceId;
    this.fieldErrors = shape.fieldErrors;
    this.raw = shape.raw;
  }
}

/** 网络层错误的人话（代理挂了/服务没起）。 */
function networkError(e: unknown, base: string): ApiError {
  return new ApiError({
    status: 0,
    error: "network_error",
    detail: `连不上后端（${base}）。请确认服务已启动且本控制台经 vite 代理访问（npm run dev 起在 5173）。`,
    code: "",
    traceId: "",
    fieldErrors: {},
    raw: e instanceof Error ? e.message : e,
  });
}

interface Normalized {
  error: string;
  detail: string;
  code: string;
  traceId: string;
  fieldErrors: Record<string, string>;
}

/** 把任意错误响应体归一为 {error, detail, code}。 */
export function normalizeErrorBody(body: unknown): Normalized {
  const out: Normalized = { error: "", detail: "", code: "", traceId: "", fieldErrors: {} };
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    out.error = typeof b.error === "string" ? b.error : "";
    out.detail = typeof b.detail === "string" ? b.detail : "";
    out.code = typeof b.code === "string" ? b.code : "";
    out.traceId = typeof b.trace_id === "string" ? b.trace_id : "";
    // FastAPI/pydantic 422：detail = [{loc: [body, field…], msg, type}]
    if (Array.isArray(b.detail)) {
      out.detail = "提交的内容有字段不合规";
      for (const item of b.detail as Array<Record<string, unknown>>) {
        const loc = Array.isArray(item.loc) ? (item.loc as string[]) : [];
        const field = loc.filter((x) => x !== "body").join(".");
        const msg = typeof item.msg === "string" ? item.msg : String(item.msg ?? "");
        if (field) out.fieldErrors[field] = msg;
      }
    }
  }
  return out;
}

async function fetchOnce(url: string, init?: RequestInit & { timeoutMs?: number }): Promise<Response> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), init?.timeoutMs ?? 30_000);
  try {
    return await fetch(url, { ...init, signal: ctrl.signal });
  } finally {
    clearTimeout(timer);
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function apiFetch<T>(
  url: string,
  init?: RequestInit & { timeoutMs?: number; retries?: number }
): Promise<{ data: T; headers: Headers; status: number }> {
  const retries = init?.retries ?? 2; // core 带看门狗自愈（偶发瞬断 5s 内恢复）——网络错/52x 自动重试
  let lastErr: unknown = null;
  for (let attempt = 0; attempt <= retries; attempt++) {
    let resp: Response;
    try {
      resp = await fetchOnce(url, init);
    } catch (e) {
      lastErr = networkError(e, url);
      if (attempt < retries) {
        await sleep(1200 * (attempt + 1));
        continue;
      }
      throw lastErr;
    }
    if (resp.status >= 502 && resp.status <= 599 && attempt < retries) {
      await sleep(1200 * (attempt + 1));
      continue; // 52x（含看门狗重启窗口）重试
    }
    return settle<T>(resp, url);
  }
  throw lastErr ?? networkError(new Error("unreachable"), url);
}

async function settle<T>(resp: Response, _url: string): Promise<{ data: T; headers: Headers; status: number }> {
  const text = await resp.text();
  let body: unknown = undefined;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  if (!resp.ok) {
    const n = normalizeErrorBody(body);
    throw new ApiError({
      status: resp.status,
      error: n.error || `http_${resp.status}`,
      detail: n.detail || (typeof body === "string" ? body.slice(0, 200) : `HTTP ${resp.status}`),
      code: n.code,
      traceId: n.traceId,
      fieldErrors: n.fieldErrors,
      raw: body,
    });
  }
  return { data: body as T, headers: resp.headers, status: resp.status };
}

export const jsonInit = (method: string, payload: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload),
});
