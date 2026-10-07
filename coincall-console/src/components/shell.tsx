/**
 * 三段式页面骨架与 CoinCall 差异化组件：
 * - PageHeader：H1 + 副语 + 右上主动作（工作台页可带身份眉标）
 * - StatCard / StatGrid：指标统计卡排（关键数字独立层级：大号等宽 600）
 * - EvidencePair：「数字 + 证据」成对模式——关键数字旁配哈希缩写与 scan 外链小图标
 * - ThemeToggle / ModePill：顶栏常驻的主题切换与模式身份/切换入口
 */
import { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useMode, type Mode } from "../state/ModeContext";
import { useTheme } from "../state/ThemeContext";
import { useWallet } from "../state/WalletContext";

/** 三段式骨架第一段：标题 + 副语 + 右上主动作。 */
export function PageHeader({
  title,
  sub,
  actions,
  mode,
}: {
  title: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
  /** 工作台身份眉标（Provider 深蓝 / Consumer 青绿） */
  mode?: Mode;
}) {
  return (
    <header className="page-head">
      <div className="head-text">
        {mode && (
          <span className={`mode-eyebrow ${mode}`}>
            <span className="dot" />
            {mode === "provider" ? "PROVIDER · 服务提供者" : "CONSUMER · 消费者"}
          </span>
        )}
        <h1 className="page-title">{title}</h1>
        {sub && <p className="page-sub">{sub}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

/** 指标统计卡：k 标签 / v 关键数字（大号等宽）/ s 辅助行；evidence 挂「数字+证据」成对。 */
export function StatCard({
  k,
  value,
  sub,
  evidence,
  tone,
}: {
  k: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  /** 证据锚（哈希缩写 + scan 外链）——与关键数字成对出现 */
  evidence?: ReactNode;
  tone?: "success" | "danger";
}) {
  return (
    <div className="stat-card">
      <div className="k">{k}</div>
      <div className={`v num${tone ? ` tone-${tone}` : ""}`}>{value}</div>
      {sub && <div className="s num">{sub}</div>}
      {evidence && <div className="s">{evidence}</div>}
    </div>
  );
}

/**
 * 「数字 + 证据」成对模式：哈希缩写（title 悬浮全量）+ scan 外链小图标 ↗。
 * CoinCall 差异化视觉语言——每个关键链上数字都可点去区块浏览器核对。
 */
export function EvidencePair({ hash, href, label = "在区块浏览器核对" }: { hash: string; href: string; label?: string }) {
  return (
    <span className="evidence">
      <span className="evidence-hash" title={hash}>
        {hash.length > 14 ? `${hash.slice(0, 10)}…${hash.slice(-4)}` : hash}
      </span>
      <a className="evidence-link" href={href} target="_blank" rel="noreferrer" aria-label={label} title={label}>
        ↗
      </a>
    </span>
  );
}

/** 顶栏主题切换（亮/暗双形态，localStorage 持久化）。 */
export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  return (
    <button type="button" className="theme-toggle" onClick={toggle} aria-label="切换主题" title={theme === "dark" ? "切到亮色" : "切到暗色"}>
      {theme === "dark" ? "☀ 亮色" : "☾ 暗色"}
    </button>
  );
}

/** 顶栏常驻模式身份 pill：显示当前身份（点回 /welcome 换身份）；未选过身份时引导去选。 */
export function ModePill() {
  const { mode } = useMode();
  const w = useWallet();
  const nav = useNavigate();
  if (!w.address) return null; // 未连接：登录动线在 /welcome，pill 不出现
  return (
    <button
      type="button"
      className={`mode-pill${mode ? ` ${mode}` : ""}`}
      onClick={() => nav("/welcome?switch=1")}
      title={mode ? `当前身份：${mode}——点击返回登录页换身份` : "选择你的身份（Provider / Consumer）"}
    >
      <span className="dot" />
      {mode ? (mode === "provider" ? "Provider" : "Consumer") : "选择身份"}
    </button>
  );
}
