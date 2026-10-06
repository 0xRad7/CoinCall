import { NavLink, Outlet } from "react-router-dom";
import { useWallet } from "./state/WalletContext";
import { CHAIN_ID } from "./chain/constants";
import { ConnectWalletButton } from "./components/ConnectWalletButton";

/** 顶栏：连接后常驻地址 chip（含实际钱包名）+ 链徽标（不对时可点切链）+ 断开按钮。 */
function WalletChipBar() {
  const w = useWallet();
  if (!w.address || !w.mode) {
    // 未连接：顶栏常驻全局入口（与消费端工作台/发布表单同一 connect 代码路径）
    return (
      <div className="wallet-bar">
        <span className="dim">未连接钱包——签名与交易需要浏览器钱包（仅读取地址）</span>
        <ConnectWalletButton size="normal" />
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
  );
}

export function AppShell() {
  return (
    <div className="app-shell">
      <nav className="side-nav">
        <div className="brand">
          <span className="dot" />
          CoinCall 控制台
        </div>
        <NavLink to="/" end className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
          总览
        </NavLink>
        <NavLink to="/provider" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
          Provider 工作台
        </NavLink>
        <NavLink to="/consumer" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
          消费端工作台
        </NavLink>
        <NavLink to="/help" className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
          帮助
        </NavLink>
        <div className="nav-foot">
          chainId 968 · BOT Chain
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
