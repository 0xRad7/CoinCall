/**
 * API 封装层测试：错误归一（三段模型 / pydantic 422 逐字段 / 网络错误人话）、
 * 请求头组装、金额换算、黄金向量外的签名护栏。
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { apiFetch, ApiError, jsonInit, normalizeErrorBody } from "../src/api/client";
import { agentWalletSetTypedData, buildCallHeaders } from "../src/api/gateway";
import { fromRaw, toRaw } from "../src/chain/constants";
import { buildCallAuthorization, buildPaymentHeader, decodePaymentHeader, signAuthorization } from "../src/chain/signing";
import { hashSha256HexStringish } from "../src/lib/idempotency";
import { humanizeChallenge, humanizeError } from "../src/lib/errors";
import { Wallet } from "ethers";

describe("normalizeErrorBody / ApiError", () => {
  it("三段错误模型 {error, detail, code, trace_id}", () => {
    const n = normalizeErrorBody({ error: "payment_required", detail: "本服务按次计费", code: "insufficient_balance", trace_id: "t1" });
    expect(n.error).toBe("payment_required");
    expect(n.detail).toBe("本服务按次计费");
    expect(n.code).toBe("insufficient_balance");
    expect(n.traceId).toBe("t1");
  });

  it("pydantic 422：detail 数组 → fieldErrors（剥掉 body 前缀）", () => {
    const n = normalizeErrorBody({
      detail: [
        { loc: ["body", "pricing", "amount_raw"], msg: "string太小", type: "value_error" },
        { loc: ["body", "service_id"], msg: "不能为空", type: "missing" },
      ],
    });
    expect(n.fieldErrors).toEqual({ "pricing.amount_raw": "string太小", service_id: "不能为空" });
  });

  it("apiFetch 非 2xx 抛 ApiError，保留原始体", async () => {
    const mock = vi.fn(async () => new Response(JSON.stringify({ error: "e", detail: "d", code: "c", trace_id: "t" }), { status: 422 }));
    vi.stubGlobal("fetch", mock);
    try {
      await apiFetch("/api/core/providers");
      expect.unreachable();
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      const ae = e as ApiError;
      expect(ae.status).toBe(422);
      expect(ae.detail).toBe("d");
      expect(ae.raw).toEqual({ error: "e", detail: "d", code: "c", trace_id: "t" });
    }
    vi.unstubAllGlobals();
  });

  it("网络失败 → status 0 人话", async () => {
    const mock = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    vi.stubGlobal("fetch", mock);
    try {
      await apiFetch("/api/gw/call/x");
      expect.unreachable();
    } catch (e) {
      const ae = e as ApiError;
      expect(ae.status).toBe(0);
      const h = humanizeError(ae);
      expect(h.hint).toContain("npm run dev");
    }
    vi.unstubAllGlobals();
  });
});

describe("humanizeError / humanizeChallenge", () => {
  it("identity_not_found → 人话 + 下一步", () => {
    const ae = new ApiError({ status: 422, error: "validation", detail: "identity_not_found: agent 999 未注册", code: "identity_not_found", traceId: "", fieldErrors: {}, raw: {} });
    const h = humanizeError(ae);
    expect(h.title).toContain("Agent 身份");
    expect(h.hint).toContain("ERC-8004");
  });

  it("402 insufficient_allowance → 动作=去授权且带金额", () => {
    const h = humanizeChallenge({
      error: "payment_required",
      detail: "授权不足",
      code: "insufficient_allowance",
      service_id: "svc_translate",
      pricing: { amount: "0.01", amount_raw: "10000", token: "USDT" },
      wallet_balance_raw: "99000000",
      payment: { scheme: "erc3009-vault", header: "X-PAYMENT", domain: { name: "PayVault", version: "1", chainId: 968, verifyingContract: "0x" }, approve_to: "0x" },
      trace_id: "t",
    });
    expect(h.action).toBe("approve");
    expect(h.amount).toBe("0.01");
  });

  it("ethers 私钥错误 → 人话", () => {
    expect(humanizeError(new Error("invalid private key")).title).toContain("私钥");
  });
});

describe("金额换算（6 位精度）", () => {
  it("toRaw/fromRaw 与服务定价口径一致", () => {
    expect(toRaw("0.01").toString()).toBe("10000");
    expect(toRaw("0.02").toString()).toBe("20000");
    expect(toRaw("0.05").toString()).toBe("50000");
    expect(toRaw("10").toString()).toBe("10000000");
    expect(toRaw("1.234567").toString()).toBe("1234567");
    expect(fromRaw(10000n)).toBe("0.01");
    expect(fromRaw(10000000n)).toBe("10");
    expect(fromRaw(1234567n)).toBe("1.234567");
  });
  it("非法输入报错", () => {
    expect(() => toRaw("-1")).toThrow();
    expect(() => toRaw("1.2345678")).toThrow(/精度/);
    expect(() => toRaw("abc")).toThrow();
  });
});

describe("调用头组装（试用调用同一路径）", () => {
  const w = new Wallet("0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"); // anvil#1（公开测试账户）

  it("buildCallAuthorization：窗口/随机 nonce/from=to 校验", () => {
    const a = buildCallAuthorization(w.address, "0xFe91F55C0e7Ccbc4A6619C67544Ab453cf79C471", 10000n, 1_000_000);
    expect(a.from).toBe(w.address);
    expect(a.validAfter).toBe(1_000_000n);
    expect(a.validBefore).toBe(1_000_600n);
    expect(a.nonce).toMatch(/^0x[0-9a-f]{64}$/);
  });

  it("X-PAYMENT：字段别名齐全且可被网关 pydantic 接受（value=十进制字符串）", () => {
    const a = buildCallAuthorization(w.address, "0xFe91F55C0e7Ccbc4A6619C67544Ab453cf79C471", 10000n, 1_000_000);
    const sig = signAuthorization(w, a, "0xFe91F55C0e7Ccbc4A6619C67544Ab453cf79C471");
    const header = buildPaymentHeader(a, sig);
    const d = decodePaymentHeader(header) as Record<string, unknown>;
    expect(d["value"]).toBe("10000"); // 字符串（网关 _DECIMAL_RE）
    expect(Number(d["validAfter"])).toBe(1000000);
    expect(Number(d["validBefore"])).toBe(1000600);
    expect(Number(d["v"]) === 27 || Number(d["v"]) === 28).toBe(true);
    expect(d["r"]).toMatch(/^0x[0-9a-f]{64}$/);
    expect(d["s"]).toMatch(/^0x[0-9a-f]{64}$/);
    expect(d["from"]).toBe(w.address);
  });

  it("buildCallHeaders：X-Api-Key / X-PAYMENT / 幂等键齐全", () => {
    const hs = buildCallHeaders("ck_live_x", "cGF5bG9hZA==", "abc123");
    expect(hs["X-Api-Key"]).toBe("ck_live_x");
    expect(hs["X-PAYMENT"]).toBe("cGF5bG9hZA==");
    expect(hs["X-Idempotency-Key"]).toBe("abc123");
  });

  it("幂等键：同参同键、异参异键", async () => {
    const a = await hashSha256HexStringish("svc_translate:" + JSON.stringify({ text: "hi" }));
    const b = await hashSha256HexStringish("svc_translate:" + JSON.stringify({ text: "hi" }));
    const c = await hashSha256HexStringish("svc_translate:" + JSON.stringify({ text: "hello" }));
    expect(a).toBe(b);
    expect(a).not.toBe(c);
    expect(a).toMatch(/^[0-9a-f]{64}$/);
  });

  it("jsonInit：方法+JSON 体", () => {
    const init = jsonInit("POST", { agent_id: 1 });
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(init.body).toBe('{"agent_id":1}');
  });
});

describe("AgentWalletSet typed data（钱包绑定）", () => {
  it("域与 typehash 契约", () => {
    const td = agentWalletSetTypedData({
      agentId: 162,
      newWallet: "0xAdeE6D874ceC4b8Dba0C969585bd2E593ddfba3D",
      owner: "0xC37fFE97B4D2C3D0187B1dDEDF273E52A461B63a",
      deadline: 1_760_000_000,
      verifyingContract: "0xec8fFbC3c9A34AdDCbB3A14F91db2bd26A8b99c0",
      chainId: 968,
    });
    expect(td.domain).toEqual({ name: "ERC8004IdentityRegistry", version: "1", chainId: 968, verifyingContract: "0xec8fFbC3c9A34AdDCbB3A14F91db2bd26A8b99c0" });
    expect(td.primaryType).toBe("AgentWalletSet");
    expect(td.types["AgentWalletSet"]!.map((f) => `${f.type} ${f.name}`).join(",")).toBe(
      "uint256 agentId,address newWallet,address owner,uint256 deadline"
    );
    expect(td.message).toEqual({ agentId: 162, newWallet: "0xAdeE6D874ceC4b8Dba0C969585bd2E593ddfba3D", owner: "0xC37fFE97B4D2C3D0187B1dDEDF273E52A461B63a", deadline: 1_760_000_000 });
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});
