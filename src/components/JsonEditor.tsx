/** 简化 JSON 编辑器：textarea + 失焦格式校验 + 一键格式化；pydantic 字段错误可定位。 */
import { useMemo, useState } from "react";

export interface JsonEditorProps {
  value: string;
  onChange: (v: string) => void;
  /** 外部定位的字段错误（如 pydantic loc） */
  fieldError?: string;
  rows?: number;
  label?: string;
  help?: string;
}

export function parseJsonSafe(s: string): { ok: true; value: unknown } | { ok: false; error: string } {
  try {
    return { ok: true, value: JSON.parse(s) };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export function JsonEditor({ value, onChange, fieldError, rows = 8, label, help }: JsonEditorProps) {
  const [touched, setTouched] = useState(false);
  const parsed = useMemo(() => parseJsonSafe(value), [value]);
  const showErr = touched && !parsed.ok;
  return (
    <div className="field">
      {label && <label>{label}</label>}
      <textarea
        className={`code${showErr || fieldError ? " invalid" : ""}`}
        rows={rows}
        value={value}
        spellCheck={false}
        onChange={(e) => onChange(e.target.value)}
        onBlur={() => setTouched(true)}
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
      {help && <div className="help">{help}</div>}
    </div>
  );
}
