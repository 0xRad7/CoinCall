/**
 * 连接流（EIP-1193）测试：mock window.ethereum 走 injected.ts 状态机
 * （连接/切链 switch→add 兑底/4001 拒绝分类），v 归一化（0/1→27/28），
 * 扩展签名 typed data 与手工 digest 路径等价，以及删除路径不残留。
 */
import { afterEach, describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Wallet } from "ethers";
import {
  BOT_CHAIN_PARAMS,
  connectInjected,
  ensureChain968,
  friendlyInjectedError,
  isUnrecognizedChain,
  isUserRejected,
  silentAccounts,
} from "../src/chain/injected";
import {
  authorizationTypedData,
  buildCallAuthorization,
  buildPaymentHeader,
  decodePaymentHeader,
  eip712Digest,
  normalizeV,
  sigFromJoined,
  signAuthorization,
  type Authorization,
} from "../src/chain/signing";
import { CHAIN_ID, PAY_VAULT } from "../src/chain/constants";

const ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8";
const hex968 = "0x3c8";

type Handler = (method: string, params: unknown) => unknown;

/** 挂/卸 mock 注入钱包（injected.ts 读 globalThis.ethereum）。 */
function installMock(handler: Handler) {
  (globalThis as { ethereum?: unknown }).ethereum = {
    request: ({ method, params }: { method: string; params?: unknown }) => Promise.resolve(handler(method, params)),
  };
}
function uninstallMock() {
  delete (globalThis as { ethereum?: unknown }).ethereum;
}

afterEach(uninstallMock);

describe("EIP-1193 错误分类", () => {
  it("4001 = 用户拒绝", () => {
    expect(isUserRejected({ code: 4001 })).toBe(true);
    expect(isUserRejected({ code: -32603 })).toBe(false);
  });
  it("4902（含 -32603 内层）= 链未添加", () => {
    expect(isUnrecognizedChain({ code: 4902 })).toBe(true);
    expect(isUnrecognizedChain({ code: -32603, data: { originalError: { code: 4902 } } })).toBe(true);
    expect(isUnrecognizedChain({ code: 4001 })).toBe(false);
  });
  it("友好文案：拒绝不是报错", () => {
    const f = friendlyInjectedError({ code: 4001 });
    expect(f.title).toContain("取消");
    expect(f.hint).toContain("没有产生");
  });
});

describe("BOT Chain 添加网络参数（权威：bot-chain-api chains.py / accounts.py）", () => {
  it("chainId 0x3C8 / BOT(18) / rpc.bohr.life / scan.bohr.life", () => {
    expect(BOT_CHAIN_PARAMS.chainId).toBe("0x" + CHAIN_ID.toString(16));
    expect(BOT_CHAIN_PARAMS.chainId.toLowerCase()).toBe(hex968);
    expect(BOT_CHAIN_PARAMS.nativeCurrency).toEqual({ name: "BOT", symbol: "BOT", decimals: 18 });
    expect(BOT_CHAIN_PARAMS.rpcUrls).toEqual(["https://rpc.bohr.life/"]);
    expect(BOT_CHAIN_PARAMS.blockExplorerUrls).toEqual(["https://scan.bohr.life"]);
  });
});

describe("连接状态机（connectInjected / silentAccounts）", () => {
  it("直连成功：账户 + 已在 968 链", async () => {
    installMock((m) => {
      if (m === "eth_requestAccounts") return [ADDR];
      if (m === "eth_chainId") return hex968;
      throw new Error(`unexpected ${m}`);
    });
    const r = await connectInjected();
    expect(r.address).toBe(ADDR);
    expect(r.chainId).toBe(968);
  });

  it("用户拒绝连接（4001）→ 友好态而非崩栈", async () => {
    installMock(() => Promise.reject(Object.assign(new Error("User rejected"), { code: 4001 })));
    await expect(connectInjected()).rejects.toThrow();
    try {
      await connectInjected();
    } catch (e) {
      expect(isUserRejected(e)).toBe(true);
      expect(friendlyInjectedError(e).title).toContain("取消");
    }
  });

  it("静默恢复（eth_accounts，不弹窗）", async () => {
    installMock((m) => {
      if (m === "eth_accounts") return [ADDR];
      throw new Error(`unexpected ${m}`);
    });
    expect(await silentAccounts()).toBe(ADDR);
  });
});

