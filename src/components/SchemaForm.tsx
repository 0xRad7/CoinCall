/**
 * 按 input_schema 动态生成参数表单：
 * - 简单 schema（object，属性均为 string/number/integer/boolean）→ 渲染成输入框；
 * - 复杂 schema（嵌套/数组/oneOf 等）→ 退化为 JSON 编辑器。
 */
import { useMemo, useState } from "react";
import { JsonEditor, parseJsonSafe } from "./JsonEditor";

type PropType = "string" | "number" | "integer" | "boolean";

interface PropDef {
  type?: string;
  title?: string;
  description?: string;
  minimum?: number;
  maximum?: number;
  default?: unknown;
  enum?: Array<string | number>;
}

function isSimpleProp(p: PropDef): p is { type: PropType } {
  return ["string", "number", "integer", "boolean"].includes(p.type ?? "");
}

export function isSimpleSchema(schema: Record<string, unknown> | null | undefined): boolean {
  if (!schema || schema["type"] !== "object") return false;
  const props = (schema["properties"] ?? {}) as Record<string, PropDef>;
  const keys = Object.keys(props);
  if (keys.length === 0) return false; // 无属性定义 → 直接 JSON 编辑器（自由传参）
  return keys.every((k) => isSimpleProp(props[k]!));
}

export interface SchemaFormProps {
  schema: Record<string, unknown>;
  value: string; // JSON 文本（复杂模式直用；简单模式由内部转换）
  onChange: (jsonText: string, parsed: Record<string, unknown> | null) => void;
}

export function SchemaForm({ schema, value, onChange }: SchemaFormProps) {
  const simple = useMemo(() => isSimpleSchema(schema), [schema]);
  const props = (schema["properties"] ?? {}) as Record<string, PropDef>;
  const required = new Set((schema["required"] ?? []) as string[]);

  const [jsonErr, setJsonErr] = useState<string | null>(null);

  if (!simple) {
    return (
      <div>
        <JsonEditor
          value={value}
          rows={10}
          label="调用参数（该服务 schema 较复杂，使用 JSON 编辑器）"
          help={`input_schema：${JSON.stringify(schema).slice(0, 160)}…`}
          onChange={(v) => {
            const p = parseJsonSafe(v);
            setJsonErr(p.ok ? null : p.error);
            onChange(v, p.ok && p.value && typeof p.value === "object" ? (p.value as Record<string, unknown>) : null);
          }}
        />
        {jsonErr && <span style={{ color: "var(--danger)", fontSize: 12 }}>JSON 语法错误：{jsonErr}（修正后才能调用）</span>}
      </div>
    );
  }

  const parsed = useMemo(() => {
    const p = parseJsonSafe(value);
    return p.ok && p.value && typeof p.value === "object" && !Array.isArray(p.value) ? (p.value as Record<string, unknown>) : {};
  }, [value]);

  const setField = (key: string, raw: string, type: PropType) => {
    const next = { ...parsed };
    if (raw === "") delete next[key];
    else if (type === "integer" || type === "number") {
      const n = Number(raw);
      if (!Number.isNaN(n)) next[key] = type === "integer" ? Math.trunc(n) : n;
    } else if (type === "boolean") next[key] = raw === "true";
    else next[key] = raw;
    onChange(JSON.stringify(next, null, 2), next);
  };

  return (
    <div>
      {Object.entries(props).map(([key, p]) => {
        const type = (p.type ?? "string") as PropType;
        const v = parsed[key];
        const vStr = v === undefined ? "" : String(v);
        const isNum = type === "integer" || type === "number";
        const invalidRange =
          isNum && vStr !== "" && ((p.minimum !== undefined && Number(vStr) < p.minimum) || (p.maximum !== undefined && Number(vStr) > p.maximum));
        return (
          <div className="field" key={key}>
            <label>
              {p.title ?? key}
              {required.has(key) ? <span style={{ color: "var(--danger)" }}> *</span> : <span className="dim">（可选）</span>}
              <span className="dim" style={{ fontWeight: 400, marginLeft: 6 }}>
                {type}
              </span>
            </label>
            {type === "boolean" ? (
              <select value={vStr === "" ? "" : String(v)} onChange={(e) => setField(key, e.target.value, "boolean")}>
                <option value="">（未设置）</option>
                <option value="true">true</option>
                <option value="false">false</option>
              </select>
            ) : (
              <input
                type={isNum ? "number" : "text"}
                className={invalidRange ? "invalid" : ""}
                value={vStr}
                placeholder={p.description ?? `输入 ${key}`}
                onChange={(e) => setField(key, e.target.value, type)}
              />
            )}
            {invalidRange && (
              <div className="err" style={{ color: "var(--danger)", fontSize: 12 }}>
                取值范围：{p.minimum ?? "-∞"} ~ {p.maximum ?? "∞"}
              </div>
            )}
            {p.description && <div className="help">{p.description}</div>}
          </div>
        );
      })}
    </div>
  );
}
