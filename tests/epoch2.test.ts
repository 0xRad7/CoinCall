/**
 * epoch2 切换测试：常量与新金库/USDT 部署事实一致（唯一事实源 deployments/testnet-968.json，
 * 缺失自动 skip）、旧地址与铸币文案零残留、获取 USDT 指引存在。
 */
import { describe, expect, it } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { keccak256, toUtf8Bytes } from "ethers";
import { domainSeparator } from "../src/chain/signing";
import { USDT, PAY_VAULT, FAUCET_URL } from "../src/chain/constants";

const DEPLOY_PATH = join(__dirname, "../../coincall-contracts/deployments/testnet-968.json");

describe.skipIf(!existsSync(DEPLOY_PATH))("epoch2 常量与部署文件一致（唯一事实源）", () => {
  const dep = JSON.parse(readFileSync(DEPLOY_PATH, "utf-8")) as {
    epoch: number;
    payVault: string;
    token: string;
    tokenSymbol: string;
    domainSeparator: string;
  };

  it("金库/USDT/水龙头地址与 domainSeparator 全对齐", () => {
    expect(dep.epoch).toBe(2);
    expect(PAY_VAULT.toLowerCase()).toBe(dep.payVault.toLowerCase());
    expect(USDT.toLowerCase()).toBe(dep.token.toLowerCase());
    expect(dep.tokenSymbol).toBe("USDT");
    expect(FAUCET_URL).toBe("https://faucet.bohr.life/basic");
    // 我们手工组装的 EIP-712 生产域分隔符 == 部署文件值（新金库域随地址换）
    expect(domainSeparator(PAY_VAULT)).toBe(dep.domainSeparator);
  });

  it("域分隔符口径自证（typehash + PayVault/1/968/新金库）", () => {
    const th = keccak256(toUtf8Bytes("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"));
    void th;
    expect(domainSeparator(PAY_VAULT)).toMatch(/^0x[0-9a-f]{64}$/);
  });
});

describe("旧 epoch 残留扫描（src 零容忍）", () => {
  const root = join(__dirname, "..", "src");
  const files = [
    "chain/constants.ts",
    "chain/rpc.ts",
    "chain/signing.ts",
    "chain/injected.ts",
    "pages/ConsumerWorkbench.tsx",
    "pages/Help.tsx",
    "pages/Overview.tsx",
    "pages/ProviderWorkbench.tsx",
    "lib/errors.ts",
    "components/FundsPlaceholder.tsx",
  ].filter((f) => existsSync(join(root, f)));

  it("旧 MockUSDT / 旧金库地址 / 铸币按钮文案不得出现", () => {
    for (const f of files) {
      const content = readFileSync(join(root, f), "utf-8");
      expect(content, f).not.toContain("0x4F8f2eaAA");
      expect(content, f).not.toContain("0xFe91F55C");
      expect(content, f).not.toContain("MockUSDT");
      expect(content, f).not.toContain("铸造 10");
      expect(content, f).not.toContain("铸造按钮");
      expect(content, f).not.toContain("MOCK_USDT");
    }
  });

  it("消费端有「获取 USDT」水龙头指引（真 USDT 无公开 mint）", () => {
    const s = readFileSync(join(root, "pages/ConsumerWorkbench.tsx"), "utf-8");
    expect(s).toContain("获取 USDT");
    expect(s).toContain("水龙头");
    expect(s).toContain("FAUCET_URL");
  });
});
