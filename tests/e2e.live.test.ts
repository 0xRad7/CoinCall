/**
 * E2E（真实后端 + 本仓真实代码路径，需 dev server 5173 与三个后端已启动）：
 *   经 vite 代理签发 api key → 本地 ethers Wallet 组装授权并签名（signing.ts 唯一路径）
 *   → X-PAYMENT → POST /api/gw/call/svc_translate → 断言 200 + 收据头 → 轮询 keeper 结算新批。
 * 运行：npx vitest run tests/e2e.live.test.ts（不在默认 npm test 范围内）
 */
import { describe, expect, it } from "vitest";
import { Wallet } from "ethers";
import { apiFetch, jsonInit } from "../src/api/client";
import { buildCallAuthorization, buildPaymentHeader, signAuthorization } from "../src/chain/signing";
import { PAY_VAULT } from "../src/chain/constants";
import { approveVault, fetchAllowance, fetchTokenBalance, mintMockUsdt } from "../src/chain/rpc";
import { hashSha256HexStringish } from "../src/lib/idempotency";

const BASE = "http://127.0.0.1:5173"; // vite dev（同源代理 → core/gw）
const ANVIL1_PK = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d";

describe.skipIf(process.env.CI || !process.env.COINCALL_E2E)("E2E：anvil#1 付费调用全流程（真实链/真实后端）", () => {
  const wallet = new Wallet(ANVIL1_PK);
  let keeperBaseline = 0;
  const SVC = "svc_translate";
  const PRICE_RAW = 10_000n; // 0.01 USDT

  it("① 目录与余额（经 vite 代理读 core；RPC 读链）", async () => {
    const cat = await apiFetch<{ services: Array<{ service_id: string; manifest: { pricing: { amount_raw: string } } }> }>(`${BASE}/api/core/catalog`).then((r) => r.data);
    const svc = cat.services.find((s) => s.service_id === SVC);
    expect(svc).toBeTruthy();
    expect(BigInt(svc!.manifest.pricing.amount_raw)).toBe(PRICE_RAW);
    const bal = await fetchTokenBalance(wallet.address);
    const allow = await fetchAllowance(wallet.address);
    // UI 状态：三数展示（若缺额走 mint/approve——当前余额充足则跳过）
    if (bal < PRICE_RAW) {
      await mintMockUsdt(wallet, wallet.address, 10_000_000n);
    }
    if (allow < PRICE_RAW) {
      await approveVault(wallet, PRICE_RAW * 10n);
    }
    const bal2 = await fetchTokenBalance(wallet.address);
    const allow2 = await fetchAllowance(wallet.address);
    expect(bal2 >= PRICE_RAW && allow2 >= PRICE_RAW).toBe(true);
  });

  it("② 签发 API key（core POST /apikeys，绑定当前钱包）", async () => {
    const r = await apiFetch<{ key_id: string; api_key: string; consumer_wallet: string }>(`${BASE}/api/core/apikeys`, {
      ...jsonInit("POST", { consumer_wallet: wallet.address }),
    }).then((x) => x.data);
    expect(r.api_key.length).toBeGreaterThan(10);
    expect(r.consumer_wallet.toLowerCase()).toBe(wallet.address.toLowerCase());
  });

  it("③ 本地签名 → X-PAYMENT → POST /api/gw/call/svc_translate → 200 + 收据", async () => {
    const key = await apiFetch<{ api_key: string }>(`${BASE}/api/core/apikeys`, jsonInit("POST", { consumer_wallet: wallet.address })).then((x) => x.data.api_key);
    // 调用前基线（keeper 可能在调用后任意 ≤flush_interval 内结算，基线必须先取）
    const baseline = await apiFetch<{ cumulative_charged_count: number }>(`${BASE}/api/gw/internal/keeper/status`).then((r) => r.data);
    keeperBaseline = baseline.cumulative_charged_count;
    const now = Math.floor(Date.now() / 1000);
    const auth = buildCallAuthorization(wallet.address, PAY_VAULT, PRICE_RAW, now);
    const sig = signAuthorization(wallet, auth, PAY_VAULT);
    const payment = buildPaymentHeader(auth, sig);
    const params = { text: `hello coincall @${Date.now()}` }; // 每次 run 唯一参数（同参会被网关幂等去重，不重复计费）
    const idem = await hashSha256HexStringish(`${SVC}:${JSON.stringify(params)}`);
    const r = await apiFetch<unknown>(`${BASE}/api/gw/call/${SVC}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Api-Key": key, "X-PAYMENT": payment, "X-Idempotency-Key": idem },
      body: JSON.stringify(params),
      timeoutMs: 60_000,
    });
    expect(r.status).toBe(200);
    expect(r.headers.get("X-Receipt-Id")).toMatch(/^rcp_/);
    expect(BigInt(r.headers.get("X-Charged-Raw") ?? "0")).toBe(PRICE_RAW);
    console.log("收据:", r.headers.get("X-Receipt-Id"), "charged_raw:", r.headers.get("X-Charged-Raw"), "响应体:", JSON.stringify(r.data).slice(0, 160));
  });

  it("④ keeper ≤60s 结算新批（settle 队列 → 链上 Charged）", async () => {
    const before = keeperBaseline;
    let status = { cumulative_charged_count: before };
    const deadline = Date.now() + 90_000;
    while (Date.now() < deadline) {
      await new Promise((res) => setTimeout(res, 5_000));
      status = await apiFetch<{ cumulative_charged_count: number }>(`${BASE}/api/gw/internal/keeper/status`).then((r) => r.data);
      if (status.cumulative_charged_count > before) break;
    }
    expect(status.cumulative_charged_count).toBeGreaterThan(before);
    const ov = await apiFetch<{ charged_count: number; gmv_raw: number }>(`${BASE}/api/core/stats/overview`).then((r) => r.data);
    expect(ov.charged_count).toBeGreaterThanOrEqual(status.cumulative_charged_count);
    console.log("keeper 批量上链完成：cumulative", before, "→", status.cumulative_charged_count, "· 总览 GMV raw:", ov.gmv_raw);
  }, 120_000);

  it("⑤ 资金按钮等价路径：本地钱包直签 raw tx（mint）pending→confirmed", async () => {
    const before = await fetchTokenBalance(wallet.address);
    const receipt = await mintMockUsdt(wallet, wallet.address, 10_000_000n);
    const after = await fetchTokenBalance(wallet.address);
    expect(receipt.status).toBe(1);
    expect(after - before).toBe(10_000_000n);
    console.log("mint 10 MockUSDT 上链:", receipt.hash, "余额 raw:", before.toString(), "→", after.toString());
  }, 60_000);
});