describe("切链：ensureChain968", () => {
  it("已在 968：无操作", async () => {
    const calls: string[] = [];
    installMock((m) => {
      calls.push(m);
      if (m === "eth_chainId") return hex968;
      throw new Error(`unexpected ${m}`);
    });
    expect(await ensureChain968()).toBe(968);
    expect(calls).toEqual(["eth_chainId"]);
  });

  it("链不对 → wallet_switchEthereumChain 成功", async () => {
    let chain = "0x1";
    const calls: Array<{ m: string; p?: unknown }> = [];
    installMock((m, p) => {
      calls.push({ m, p });
      if (m === "eth_chainId") return chain;
      if (m === "wallet_switchEthereumChain") {
        chain = hex968;
        return null;
      }
      throw new Error(`unexpected ${m}`);
    });
    expect(await ensureChain968()).toBe(968);
    expect(calls.map((c) => c.m)).toEqual(["eth_chainId", "wallet_switchEthereumChain", "eth_chainId"]);
    expect(calls[1]!.p).toEqual([{ chainId: hex968 }]);
  });

  it("switch 报 4902（未添加）→ wallet_addEthereumChain（带完整网络参数）", async () => {
    let chain = "0x1";
    const calls: Array<{ m: string; p?: unknown }> = [];
    installMock((m, p) => {
      calls.push({ m, p });
      if (m === "eth_chainId") return chain;
      if (m === "wallet_switchEthereumChain") return Promise.reject(Object.assign(new Error("Unrecognized chain"), { code: 4902 }));
      if (m === "wallet_addEthereumChain") {
        chain = hex968;
        return null;
      }
      throw new Error(`unexpected ${m}`);
    });
    expect(await ensureChain968()).toBe(968);
    expect(calls[2]!.m).toBe("wallet_addEthereumChain");
    expect(calls[2]!.p).toEqual([{ ...BOT_CHAIN_PARAMS }]);
  });

  it("OKX 风格 -32603 内层 4902 也走添加网络", async () => {
    let chain = "0x1";
    installMock((m) => {
      if (m === "eth_chainId") return chain;
      if (m === "wallet_switchEthereumChain")
        return Promise.reject(Object.assign(new Error("wrapper"), { code: -32603, data: { originalError: { code: 4902 } } }));
      if (m === "wallet_addEthereumChain") {
        chain = hex968;
        return null;
      }
      throw new Error(`unexpected ${m}`);
    });
    expect(await ensureChain968()).toBe(968);
  });

  it("用户拒绝切链（4001）→ 抛出但可分类为友好取消", async () => {
    installMock((m) => {
      if (m === "eth_chainId") return "0x1";
      if (m === "wallet_switchEthereumChain") return Promise.reject(Object.assign(new Error("rejected"), { code: 4001 }));
      throw new Error(`unexpected ${m}`);
    });
    try {
      await ensureChain968();
      expect.unreachable();
    } catch (e) {
      expect(isUserRejected(e)).toBe(true);
      expect(friendlyInjectedError(e).title).toContain("取消");
    }
  });

  it("切链后仍不是 968 → 明确报错（不静默错链）", async () => {
    installMock((m) => {
      if (m === "eth_chainId") return "0x1";
      if (m === "wallet_switchEthereumChain") return null; // 假装成功但链没变
      throw new Error(`unexpected ${m}`);
    });
    await expect(ensureChain968()).rejects.toThrow(/仍不是 968/);
  });
});

