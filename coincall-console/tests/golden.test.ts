/**
 * T25 黄金向量测试（第四份独立实现）：
 * 读取 ../coincall-contracts/vectors/eip712_golden.json（缺失则整体 skip），
 * 用 anvil#0 私钥对每个 case 的 message 走「本仓唯一签名代码路径」签名，
 * 断言 domainSeparator / structHash / digest / v / r / s 与向量逐字段一致；
 * 并交叉验证 ethers signTypedData 独立路径与手工 digest 路径一致，
 * 以及 X-PAYMENT 组装函数确实复用同一 digest 代码路径。
 */
import { describe, expect, it } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { Wallet, verifyTypedData } from "ethers";
import {
  AUTHORIZATION_TYPEHASH,
  authorizationStructHash,
  buildPaymentHeader,
  decodePaymentHeader,
  domainSeparator,
  eip712Digest,
  signAuthorization,
  type Authorization,
} from "../src/chain/signing";

const VECTOR_PATH = join(__dirname, "../../coincall-contracts/vectors/eip712_golden.json");
const ANVIL0_PK = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80";

interface VectorCase {
  description: string;
  message: { from: string; to: string; value: number; validAfter: number; validBefore: number; nonce: string };
  address: string;
  domainSeparator: string;
  structHash: string;
  digest: string;
  signature: { v: number; r: string; s: string };
}

interface VectorFile {
  chain_id: number;
  domain: { name: string; version: string; chainId: number; verifyingContract: string };
  struct_type: string;
  cases: VectorCase[];
}

const exists = existsSync(VECTOR_PATH);
const vectors: VectorFile | null = exists ? (JSON.parse(readFileSync(VECTOR_PATH, "utf-8")) as VectorFile) : null;

describe.skipIf(!exists || !vectors)("EIP-712 黄金向量（eip712_golden.json）", () => {
  const wallet = new Wallet(ANVIL0_PK);

  it("向量文件结构自洽：struct_type 与本实现 typehash 声明一致", () => {
    const canonical = "Authorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)";
    expect(vectors!.struct_type).toBe(canonical);
    expect(AUTHORIZATION_TYPEHASH).toMatchInlineSnapshot(
      `"0x9f7910038774c0ca3d18d10ee68476fb7c3b7af50e274714ff0371ab766bb961"`
    );
    // 向量里的 structHash 字段即该 typehash 的产物口径，逐 case 再验
  });

  for (const c of vectors!.cases) {
    it(`case「${c.description}」：digest 组装 + 确定性签名 v/r/s 逐字段一致`, async () => {
      const auth: Authorization = {
        from: c.message.from,
        to: c.message.to,
        value: BigInt(c.message.value),
        validAfter: BigInt(c.message.validAfter),
        validBefore: BigInt(c.message.validBefore),
        nonce: c.message.nonce,
      };
      const vc = vectors!.domain.verifyingContract;
      const chainId = vectors!.domain.chainId;

      // ① 自算 domainSeparator（向量的 domain 是 0x…dEaD，非生产 PayVault——按向量）
      expect(domainSeparator(vc, chainId)).toBe(c.domainSeparator);
      // ② 自算 structHash
      expect(authorizationStructHash(auth)).toBe(c.structHash);
      // ③ 自算 digest（\x19\x01 口径）
      expect(eip712Digest(auth, vc, chainId)).toBe(c.digest);
      // ④ 唯一签名路径（RFC6979 确定性）：v/r/s 逐字段
      const sig = signAuthorization(wallet, auth, vc, chainId);
      expect(sig.v).toBe(c.signature.v);
      expect(sig.r).toBe(c.signature.r);
      expect(sig.s).toBe(c.signature.s);
      // ⑤ 签名可恢复出 anvil#0 地址（自洽）
      expect(wallet.address.toLowerCase()).toBe(c.address.toLowerCase());
      // ⑥ 交叉验证：ethers signTypedData 独立路径得到同一签名（两条路径都指向向量）
      const etherSig = await wallet.signTypedData(
        { name: vectors!.domain.name, version: vectors!.domain.version, chainId, verifyingContract: vc },
        { Authorization: [
          { name: "from", type: "address" },
          { name: "to", type: "address" },
          { name: "value", type: "uint256" },
          { name: "validAfter", type: "uint256" },
          { name: "validBefore", type: "uint256" },
          { name: "nonce", type: "bytes32" },
        ] },
        { ...c.message, value: BigInt(c.message.value), validAfter: BigInt(c.message.validAfter), validBefore: BigInt(c.message.validBefore) }
      );
      expect("0x" + etherSig.slice(2, 66)).toBe(c.signature.r);
      expect("0x" + etherSig.slice(66, 130)).toBe(c.signature.s);
      expect(parseInt(etherSig.slice(130, 132), 16)).toBe(c.signature.v);
      // ⑦ verifyTypedData（ecrecover 等价）也认可
      expect(
        verifyTypedData(
          { name: vectors!.domain.name, version: vectors!.domain.version, chainId, verifyingContract: vc },
          { Authorization: [
            { name: "from", type: "address" },
            { name: "to", type: "address" },
            { name: "value", type: "uint256" },
            { name: "validAfter", type: "uint256" },
            { name: "validBefore", type: "uint256" },
            { name: "nonce", type: "bytes32" },
          ] },
          { ...c.message, value: BigInt(c.message.value), validAfter: BigInt(c.message.validAfter), validBefore: BigInt(c.message.validBefore) },
          etherSig
        )
      ).toBe(wallet.address);
    });
  }

  it("X-PAYMENT 组装走同一 digest 代码路径（canonical case 的 v/r/s 进头）", () => {
    const c = vectors!.cases[0]!;
    const auth: Authorization = {
      from: c.message.from,
      to: c.message.to,
      value: BigInt(c.message.value),
      validAfter: BigInt(c.message.validAfter),
      validBefore: BigInt(c.message.validBefore),
      nonce: c.message.nonce,
    };
    const vc = vectors!.domain.verifyingContract;
    const sig = signAuthorization(wallet, auth, vc, vectors!.domain.chainId); // ← 与上面同一函数
    const header = buildPaymentHeader(auth, sig);
    const decoded = decodePaymentHeader(header);
    // 字段别名与网关 pydantic（extra=forbid）对齐
    expect(Object.keys(decoded).sort()).toEqual(["from", "nonce", "r", "s", "to", "v", "validAfter", "validBefore", "value"]);
    expect(decoded["v"]).toBe(c.signature.v);
    expect(decoded["r"]).toBe(c.signature.r);
    expect(decoded["s"]).toBe(c.signature.s);
    expect(decoded["value"]).toBe(String(c.message.value)); // 十进制字符串
    expect(decoded["validBefore"]).toBe(c.message.validBefore);
    expect(decoded["nonce"]).toBe(c.message.nonce.toLowerCase());
    // base64 可逆
    expect(JSON.parse(Buffer.from(header, "base64").toString("utf-8"))["from"]).toBe(c.message.from);
  });

  it("生产域（PayVault@968）与向量域 domainSeparator 不同且稳定（防串域）", () => {
    const prod = "0xa6E82Fd6648F9Ea8f695c37Edf89f2E5FDb89ff0"; // epoch2 金库（deployments/testnet-968.json）
    const ds = domainSeparator(prod, 968);
    expect(ds).not.toBe(vectors!.cases[0]!.domainSeparator); // 向量用 dEaD 合约
    expect(ds).toMatchInlineSnapshot(`"0x00ae87cf4248afb7385a6ec05d3c4b294616dec3c0f44effecd64acb06c2d8e6"`);
  });
});
