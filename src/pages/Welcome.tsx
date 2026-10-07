/**
 * 落地页 /welcome（登录 + 选身份单页流，两态）：
 * - 未连接 = 登录态（=落地页）：深色叙事面——登录入口在右上角顶栏（连接钱包按钮 + 「先逛逛目录」次级链接），
 *   页面中部纯叙事：眉题 + 一句话主张 + 三个记忆点 + 底部链上数据自证，不再放居中登录卡；
 * - 已连接 = 选身份态：工具面亮色——两张大模式卡 I'm a provider / I'm a consumer（英文主标签 +
 *   中文副语，身份色首次出现），选择后进对应工作台并 localStorage 粘滞；
 * - 已连接且已选过身份（非手动返回）→ 直接进对应工作台。
 */
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

/* ============ 登录态（深色叙事面，登录入口在右上角顶栏） ============ */
function LoginFace() {
  const overview = useAsync(() => coreApi.overview().catch(() => null), []);
  const o = overview.data;
  return (
    <div className="welcome-shell login">
      <header className="welcome-top">
        <span className="brand">
          <span className="dot" />
          琢信
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
        <div className="welcome-eyebrow">按次计费 · 链上结算 · BOT Chain 测试网</div>
        <h1>
          让 Agent 能力按次收费，
          <br />
          让 Agent 调用按次付费
        </h1>
        <p className="welcome-lede">
          钱包即账户：连接一次钱包，既能把你的 Agent 接口按次出售，也能按次调用别人的能力——定价、扣费、结算全程链上可核验。
        </p>

        <div className="welcome-points">
          <div className="welcome-point">
            <div className="pt">30 秒上架</div>
            <p className="pd">连上钱包、填一张服务表单，你的 Agent 接口就变成货架上的按次服务。</p>
          </div>
          <div className="welcome-point">
            <div className="pt">0.01 USDT / 次</div>
            <p className="pd">明码标价、按次计费；一次钱包授权即可连续调用，不必逐笔发交易。</p>
          </div>
          <div className="welcome-point">
            <div className="pt">失败不扣款</div>
            <p className="pd">Provider 未履约就不结算——链上记录是唯一计费真相，随时可核。</p>
          </div>
        </div>

        <div className="welcome-stats">
          {o ? (
            <>
              <span>
                链上 GMV <span className="ws-num">{o.gmv}</span> USDT（{o.charged_count} 笔 Charged）
              </span>
              <EvidencePair hash={`charged_count=${o.charged_count} · synced_to_block=${o.synced_to_block}`} href="https://scan.bohr.life" label="去 scan.bohr.life 核对 Charged 事件" />
              <span>
                在售服务 <span className="ws-num">{o.services_active}</span> / {o.services_total}
              </span>
            </>
          ) : (
            <span>链上数据自证（GMV / 服务数）加载中或暂不可用——可稍后在总览页核对。</span>
          )}
        </div>
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
          琢信
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
