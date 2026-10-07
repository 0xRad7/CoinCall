import { createHashRouter, Navigate, RouterProvider, useSearchParams } from "react-router-dom";
import { AppShell } from "./AppShell";
import Overview from "./pages/Overview";
import ProviderWorkbench from "./pages/ProviderWorkbench";
import ConsumerWorkbench from "./pages/ConsumerWorkbench";
import Help from "./pages/Help";
import Welcome from "./pages/Welcome";
import { useWallet } from "./state/WalletContext";

/**
 * 动线：/welcome（登录+选身份单页）→ /provider | /consumer（双工作台）；
 * / 总览为公共目录+决策视图（两端共用）——未连接默认落 /welcome，「先逛逛」(?browse=1) 可免连进入。
 */
function RootIndex() {
  const w = useWallet();
  const [params] = useSearchParams();
  const browse = params.get("browse") === "1";
  if (!w.address && !browse) return <Navigate to="/welcome" replace />;
  return <Overview />;
}

const router = createHashRouter([
  { path: "/welcome", element: <Welcome /> },
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <RootIndex /> },
      { path: "provider", element: <ProviderWorkbench /> },
      { path: "consumer", element: <ConsumerWorkbench /> },
      { path: "help", element: <Help /> },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
