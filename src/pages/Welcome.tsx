/**
 * 落地页 /welcome（登录 + 选身份单页流，两态）：
 * - 未连接 = 登录态（=落地页）：暗色情报站风格叙事面——登录入口在右上角顶栏（连接钱包按钮 + 「先逛逛目录」次级链接），
 *   页面自上而下：Hero（「双向流光」渐变大标题 + 正下方 mono 打字机副标题▌ + 次级锚链接平滑滚动）→
 *   Core Capabilities Bento 网格（2×2 主推大卡 + 1×1×2 普通卡 + 通栏宽卡；旋转描边 + 聚光/微倾双动效）→
 *   平台指标三大数字（EvidencePair 链上自证外链）→ 护栏三行小字；入场 fade-in-up 依次延迟、hero 底部径向光晕（纯 CSS）；
 * - 已连接 = 选身份态：工具面亮色——两张大模式卡 I'm a provider / I'm a consumer（英文主标签 +
 *   中文副语，身份色首次出现），选择后进对应工作台并 localStorage 粘滞；
 * - 已连接且已选过身份（非手动返回）→ 直接进对应工作台。
 */
import { useEffect, useRef, type MouseEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { coreApi } from "../api/core";
import { ConnectWalletButton } from "../components/ConnectWalletButton";
import { EvidencePair, ThemeToggle } from "../components/shell";
import { useMode, type Mode } from "../state/ModeContext";
import { useAsync } from "../lib/useAsync";
import { useWallet } from "../state/WalletContext";

export default function Welcome() {
  const w = useWallet();
  const { mode } = useMode();
  const [params] = useSearchParams();
  const switching = params.get("switch") === "1"; // 顶栏 pill / 手动返回：不自动跳工作台

  if (!w.address) return <LoginFace />;
  // 已连接且已选过身份（非换身份入口）→ 直接进工作台
  if (mode && !switching) return <Navigate to={mode === "provider" ? "/provider" : "/consumer"} replace />;
  return <PickFace />;
}

/* ============ 登录态（暗色情报站落地页，登录入口在右上角顶栏） ============ */
const CAPABILITIES: Array<{ no: string; title: string; tag: string; desc: string; size?: "lg" | "wide" }> = [
  { no: "01", title: "AI Agents 一把钥匙", tag: "Single API Key, Pay-per-Request", desc: "一个 API Key 接入平台全部服务，按次付费，无需为每个服务单独注册与议价。", size: "lg" },
  { no: "02", title: "综合决策建议", tag: "平台公共服务", desc: "为 Agent 提供服务方的履约历史、安全评估、投毒检测等综合决策建议，报价内嵌 advice，选型有据可依。" },
  { no: "03", title: "链上信任底座", tag: "BOT Chain 生态", desc: "服务方身份注册（ERC-8004）、PayVault 支付托管合约交互，身份与资金流全程链上可核验。" },
  { no: "04", title: "失败不扣款", tag: "后付费结算", desc: "Provider 未履约不结算，链上 Charged 事件是唯一计费真相，收据 Ed25519 签名可离线验证。", size: "wide" },
];

/** 副标题打字机文案（注入 data-text，便于以后配置化） */
const SUB_TITLE = "AI Agent Integration Platform on BOT Chain";

function LoginFace() {
  const overview = useAsync(() => coreApi.overview().catch(() => null), []);
  const o = overview.data;
  const subRef = useRef<HTMLSpanElement>(null);

  // 副标题打字机（JS 定时器链，文案读自身 data-text）：打出 70ms/字 → 停 2.4s → 倒删 35ms/字 → 歇 700ms → 重打，无限循环；
  // prefers-reduced-motion 时不打字、静态全显（▌光标常亮由 CSS 关动画实现）。effect 清理取消定时器，StrictMode 双挂载不叠加。
  useEffect(() => {
    const el = subRef.current;
    if (!el) return;
    const full = el.dataset.text ?? "";
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      el.textContent = full;
      return;
    }
    el.textContent = "";
    let pos = 0;
    let dir: 1 | -1 = 1; // 1=打出 -1=倒删
    let timer = 0;
    const step = () => {
      pos += dir;
      el.textContent = full.slice(0, Math.max(pos, 0));
      let delay: number;
      if (dir === 1 && pos >= full.length) {
        dir = -1;
        delay = 2400; // 打满停顿
      } else if (dir === -1 && pos <= 0) {
        dir = 1;
        delay = 700; // 删空歇
      } else {
        delay = dir === 1 ? 70 : 35;
      }
      timer = window.setTimeout(step, delay);
    };
    timer = window.setTimeout(step, 700); // 等标题入场落定后再起打
    return () => window.clearTimeout(timer);
  }, []);

  // Bento 动效 B：鼠标跟随聚光灯 + 3D 微倾——JS 只写 CSS 变量（聚光坐标 --mx/--my、倾角 --rx/--ry），样式全在 CSS；
  // prefers-reduced-motion 的微倾禁用同样由 CSS 层兜底（transform: none），聚光灯保留。
  const onCardMove = (e: MouseEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const r = el.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    el.style.setProperty("--mx", `${x.toFixed(1)}px`);
    el.style.setProperty("--my", `${y.toFixed(1)}px`);
    el.style.setProperty("--ry", `${((x / r.width - 0.5) * 10).toFixed(2)}deg`);
    el.style.setProperty("--rx", `${((0.5 - y / r.height) * 8).toFixed(2)}deg`);
  };
  const onCardLeave = (e: MouseEvent<HTMLDivElement>) => {
    e.currentTarget.style.setProperty("--rx", "0deg");
    e.currentTarget.style.setProperty("--ry", "0deg");
  };

  // 次级锚链接：平滑滚动到能力区（href 兜底可跳）
  const jumpToCaps = (e: MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    document.getElementById("welcome-caps")?.scrollIntoView({ behavior: "smooth" });
  };
  return (
    <div className="welcome-shell login">
      <header className="welcome-top">
        <span className="brand">
          <span className="dot" />
          CoinCall
        </span>
        <div className="welcome-top-actions">
          <a className="welcome-browse-link" href="#/?browse=1">
            先逛逛目录
          </a>
          <ConnectWalletButton
            size="normal"
            label="连接钱包登录"
            title="只读取钱包地址（eth_requestAccounts）；签名与交易都在钱包扩展弹窗里确认"
          />
          <ThemeToggle />
        </div>
      </header>
      <main className="welcome-login">
        {/* Hero：双向流光渐变大标题 + 正下方 mono 打字机副标题（▌常驻）+ 次级锚链接；底部微弱径向光晕 */}
        <section className="wl-hero">
          <h1 className="wl-fade d2">
            <span className="wl-title">CoinCall</span>
          </h1>
          <div className="wl-sub-type wl-fade d3" aria-label={SUB_TITLE}>
            <span className="wl-sub-text" ref={subRef} data-text={SUB_TITLE} aria-hidden="true" />
            <span className="wl-sub-caret" aria-hidden="true">▌</span>
          </div>
          <div className="wl-cta-row wl-fade d4">
            <a className="wl-cta" href="#welcome-caps" onClick={jumpToCaps}>
              查看核心能力 <span aria-hidden="true">↓</span>
            </a>
          </div>
        </section>

        {/* Core Capabilities：Bento 网格（01 主推 2×2 大卡 / 02 03 普通 1×1 / 04 通栏宽卡）；
            动效 A 旋转描边（conic + @property --a）+ 动效 B 聚光灯/3D 微倾（mousemove 只写 CSS 变量） */}
        <section className="wl-caps wl-fade d5" id="welcome-caps">
          <h2 className="wl-sec-title">Core Capabilities</h2>
          <div className="bento-grid">
            {CAPABILITIES.map((c) => (
              <div
                key={c.no}
                className={`bento-card${c.size === "lg" ? " bento-lg" : c.size === "wide" ? " bento-wide" : ""}`}
                onMouseMove={onCardMove}
                onMouseLeave={onCardLeave}
              >
                <div className="wc-no">{c.no}</div>
                <h3>{c.title}</h3>
                <div className="wc-tag">{c.tag}</div>
                <p>{c.desc}</p>
              </div>
            ))}
          </div>
        </section>

        {/* 平台指标：3 列大数字（等宽 tabular-nums + 高亮色）+ EvidencePair 链上自证外链；不可用优雅降级 */}
        <section className="wl-metrics welcome-stats wl-fade d6">
          {o ? (
            <>
              <div className="wl-metric">
                <div className="wm-num">
                  {o.gmv}
                  <span className="wm-unit">USDT</span>
                </div>
                <div className="wm-label">链上 GMV</div>
                <div className="wm-sub">
                  {o.charged_count} 笔 Charged ·{" "}
                  <EvidencePair
                    hash={`charged_count=${o.charged_count} · synced_to_block=${o.synced_to_block}`}
                    href="https://scan.bohr.life"
                    label="去 scan.bohr.life 核对 Charged 事件"
                  />
                </div>
              </div>
              <div className="wl-metric">
                <div className="wm-num">{o.services_active}</div>
                <div className="wm-label">在售服务</div>
                <div className="wm-sub">
                  active / total：{o.services_active} / {o.services_total}
                </div>
              </div>
              <div className="wl-metric">
                <div className="wm-num">5s</div>
                <div className="wm-label">keeper 批结算上链</div>
              </div>
            </>
          ) : (
            <div className="wl-metric">
              <div className="wm-num">—</div>
              <div className="wm-label">链上数据自证（GMV / 服务数）</div>
              <div className="wm-sub">加载中或暂不可用——可稍后在总览页核对。</div>
            </div>
          )}
        </section>

        {/* 护栏：低调三行小字 */}
        <section className="wl-guard wl-fade d6">
          <div className="wl-guard-title">为 Agent 设计的护栏</div>
          <ul>
            <li>
              <b>三重预算护栏</b>：本地 L0 预算（总额/日额/单笔）+ 平台钱包日限 + 服务白名单
            </li>
            <li>
              <b>MCP 一条命令接入</b>：<span className="mono">uvx coincall-mcp</span> 五工具（目录/报价/付费/账单/自查）
            </li>
            <li>
              <b>目录脱敏中转</b>：真实上游地址永不公开，消费者只经平台端点调用
            </li>
          </ul>
        </section>
      </main>
    </div>
  );
}

