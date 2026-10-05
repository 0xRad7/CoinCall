import { createHashRouter, RouterProvider } from "react-router-dom";
import { AppShell } from "./AppShell";
import Overview from "./pages/Overview";
import ProviderWorkbench from "./pages/ProviderWorkbench";
import ConsumerWorkbench from "./pages/ConsumerWorkbench";
import Help from "./pages/Help";

const router = createHashRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Overview /> },
      { path: "provider", element: <ProviderWorkbench /> },
      { path: "consumer", element: <ConsumerWorkbench /> },
      { path: "help", element: <Help /> },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
