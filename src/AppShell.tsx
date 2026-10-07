import { NavLink, Outlet, Navigate, useLocation } from "react-router-dom";
import { useWallet } from "./state/WalletContext";
import { useMode } from "./state/ModeContext";
import { ADMIN_ADDRESS, CHAIN_ID } from "./chain/constants";
import { ConnectWalletButton } from "./components/ConnectWalletButton";
import { ModePill, ThemeToggle } from "./components/shell";

/** 顶栏（右上角）：钱包簇整组右对齐——连接后常驻地址 chip（含实际钱包名）+ 链徽标（不对时可点切链）+ 断开按钮；簇后是模式 pill 与主题切换。 */
function WalletChipBar() {
  const w = useWallet();
  if (!w.address || !w.mode) {
    // 未连接：顶栏常驻全局入口（与消费端工作台/发布表单同一 connect 代码路径）
    return (
      <div className="wallet-bar">
        <div className="wallet-cluster">
          <span className="dim">未连接钱包——签名与交易需要浏览器钱包（仅读取地址）</span>
          <ConnectWalletButton size="normal" />
        </div>
        <div className="topbar-tools">
          <ThemeToggle />
        </div>
      </div>
    );
  }
  const chainBadge =
    w.mode === "demo" ? (
      <span className="badge warn" title="一次性演示钱包，关页即焚">
        demo 钱包
      </span>
    ) : w.chainOk ? (
      <span className="badge ok" title={`chainId ${CHAIN_ID} · BOT Chain Testnet`}>
        链 {CHAIN_ID} ✓
      </span>
    ) : (
      <button className="badge err" style={{ border: "none", cursor: "pointer" }} onClick={() => void w.ensureChain()} title="当前不是 BOT Chain 测试网，点击引导切换/添加">
        链 {w.chainId ?? "?"} ✗ 点我切链
      </button>
    );
  return (
    <div className="wallet-bar">
      <div className="wallet-cluster">
        <span className="mono wallet-addr" title={w.address}>
          ● {w.walletName ? `${w.walletName} · ` : ""}
          {w.address.slice(0, 8)}…{w.address.slice(-6)}
        </span>
        {chainBadge}
        {w.switching && (
          <span className="dim">
            <span className="spin" style={{ width: 12, height: 12, borderWidth: 2 }} /> 切链中…
          </span>
        )}
        {w.mode === "injected" && w.candidates.length >= 2 && (
          <button className="btn small secondary" onClick={w.rechoose} title="清除记忆并重新选择浏览器钱包">
            换钱包
          </button>
        )}
        <button className="btn small secondary" onClick={w.disconnect}>
          断开
        </button>
      </div>
      <div className="topbar-tools">
        <ModePill />
        <ThemeToggle />
      </div>
    </div>
  );
}

/** 侧栏导航工厂：adminEnabled 决定总览副标注（管理员视图/目录与比价）。 */
function navGroups(adminEnabled: boolean, isAdmin: boolean): Array<{
  label: string;
  items: Array<{ to: string; end?: boolean; name: string; sub: string; identity?: "provider" | "consumer" }>;
}> {
  return [
  {
    label: "工作台",
    items: [
      { to: "/provider", name: "Provider", sub: "团队与服务", identity: "provider" },
      { to: "/consumer", name: "Consumer", sub: "钱包与调用", identity: "consumer" },
      ...(adminEnabled && !isAdmin ? [] : [{ to: "/" as const, end: true as const, name: "总览", sub: adminEnabled ? "管理员视图" : "目录与比价" }]),
    ],
  },
  {
    label: "支持",
    items: [
      { to: "/help", name: "帮助", sub: "流程与错误码" },
    ],
  },
  ];
}

export function AppShell() {
  const { mode } = useMode();
  const w = useWallet();
  // 管理员门禁：配置 VITE_ADMIN_ADDRESS 后，总览=管理员专属（连接地址精确匹配）；
  // 未配置（本地开发）不启用门禁——总览对所有人可见，便于调试。
  const adminEnabled = ADMIN_ADDRESS !== "";
  const isAdmin = !adminEnabled || (w.address ?? "").toLowerCase() === ADMIN_ADDRESS;

  // 响应式路由守卫：useLocation 使 SPA 内部导航也触发判定（原 window.location.hash 只在首次渲染读一次）
  const loc = useLocation();
  if (adminEnabled && !isAdmin && (loc.pathname === "/" || loc.pathname === "")) {
    return <Navigate to="/welcome" replace />;
  }

  return (
    <div className="app-shell">
      <nav className="side-nav">
        <div className="brand">
          <span className="dot" />
          CoinCall 币应
        </div>
        {navGroups(adminEnabled, isAdmin).map((g) => (
          <div key={g.label} className="nav-group">
            <div className="nav-group-label">{g.label}</div>
            {g.items
              .filter((it) => !it.identity || !mode || it.identity === mode)
              .map((it) => (
              <NavLink
                key={it.to}
                to={it.to}
                end={it.end}
                data-identity={it.identity}
                className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
              >
                <span className="nav-name">{it.name}</span>
                <span className="nav-sub">{it.sub}</span>
              </NavLink>
            ))}
          </div>
        ))}
        <div className="nav-foot">
          chainId {CHAIN_ID} · BOT Chain
          <br />
          不接触任何私钥
        </div>
      </nav>
      <div className="main-col">
        <WalletChipBar />
        <main className="main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
