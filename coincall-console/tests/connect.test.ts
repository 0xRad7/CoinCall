/**
 * 注入钱包层测试：EIP-1193 错误分类、BOT Chain 参数、显式 provider 的连接/切链
 * 状态机、legacy 回退顺序、6963 公告与 legacy 槽位的候选合并/去重、自动决策
 * （0/1/记忆 rdns/多选）、v 归一化、双路径签名等价、删除路径残留扫描。
 * （选择器交互 UI 用例见 selector.test.tsx）
 */
import { afterEach, describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Wallet } from "ethers";
import {
  BOT_CHAIN_PARAMS,
  collectCandidates,
  connectInjected,
  detectLegacyCandidate,
  discoverEip6963,
  ensureBotChain,
  friendlyInjectedError,
  isUnrecognizedChain,
  isUserRejected,
  resolveAutoChoice,
  silentAccounts,
  startEip6963Discovery,
  type Eip1193Provider,
  type Eip6963Announcement,
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

/** 显式构造 mock provider（链操作一律传入选中的 provider 引用）。 */
function mockWallet(handler: Handler): Eip1193Provider {
  return {
    request: ({ method, params }: { method: string; params?: unknown }) => Promise.resolve(handler(method, params)),
  };
}

afterEach(() => {
  const g = globalThis as { ethereum?: unknown; okxwallet?: unknown };
  delete g.ethereum;
  delete g.okxwallet;
});

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

describe("连接状态机（显式 provider）", () => {
  it("直连成功：账户 + 已在 968 链", async () => {
    const p = mockWallet((m) => {
      if (m === "eth_requestAccounts") return [ADDR];
      if (m === "eth_chainId") return hex968;
      throw new Error(`unexpected ${m}`);
    });
    const r = await connectInjected(p);
    expect(r.address).toBe(ADDR);
    expect(r.chainId).toBe(968);
  });

  it("用户拒绝连接（4001）→ 友好态而非崩栈", async () => {
    const p = mockWallet(() => Promise.reject(Object.assign(new Error("User rejected"), { code: 4001 })));
    try {
      await connectInjected(p);
      expect.unreachable();
    } catch (e) {
      expect(isUserRejected(e)).toBe(true);
      expect(friendlyInjectedError(e).title).toContain("取消");
    }
  });

  it("静默恢复（eth_accounts，不弹窗）", async () => {
    const p = mockWallet((m) => {
      if (m === "eth_accounts") return [ADDR];
      throw new Error(`unexpected ${m}`);
    });
    expect(await silentAccounts(p)).toBe(ADDR);
  });
});

describe("切链：ensureBotChain（走选中的 provider）", () => {
  it("已在 968：无操作", async () => {
    const calls: string[] = [];
    const p = mockWallet((m) => {
      calls.push(m);
      if (m === "eth_chainId") return hex968;
      throw new Error(`unexpected ${m}`);
    });
    expect(await ensureBotChain(p)).toBe(968);
    expect(calls).toEqual(["eth_chainId"]);
  });

  it("链不对 → wallet_switchEthereumChain 成功", async () => {
    let chain = "0x1";
    const calls: Array<{ m: string; p?: unknown }> = [];
    const p = mockWallet((m, params) => {
      calls.push({ m, p: params });
      if (m === "eth_chainId") return chain;
      if (m === "wallet_switchEthereumChain") {
        chain = hex968;
        return null;
      }
      throw new Error(`unexpected ${m}`);
    });
    expect(await ensureBotChain(p)).toBe(968);
    expect(calls.map((c) => c.m)).toEqual(["eth_chainId", "wallet_switchEthereumChain", "eth_chainId"]);
    expect(calls[1]!.p).toEqual([{ chainId: hex968 }]);
  });

  it("switch 报 4902（未添加）→ wallet_addEthereumChain（带完整网络参数）", async () => {
    let chain = "0x1";
    const calls: Array<{ m: string; p?: unknown }> = [];
    const p = mockWallet((m, params) => {
      calls.push({ m, p: params });
      if (m === "eth_chainId") return chain;
      if (m === "wallet_switchEthereumChain") return Promise.reject(Object.assign(new Error("Unrecognized chain"), { code: 4902 }));
      if (m === "wallet_addEthereumChain") {
        chain = hex968;
        return null;
      }
      throw new Error(`unexpected ${m}`);
    });
    expect(await ensureBotChain(p)).toBe(968);
    expect(calls[2]!.m).toBe("wallet_addEthereumChain");
    expect(calls[2]!.p).toEqual([{ ...BOT_CHAIN_PARAMS }]);
  });

  it("OKX 风格 -32603 内层 4902 也走添加网络", async () => {
    let chain = "0x1";
    const p = mockWallet((m) => {
      if (m === "eth_chainId") return chain;
      if (m === "wallet_switchEthereumChain")
        return Promise.reject(Object.assign(new Error("wrapper"), { code: -32603, data: { originalError: { code: 4902 } } }));
      if (m === "wallet_addEthereumChain") {
        chain = hex968;
        return null;
      }
      throw new Error(`unexpected ${m}`);
    });
    expect(await ensureBotChain(p)).toBe(968);
  });

  it("用户拒绝切链（4001）→ 可分类为友好取消", async () => {
    const p = mockWallet((m) => {
      if (m === "eth_chainId") return "0x1";
      if (m === "wallet_switchEthereumChain") return Promise.reject(Object.assign(new Error("rejected"), { code: 4001 }));
      throw new Error(`unexpected ${m}`);
    });
    try {
      await ensureBotChain(p);
      expect.unreachable();
    } catch (e) {
      expect(isUserRejected(e)).toBe(true);
      expect(friendlyInjectedError(e).title).toContain("取消");
    }
  });

  it("切链后仍不是 968 → 明确报错（不静默错链）", async () => {
    const p = mockWallet((m) => {
      if (m === "eth_chainId") return "0x1";
      if (m === "wallet_switchEthereumChain") return null; // 假装成功但链没变
      throw new Error(`unexpected ${m}`);
    });
    await expect(ensureBotChain(p)).rejects.toThrow(/仍不是 968/);
  });
});

describe("legacy 回退顺序（EIP-6963 无响应的旧扩展）", () => {
  it("okxwallet 专属槽位最优先（修复：不再被通用槽位的 Core 短路）", () => {
    const okx = mockWallet(() => null);
    const core = mockWallet(() => null);
    const c = detectLegacyCandidate({ okxwallet: okx, ethereum: core })!;
    expect(c.source).toBe("legacy-okxwallet");
    expect(c.provider).toBe(okx);
    expect(c.name).toBe("OKX Wallet");
  });
  it("无 okxwallet：ethereum.isOkxWallet === true 认 OKX", () => {
    const p = mockWallet(() => null);
    p.isOkxWallet = true;
    const c = detectLegacyCandidate({ ethereum: p })!;
    expect(c.source).toBe("legacy-ethereum-okx");
    expect(c.name).toBe("OKX Wallet");
  });
  it("isMetaMask === true 认 MetaMask", () => {
    const p = mockWallet(() => null);
    p.isMetaMask = true;
    const c = detectLegacyCandidate({ ethereum: p })!;
    expect(c.source).toBe("legacy-ethereum-metamask");
    expect(c.name).toBe("MetaMask");
  });
  it("无身份通用槽位兜底（Core 这类只有独占时才命中）", () => {
    const p = mockWallet(() => null);
    const c = detectLegacyCandidate({ ethereum: p })!;
    expect(c.source).toBe("legacy-ethereum");
    expect(c.provider).toBe(p);
  });
  it("全空 → null", () => {
    expect(detectLegacyCandidate({})).toBeNull();
  });
});

describe("6963 公告 + legacy 槽位合并（候选去重）", () => {
  const okxAnn: Eip6963Announcement = {
    info: { uuid: "u1", name: "OKX Wallet", icon: "data:image/svg+xml,okx", rdns: "com.okex.wallet" },
    provider: mockWallet(() => null),
  };
  const mmAnn: Eip6963Announcement = {
    info: { uuid: "u2", name: "MetaMask", icon: "", rdns: "io.metamask" },
    provider: mockWallet(() => null),
  };

  it("同一 provider 既公告又占槽位 → 只留公告（引用相等去重）", () => {
    const legacy = { name: "OKX Wallet", rdns: null, provider: okxAnn.provider, source: "legacy-okxwallet" as const };
    const out = collectCandidates([okxAnn], legacy);
    expect(out).toHaveLength(1);
    expect(out[0]!.source).toBe("eip6963");
  });
  it("OKX 已公告 → 占据 okxwallet 槽位的另一个对象也被指纹去重", () => {
    const legacy = { name: "OKX Wallet", rdns: null, provider: mockWallet(() => null), source: "legacy-okxwallet" as const };
    const out = collectCandidates([okxAnn], legacy);
    expect(out).toHaveLength(1);
  });
  it("MetaMask 已公告 → 通用槽位的 isMetaMask 对象被去重", () => {
    const eth = mockWallet(() => null);
    eth.isMetaMask = true;
    const legacy = detectLegacyCandidate({ ethereum: eth })!;
    const out = collectCandidates([mmAnn], legacy);
    expect(out).toHaveLength(1);
  });
  it("公告的是 OKX，通用槽位是被 Core 抢注的无身份对象 → 两个候选（选择器裁决）", () => {
    const core = mockWallet(() => null);
    const legacy = detectLegacyCandidate({ ethereum: core })!; // 无 is* 标志
    const out = collectCandidates([okxAnn], legacy);
    expect(out).toHaveLength(2);
    expect(out[0]!.source).toBe("eip6963"); // 公告到达序在前
    expect(out[1]!.source).toBe("legacy-ethereum"); // Core 追加在后
  });
  it("无 legacy：仅公告，保持到达序", () => {
    const out = collectCandidates([okxAnn, mmAnn], null);
    expect(out.map((c) => c.rdns)).toEqual(["com.okex.wallet", "io.metamask"]);
  });
});

describe("自动决策 resolveAutoChoice", () => {
  const a = { name: "A", rdns: "a.x", provider: mockWallet(() => null), source: "eip6963" as const };
  const b = { name: "B", rdns: "b.x", provider: mockWallet(() => null), source: "eip6963" as const };

  it("0 个 → none", () => {
    expect(resolveAutoChoice([], null)).toEqual({ type: "none" });
  });
  it("1 个 → auto 直连", () => {
    expect(resolveAutoChoice([a], null)).toEqual({ type: "auto", candidate: a });
  });
  it("多个 + 记住的 rdns 命中 → auto（下次默认选中）", () => {
    expect(resolveAutoChoice([a, b], "b.x")).toEqual({ type: "auto", candidate: b });
  });
  it("多个 + 无记忆/记忆未命中 → 弹选择器", () => {
    expect(resolveAutoChoice([a, b], null).type).toBe("choice");
    expect(resolveAutoChoice([a, b], "zzz").type).toBe("choice");
  });
});

describe("EIP-6963 发现（事件接线，用注入的 EventTarget）", () => {
  it("requestProvider 广播后收集 announceProvider（到达序、可清理）", async () => {
    const target = new EventTarget();
    const seen: string[] = [];
    const stop = startEip6963Discovery((a) => seen.push(a.info.rdns), target);
    target.dispatchEvent(new CustomEvent("eip6963:announceProvider", { detail: { info: { uuid: "u1", name: "W1", icon: "", rdns: "one.x" }, provider: mockWallet(() => null) } }));
    target.dispatchEvent(new CustomEvent("eip6963:announceProvider", { detail: { info: { uuid: "u2", name: "W2", icon: "", rdns: "two.x" }, provider: mockWallet(() => null) } }));
    expect(seen).toEqual(["one.x", "two.x"]);
    stop();
    target.dispatchEvent(new CustomEvent("eip6963:announceProvider", { detail: { info: { uuid: "u3", name: "W3", icon: "", rdns: "three.x" }, provider: mockWallet(() => null) } }));
    expect(seen).toEqual(["one.x", "two.x"]); // 已清理
  });
  it("discoverEip6963：窗口期收集后停止", async () => {
    const target = new EventTarget();
    const p = discoverEip6963(target, 30);
    target.dispatchEvent(new CustomEvent("eip6963:announceProvider", { detail: { info: { uuid: "u", name: "W", icon: "", rdns: "w.x" }, provider: mockWallet(() => null) } }));
    const list = await p;
    expect(list.map((a) => a.info.rdns)).toEqual(["w.x"]);
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
    expect(sigFromJoined("0x" + r.slice(2) + s.slice(2) + "1c")).toEqual({ v: 28, r, s }); // 0x1c=28
    expect(sigFromJoined("0x" + r.slice(2) + s.slice(2) + "00")).toEqual({ v: 27, r, s }); // v=0 → 27
    expect(sigFromJoined("0x" + r.slice(2) + s.slice(2) + "01")).toEqual({ v: 28, r, s }); // v=1 → 28
    expect(() => sigFromJoined("0x1234")).toThrow(/65 字节/);
  });

  it("归一后的 v/r/s 直接进 X-PAYMENT", () => {
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
  it("Wallet.signTypedData(authorizationTypedData) 与 signAuthorization 同一签名", async () => {
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
    expect(sigFromJoined(viaExtensionLike)).toEqual(manual);
    expect(eip712Digest(auth, PAY_VAULT)).toMatch(/^0x[0-9a-f]{64}$/);
  });
});

describe("删除路径不残留（不留尸体）", () => {
  const root = join(__dirname, "..");
  const read = (p: string) => readFileSync(join(root, p), "utf-8");

  it("消费端页面与钱包上下文没有私钥导入入口", () => {
    for (const f of ["src/pages/ConsumerWorkbench.tsx", "src/state/WalletContext.tsx"]) {
      const s = read(f);
      expect(s, f).not.toContain("importKey");
      expect(s, f).not.toContain("导入已有私钥");
      expect(s, f).not.toContain("粘贴私钥");
      // password 型输入只允许出现在 API key 粘贴框（不得用于私钥语义）
      const ls = s.split("\n");
      for (let i = 0; i < ls.length; i++) {
        if (ls[i]!.includes('type="password"')) {
          expect(ls.slice(i, i + 6).join("\n"), f).toContain('aria-label="粘贴 API key"'); // password 输入必须且只能是 API key 粘贴框
        }
      }
    }
    expect(read("src/state/WalletContext.tsx")).not.toContain('type="password"');
  });
  it("rpc.ts 不再承载注入钱包逻辑（已迁 injected.ts）", () => {
    const s = read("src/chain/rpc.ts");
    expect(s).not.toContain("injectedSignTypedData");
    expect(s).not.toContain("injectedSendProviderWithdraw");
    expect(s).not.toContain("window.ethereum");
  });
  it("injected.ts 不再有「通用槽位短路专属槽位」的旧检测", () => {
    const s = read("src/chain/injected.ts");
    expect(s).not.toContain("g.ethereum ?? g.okxwallet"); // 修复的 bug 模式
    expect(s).not.toContain("getInjected()");
  });
});
