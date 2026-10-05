/** 通用 UI 原子组件：四态展示、错误框（人话+折叠详情）、二次确认。 */
import { ReactNode, useEffect, useState } from "react";
import { humanizeError } from "../lib/errors";

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="skeleton flex">
      <span className="spin" />
      <span>{label ?? "加载中…"}</span>
    </div>
  );
}

export function Empty({ text }: { text: string }) {
  return <div className="empty">{text}</div>;
}

export function Badge({ kind, children }: { kind: "ok" | "warn" | "err" | "muted"; children: ReactNode }) {
  return <span className={`badge ${kind}`}>{children}</span>;
}

/** 错误框：人话 title + 下一步 hint + 可折叠原始详情。 */
export function ErrorBox({ error, title }: { error: unknown; title?: string }) {
  if (error == null) return null;
  const h = humanizeError(error);
  return (
    <div className="alert err" role="alert">
      <div style={{ fontWeight: 700 }}>{title ?? h.title}</div>
      <div>{h.hint}</div>
      {h.fieldErrors && Object.keys(h.fieldErrors).length > 0 && (
        <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
          {Object.entries(h.fieldErrors).map(([f, m]) => (
            <li key={f}>
              <b>{f}</b>：{m}
            </li>
          ))}
        </ul>
      )}
      {h.traceId && <div className="dim">trace_id: {h.traceId}</div>}
      <details className="raw-detail">
        <summary>详细信息（原始响应）</summary>
        <pre>{typeof h.raw === "string" ? h.raw : JSON.stringify(h.raw, null, 2)}</pre>
      </details>
    </div>
  );
}

export function SuccessBox({ children }: { children: ReactNode }) {
  return <div className="alert ok">{children}</div>;
}

export function InfoBox({ children }: { children: ReactNode }) {
  return <div className="alert info">{children}</div>;
}

export function WarnBox({ children }: { children: ReactNode }) {
  return <div className="alert warn">{children}</div>;
}

/** 金额展示：人类可读 + raw。 */
export function Amount({ human, raw, token = "USDT" }: { human: string; raw: string | bigint; token?: string }) {
  return (
    <span className="num" title={`最小单位(raw): ${raw.toString()}`}>
      {human} {token}
      <span className="dim"> · raw={raw.toString()}</span>
    </span>
  );
}

/** 交易哈希外链（scan.bohr.life）。 */
export function TxLink({ hash }: { hash: string }) {
  return (
    <a href={`https://scan.bohr.life/tx/${hash}`} target="_blank" rel="noreferrer" className="mono">
      {hash.slice(0, 10)}…{hash.slice(-8)} ↗
    </a>
  );
}

/** 上链/破坏性动作二次确认。 */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmText = "确认执行",
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmText?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      style={{ position: "fixed", inset: 0, background: "rgba(10,16,28,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50 }}
      onClick={(e) => e.target === e.currentTarget && onCancel()}
    >
      <div className="card" style={{ maxWidth: 520, margin: 0 }}>
        <h3>{title}</h3>
        <div style={{ fontSize: 13, color: "var(--text-2)" }}>{body}</div>
        <div className="btn-row" style={{ marginTop: 16, justifyContent: "flex-end" }}>
          <button className="btn secondary" onClick={onCancel}>
            取消
          </button>
          <button className="btn danger" onClick={onConfirm}>
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}

/** 复制按钮（成功后短暂变 ✓）。 */
export function CopyButton({ text, label = "复制" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => setDone(false), 1600);
    return () => clearTimeout(t);
  }, [done]);
  return (
    <button
      className="btn small secondary"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
        } catch {
          const ta = document.createElement("textarea");
          ta.value = text;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          ta.remove();
        }
        setDone(true);
      }}
    >
      {done ? "已复制 ✓" : label}
    </button>
  );
}

/** 四态包装：loading / error / empty / 内容。 */
export function AsyncSection<T>({
  state,
  empty,
  children,
}: {
  state: { data: T | null; loading: boolean; error: unknown };
  empty?: string;
  children: (data: T) => ReactNode;
}) {
  if (state.loading && state.data == null) return <Spinner />;
  if (state.error != null) return <ErrorBox error={state.error} />;
  if (state.data == null) return <Empty text={empty ?? "暂无数据"} />;
  return <>{children(state.data)}</>;
}
