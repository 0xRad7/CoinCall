/** 简化 JSON 编辑器：textarea + 失焦格式校验 + 一键格式化 + 「这是什么？」引导与模板。 */
import { useMemo, useState } from "react";

export interface JsonEditorProps {
  value: string;
  onChange: (v: string) => void;
  /** 外部定位的字段错误（如 pydantic loc） */
  fieldError?: string;
  rows?: number;
  label?: string;
  help?: string;
  /** 右上角附加徽标（如「自动识别，请核对」） */
  badge?: string;
}

export function parseJsonSafe(s: string): { ok: true; value: unknown } | { ok: false; error: string } {
  try {
    return { ok: true, value: JSON.parse(s) };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

const TEMPLATE_ANY = `{\n  "type": "object"\n}`;
const TEMPLATE_FIELDS = `{\n  "type": "object",\n  "properties": {\n    "text": { "type": "string", "description": "示例字段，改成你的" }\n  },\n  "required": ["text"]\n}`;

export function JsonEditor({ value, onChange, fieldError, rows = 8, label, help, badge }: JsonEditorProps) {
  const [touched, setTouched] = useState(false);
  const parsed = useMemo(() => parseJsonSafe(value), [value]);
  const showErr = touched && !parsed.ok;
  return (
    <div className="field">
      {label && (
        <label>
          {label}
          {badge && (
            <span className="badge warn" style={{ marginLeft: 8, fontWeight: 400 }} title="由探测响应自动推断，发布前请人工核对">
              {badge}
            </span>
          )}
        </label>
      )}
      <textarea
        className={`code${showErr || fieldError ? " invalid" : ""}`}
        rows={rows}
        value={value}
        spellCheck={false}
        onChange={(e) => onChange(e.target.value)}
        onBlur={() => setTouched(true)}
        aria-label={label ?? "JSON 编辑器"}
      />
      <div className="flex" style={{ justifyContent: "space-between" }}>
        <span>
          {parsed.ok ? (
            <span className="dim">✓ 合法 JSON</span>
          ) : showErr ? (
            <span className="err" style={{ color: "var(--danger)", fontSize: 12 }}>
              JSON 语法错误：{parsed.error}
            </span>
          ) : (
            <span className="dim">失焦时校验 JSON 语法</span>
          )}
          {fieldError && (
            <span className="err" style={{ color: "var(--danger)", fontSize: 12, marginLeft: 8 }}>
              服务端：{fieldError}
            </span>
          )}
        </span>
        <button
          type="button"
          className="btn small secondary"
          disabled={!parsed.ok}
          onClick={() => onChange(JSON.stringify(parsed.ok ? parsed.value : null, null, 2))}
        >
          格式化
        </button>
      </div>
      <details className="raw-detail">
        <summary>这是什么？</summary>
        <div className="dim" style={{ marginTop: 4 }}>
          JSON Schema，一句话：<b>描述这个请求/响应的 JSON 长什么样</b>。最小示例：
          <pre className="code-box" style={{ padding: 8, margin: "6px 0" }}>{`{\n  "type": "object",\n  "properties": {\n    "text": { "type": "string" }\n  },\n  "required": ["text"]\n}`}</pre>
          类型可用 string / integer / number / boolean / array / object / null。
          <div className="btn-row" style={{ marginTop: 6 }}>
            <button type="button" className="btn small secondary" onClick={() => onChange(TEMPLATE_ANY)}>
              模板：任意 JSON
            </button>
            <button type="button" className="btn small secondary" onClick={() => onChange(TEMPLATE_FIELDS)}>
              模板：对象+字段示例
            </button>
          </div>
        </div>
      </details>
      {help && <div className="help">{help}</div>}
    </div>
  );
}
