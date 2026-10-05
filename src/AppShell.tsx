import { NavLink, Outlet } from "react-router-dom";

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
          全部数据本地处理
        </div>
      </nav>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
