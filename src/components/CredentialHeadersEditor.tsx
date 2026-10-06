/**
 * 上游认证头键值编辑器（http_json 服务专用）：
 * 头名下拉常用项（X-API-KEY / Authorization / X-Auth-Token / 自定义）+ 值 type=password（可显隐）。
 * 值仅在本组件内存中，提交后由 core 加密落盘；公开 manifest/目录零泄露。
 */
import { useState } from "react";

export interface CredentialRow {
  name: string;
  value: string;
  custom?: boolean;
}

const COMMON_HEADERS = ["X-API-KEY", "Authorization", "X-Auth-Token"] as const;

export function CredentialHeadersEditor({
  rows,
  onChange,
}: {
  rows: CredentialRow[];
  onChange: (rows: CredentialRow[]) => void;
}) {
  const [revealed, setRevealed] = useState<Record<number, boolean>>({});

  const setRow = (i: number, patch: Partial<CredentialRow>) => {
    onChange(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  };

  return (
    <div>
      {rows.map((row, i) => (
        <div key={i} className="flex" style={{ marginBottom: 8, alignItems: "center" }}>
          <select
            style={{ width: 150 }}
            value={row.custom ? "__custom__" : COMMON_HEADERS.includes(row.name as (typeof COMMON_HEADERS)[number]) ? row.name : row.name || ""}
            onChange={(e) => {
              const v = e.target.value;
              if (v === "__custom__") setRow(i, { name: "", custom: true });
              else setRow(i, { name: v, custom: false });
            }}
            aria-label={`头名 ${i + 1}`}
          >
            {!row.custom && !COMMON_HEADERS.includes(row.name as (typeof COMMON_HEADERS)[number]) && row.name !== "" && (
              <option value={row.name}>{row.name}（当前）</option>
            )}
            <option value="">— 选择头名 —</option>
            {COMMON_HEADERS.map((h) => (
              <option key={h} value={h}>
                {h}
              </option>
            ))}
            <option value="__custom__">自定义…</option>
          </select>
          {row.custom && (
            <input
              type="text"
              style={{ width: 150 }}
              placeholder="自定义头名，如 X-My-Token"
              value={row.name}
              onChange={(e) => setRow(i, { name: e.target.value.trim() })}
              aria-label={`自定义头名 ${i + 1}`}
            />
          )}
          <input
            type={revealed[i] ? "text" : "password"}
            style={{ flex: 1, minWidth: 160 }}
            placeholder="凭证值（sk-… / Bearer …）"
            value={row.value}
            onChange={(e) => setRow(i, { value: e.target.value })}
            autoComplete="off"
            aria-label={`凭证值 ${i + 1}`}
          />
          <button
            type="button"
            className="btn small secondary"
            onClick={() => setRevealed((rv) => ({ ...rv, [i]: !rv[i] }))}
            title={revealed[i] ? "隐藏" : "显示"}
          >
            {revealed[i] ? "隐藏" : "显示"}
          </button>
          <button type="button" className="btn small danger" onClick={() => onChange(rows.filter((_, idx) => idx !== i))}>
            删除
          </button>
        </div>
      ))}
      <button type="button" className="btn small secondary" onClick={() => onChange([...rows, { name: "", value: "", custom: false }])}>
        + 添加认证头
      </button>
    </div>
  );
}

/** 过滤出可直接提交的键值（名字与值都非空）。 */
export function rowsToHeaders(rows: CredentialRow[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const r of rows) {
    if (r.name.trim() && r.value.trim()) out[r.name.trim()] = r.value.trim();
  }
  return out;
}
