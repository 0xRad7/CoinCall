import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * 后端同源代理（dev 与 preview 一致）：
 *   /api/core/*  → http://127.0.0.1:8020  (coincall-core)
 *   /api/gw/*    → http://127.0.0.1:8030  (coincall-gateway)
 *   /api/chain/* → http://127.0.0.1:8010  (coincall-bot-chain-api)
 * RPC (https://rpc.bohr.life/) 由浏览器直连（已验 CORS=*）。
 */
const proxy = {
  "/api/core": { target: "http://127.0.0.1:8020", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/api\/core/, "") },
  "/api/gw": { target: "http://127.0.0.1:8030", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/api\/gw/, "") },
  "/api/chain": { target: "http://127.0.0.1:8010", changeOrigin: true, rewrite: (p: string) => p.replace(/^\/api\/chain/, "") },
};

export default defineConfig({
  plugins: [react()],
  // host: true = 监听 0.0.0.0，局域网机器可访问（后端三服务仍仅本机，经此代理转发）
  // hmr:false=关闭热更新（发起人 2026-10-07）：编辑不再实时推送，改完统一重启前端
  server: { port: 5173, host: true, hmr: false, proxy,
    // 演示期隧道域名常换（ngrok / 51vip 等）——放行任意 Host（本机演示服务，风险可接受）
    allowedHosts: true },
  preview: { port: 5173, host: true, proxy, allowedHosts: true },
  test: {
    include: ["tests/**/*.test.{ts,tsx}"],
    environment: "node",
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          "vendor-react": ["react", "react-dom", "react-router-dom"],
        },
      },
    },
  },
} as ReturnType<typeof defineConfig>);