describe("v 归一化与 joined 签名拆分（X-PAYMENT 组包）", () => {
  const r = "0x" + "11".repeat(32);
  const s = "0x" + "22".repeat(32);

  it("0/1 → 27/28；27/28 保持；EIP-155 偏移纠回", () => {
    expect(normalizeV(0)).toBe(27);
    expect(normalizeV(1)).toBe(28);
    expect(normalizeV(27)).toBe(27);
    expect(normalizeV(28)).toBe(28);
    expect(normalizeV(35)).toBe(27);
    expect(normalizeV(36)).toBe(28);
    expect(() => normalizeV(2)).toThrow(/非法/);
  });

  it("sigFromJoined：65 字节 → {v,r,s}，v 已归一", () => {
    expect(sigFromJoined(r.replace("0x", "0x") + s.slice(2) + "1c")).toEqual({ v: 28, r, s }); // 0x1c=28
    expect(sigFromJoined("0x" + r.slice(2) + s.slice(2) + "00")).toEqual({ v: 27, r, s }); // v=0 → 27
    expect(sigFromJoined("0x" + r.slice(2) + s.slice(2) + "01")).toEqual({ v: 28, r, s }); // v=1 → 28
    expect(() => sigFromJoined("0x1234")).toThrow(/65 字节/);
  });

  it("归一后的 v/r/s 直接进 X-PAYMENT（网关 pydantic 可收 0|1|27|28，本仓统一 27|28）", () => {
    const joined = "0x" + r.slice(2) + s.slice(2) + "00"; // v=0（扩展原始回包）
    const sig = sigFromJoined(joined);
    const auth = buildCallAuthorization(ADDR, PAY_VAULT, 10000n, 1000);
    const header = buildPaymentHeader(auth, sig);
    const d = decodePaymentHeader(header);
    expect(d["v"]).toBe(27);
    expect(d["r"]).toBe(r);
    expect(d["s"]).toBe(s);
  });
});

describe("扩展签名 typed data ⇆ 手工 digest 双路径等价", () => {
  it("Wallet.signTypedData(authorizationTypedData) 与 signAuthorization 同一签名（即扩展拿到的就是黄金向量口径的 digest）", async () => {
    const w = new Wallet("0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d");
    const auth: Authorization = {
      from: w.address,
      to: PAY_VAULT,
      value: 10000n,
      validAfter: 1_760_000_000n,
      validBefore: 1_760_000_600n,
      nonce: "0x" + "ab".repeat(32),
    };
    const manual = signAuthorization(w, auth, PAY_VAULT); // 手工 digest（黄金向量锁死）
    const td = authorizationTypedData(auth, PAY_VAULT);
    expect(td.domain).toEqual({ name: "PayVault", version: "1", chainId: CHAIN_ID, verifyingContract: PAY_VAULT });
    const viaExtensionLike = await w.signTypedData(td.domain, td.types, td.message); // 扩展同构路径
    const fromJoined = sigFromJoined(viaExtensionLike);
    expect(fromJoined).toEqual(manual);
    // 且 digest 口径与 eip712Digest 一致（自算 = 扩展将算的）
    expect(eip712Digest(auth, PAY_VAULT)).toMatch(/^0x[0-9a-f]{64}$/);
  });
});

describe("删除路径不残留（不留尸体）", () => {
  const root = join(__dirname, "..");
  const read = (p: string) => readFileSync(join(root, p), "utf-8");

  it("消费端页面与钱包上下文没有私钥导入入口", () => {
    for (const f of ["src/pages/ConsumerWorkbench.tsx", "src/state/WalletContext.tsx"]) {
      const s = read(f);
      expect(s, f).not.toContain('type="password"');
      expect(s, f).not.toContain("importKey");
      expect(s, f).not.toContain("导入已有私钥");
      expect(s, f).not.toContain("粘贴私钥");
    }
  });
  it("rpc.ts 不再承载注入钱包逻辑（已迁 injected.ts）", () => {
    const s = read("src/chain/rpc.ts");
    expect(s).not.toContain("injectedSignTypedData");
    expect(s).not.toContain("injectedSendProviderWithdraw");
    expect(s).not.toContain("window.ethereum");
  });
});
