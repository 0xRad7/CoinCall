/**
 * 探测接口弹层：组装 probe 请求（url/method/query|body/headers）→ POST /api/core/services/probe
 * → 结果卡（状态码/耗时/响应体可折叠）→「用结果生成 Schema」。
 * headers 优先用发布表单里已填的上游认证头，也可在弹层临时填（明示「仅探测用，不保存/不落盘」）。
 * 浏览器直连上游会被 CORS 拦，所以由平台代发（护栏：仅 http/https、私网 403、20s 硬顶、不跟随重定向）。
 */
import { useEffect, useState } from "react";
import { probeApi, type ProbeResult } from "../api/core";
import { CredentialHeadersEditor, rowsToHeaders, type CredentialRow } from "./CredentialHeadersEditor";
import { ErrorBox, Spinner } from "./ui";
import { inputSchemaFromExampleJson, inputSchemaFromParams, outputSchemaFromProbeBody, type ExampleParam } from "../lib/schema-infer";

export interface ProbeApplyPayload {
  outputSchemaText: string;
  inputSchemaText: string;
}

export function ProbeDialog({
  open,
  onClose,
  initialUrl,
  method,
  params,
  exampleJson,
  formCredHeaders,
  onApplySchema,
}: {
  open: boolean;
  onClose: () => void;
  initialUrl: string;
  method: "GET" | "POST";
  params: ExampleParam[];
  exampleJson: string;
  /** 发布表单「上游认证头」区块里已填的键值（优先使用） */
  formCredHeaders: Record<string, string>;
  onApplySchema: (p: ProbeApplyPayload) => void;
}) {
  const [url, setUrl] = useState(initialUrl);
  const [editParams, setEditParams] = useState<ExampleParam[]>(params);
  const [editJson, setEditJson] = useState(exampleJson);
  const [useFormCreds, setUseFormCreds] = useState(Object.keys(formCredHeaders).length > 0);
  const [tempRows, setTempRows] = useState<CredentialRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ProbeResult | null>(null);
  const [error, setError] = useState<unknown>(null);

  // 每次打开时同步表单当前值（组件可能早在 URL 未填时就已挂载）
  useEffect(() => {
    if (open) {
      setUrl(initialUrl);
      setEditParams(params);
      setEditJson(exampleJson);
      setResult(null);
      setError(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialUrl]);

  if (!open) return null;

  const run = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const headers = useFormCreds ? formCredHeaders : rowsToHeaders(tempRows);
      const req: Parameters<typeof probeApi.probe>[0] = { url: url.trim(), method };
      if (Object.keys(headers).length > 0) req.headers = headers;
      if (method === "GET") {
        const q: Record<string, string> = {};
        for (const p of editParams) if (p.name.trim()) q[p.name.trim()] = p.value;
        if (Object.keys(q).length > 0) req.query = q;
      } else {
        try {
          req.body = JSON.parse(editJson || "{}") as Record<string, unknown>;
        } catch {
          throw new Error("示例请求体不是合法 JSON——请修正后再探测。");
        }
      }
      setResult(await probeApi.probe(req));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="探测接口"
      style={{ position: "fixed", inset: 0, background: "rgba(10,16,28,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 60, overflow: "auto" }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="card" style={{ maxWidth: 640, margin: 20, width: "94vw", maxHeight: "88vh", overflow: "auto" }}>
        <h3 className="mt-0">探测接口（平台代发）</h3>
        <p className="card-desc">
          浏览器直连你的上游会被 CORS 拦，所以由平台服务代发（护栏：仅 http/https、私网/回环拒绝、20 秒硬顶、不跟随重定向）。
        </p>

        <div className="field">
          <label>上游 URL</label>
          <input type="text" value={url} placeholder="https://your-host/endpoint" onChange={(e) => setUrl(e.target.value)} aria-label="上游 URL" />
        </div>

        <div className="field">
          <label>认证头（仅探测用，不保存不落盘）</label>
          <div className="flex" style={{ marginBottom: 8 }}>
            <label className="dim" style={{ cursor: "pointer" }}>
              <input type="radio" checked={useFormCreds} disabled={Object.keys(formCredHeaders).length === 0} onChange={() => setUseFormCreds(true)} />{" "}
              用表单里已填的上游认证头（{Object.keys(formCredHeaders).length} 个）
            </label>
            <label className="dim" style={{ cursor: "pointer" }}>
              <input type="radio" checked={!useFormCreds} onChange={() => setUseFormCreds(false)} /> 本次临时填写
            </label>
          </div>
          {!useFormCreds && <CredentialHeadersEditor rows={tempRows} onChange={setTempRows} />}
          <div className="help">headers 只随本次探测请求发出，不会保存到平台；表单里的认证头要保存仍走「发布」。</div>
        </div>

        <div className="field">
          <label>{method === "GET" ? "query 参数（可改）" : "请求体（可改）"}</label>
          {method === "GET" ? (
            <div>
              {editParams.map((p, i) => (
                <div key={i} className="flex" style={{ marginBottom: 6 }}>
                  <input
                    type="text"
                    style={{ width: 180 }}
                    placeholder="参数名"
                    value={p.name}
                    onChange={(e) => setEditParams(editParams.map((x, idx) => (idx === i ? { ...x, name: e.target.value } : x)))}
                    aria-label={`探测参数名 ${i + 1}`}
                  />
                  <input
                    type="text"
                    style={{ flex: 1 }}
                    placeholder="值"
                    value={p.value}
                    onChange={(e) => setEditParams(editParams.map((x, idx) => (idx === i ? { ...x, value: e.target.value } : x)))}
                    aria-label={`探测参数值 ${i + 1}`}
                  />
                </div>
              ))}
              <button type="button" className="btn small secondary" onClick={() => setEditParams([...editParams, { name: "", value: "" }])}>
                + 添加参数
              </button>
            </div>
          ) : (
            <textarea className="code" rows={4} value={editJson} onChange={(e) => setEditJson(e.target.value)} aria-label="探测请求体 JSON" />
          )}
        </div>

        <div className="btn-row">
          <button className="btn" disabled={busy || !/^https?:\/\//.test(url.trim())} onClick={run}>
            {busy ? <Spinner label="探测中（最长 20s）…" /> : `发送探测（${method}）`}
          </button>
          <button className="btn secondary" onClick={onClose}>
            关闭
          </button>
        </div>

        {error != null && <ErrorBox error={error} />}

        {result && (
          <div style={{ marginTop: 12 }}>
            <div className="section-title" style={{ margin: "8px 0" }}>
              探测结果{" "}
              <span className={`badge ${result.status_code >= 200 && result.status_code < 300 ? "ok" : "err"}`}>{result.status_code}</span>{" "}
              <span className="dim num">{result.elapsed_ms} ms · {result.content_type ?? "未知类型"}</span>
              {!(result.status_code >= 200 && result.status_code < 300) && (
                <span className="dim">（非 2xx 也可从错误结构生成 schema）</span>
              )}
            </div>
            <details className="raw-detail" open>
              <summary>响应体预览</summary>
              <pre>{typeof result.body === "string" ? result.body : JSON.stringify(result.body, null, 2)}</pre>
            </details>
            <GenerateButton result={result} method={method} params={editParams} exampleJson={editJson} onApplySchema={onApplySchema} />
          </div>
        )}
      </div>
    </div>
  );
}

/** 「用结果生成 Schema」：output 从响应体递归推断；input 从示例区推断。 */
function GenerateButton({
  result,
  method,
  params,
  exampleJson,
  onApplySchema,
}: {
  result: ProbeResult;
  method: "GET" | "POST";
  params: ExampleParam[];
  exampleJson: string;
  onApplySchema: (p: ProbeApplyPayload) => void;
}) {
  return (
    <button
      className="btn"
      onClick={() => {
        const output = outputSchemaFromProbeBody(result.body);
        const input = method === "GET" ? inputSchemaFromParams(params) : inputSchemaFromExampleJson(exampleJson);
        onApplySchema({
          outputSchemaText: JSON.stringify(output, null, 2),
          inputSchemaText: input ? JSON.stringify(input, null, 2) : "",
        });
      }}
    >
      用结果生成 Schema（input + output）
    </button>
  );
}