/* ============ 选身份态（工具面 + 两张大模式卡） ============ */
const MODE_CARDS: Array<{
  id: Mode;
  role: string;
  title: string;
  desc: string;
  points: string[];
  go: string;
}> = [
  {
    id: "provider",
    role: "PROVIDER",
    title: "I'm a provider",
    desc: "创建团队、上架服务、收入直进你的钱包。",
    points: ["一个钱包可建多个团队，链上身份由平台自动管理", "一份 manifest 表单完成发布、改价与暂停", "收入按链上 Charged 记账，随时 providerWithdraw 提现"],
    go: "进入 Provider 工作台 →",
  },
  {
    id: "consumer",
    role: "CONSUMER",
    title: "I'm a consumer",
    desc: "浏览服务、授权一次、按次付费调用。",
    points: ["目录 + 四信号比价，价格与履约表现全公开", "API key 与钱包绑定，付费签名走 EIP-712", "Provider 失败不扣款，本机还能设预算护栏"],
    go: "进入 Consumer 工作台 →",
  },
];

function PickFace() {
  const w = useWallet();
  const { setMode } = useMode();
  const nav = useNavigate();
  const choose = (m: Mode) => {
    setMode(m);
    nav(`/${m}`);
  };
  return (
    <div className="welcome-shell pick">
      <header className="welcome-top">
        <span className="brand">
          <span className="dot" />
          CoinCall
        </span>
        <span className="dim mono" title={w.address ?? undefined}>
          {w.address ? `${w.address.slice(0, 10)}…${w.address.slice(-6)}` : ""}
        </span>
        <ThemeToggle />
      </header>
      <main className="welcome-pick">
        <h1>选择你的身份</h1>
        <p className="pick-sub">
          连接完成——两种身份共用同一个钱包，选择后进入对应工作台（之后可从顶栏 pill 随时回来换）。
        </p>
        <div className="choose-grid" role="group" aria-label="身份选择">
          {MODE_CARDS.map((c) => (
            <button key={c.id} type="button" className={`mode-card ${c.id}`} onClick={() => choose(c.id)} aria-label={`${c.title}——${c.desc}`}>
              <span className="mc-role">
                <span className="dot" />
                {c.role}
              </span>
              <h2>{c.title}</h2>
              <p className="mc-desc">{c.desc}</p>
              <ul>
                {c.points.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
              <span className="mc-go">{c.go}</span>
            </button>
          ))}
        </div>
        <div className="btn-row" style={{ marginTop: 24 }}>
          <button type="button" className="btn small secondary" onClick={w.disconnect}>
            断开钱包，换一个
          </button>
        </div>
      </main>
    </div>
  );
}
