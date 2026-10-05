/**
 * EIP-712 支付授权签名（PayVault Authorization 六元组）。
 *
 * 冻结契约：digest 手工编码（与 coincall-sdk / PayVault.sol / 网关验签侧四方一致），
 * 基准向量 `../coincall-contracts/vectors/eip712_golden.json`（vitest 逐字段比对）。
 * 本模块是唯一的签名代码路径：试用调用（X-PAYMENT）与黄金向量测试共用同一函数。
 */
import { Wallet, concat, toUtf8Bytes, keccak256, getAddress, randomBytes, hexlify, zeroPadValue, assertArgument } from "ethers";

const qtyHex = (v: bigint | number) => {
  const h = BigInt(v).toString(16);
  return "0x" + (h.length % 2 ? "0" + h : h);
};
const pad32 = (v: bigint | number | string) => zeroPadValue(typeof v === "bigint" || typeof v === "number" ? qtyHex(v) : v, 32);

export const DOMAIN_NAME = "PayVault";
export const DOMAIN_VERSION = "1";
export const CHAIN_ID = 968;
export const AUTH_WINDOW_S = 600;

export const DOMAIN_TYPEHASH = keccak256(
  toUtf8Bytes("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")
);
export const AUTHORIZATION_TYPEHASH = keccak256(
  toUtf8Bytes(
    "Authorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)"
  )
);

/** Authorization 六元组（value 为最小单位整数，字符串用十进制/0x-hex 表示）。 */
export interface Authorization {
  from: string;
  to: string;
  value: bigint;
  validAfter: bigint;
  validBefore: bigint;
  /** 0x 前缀 32 字节 hex */
  nonce: string;
}

export interface PaymentSignature {
  /** 27 | 28 */
  v: number;
  /** 0x + 32 字节 hex */
  r: string;
  s: string;
}

export function domainSeparator(verifyingContract: string, chainId: bigint | number = CHAIN_ID): string {
  // abi.encode 语义：所有静态类型左补齐到 32 字节（黄金向量锁定 domainSeparator 口径）
  return keccak256(
    concat([
      DOMAIN_TYPEHASH,
      keccak256(toUtf8Bytes(DOMAIN_NAME)),
      keccak256(toUtf8Bytes(DOMAIN_VERSION)),
      pad32(chainId),
      zeroPadValue(verifyingContract, 32),
    ])
  );
}

export function authorizationStructHash(auth: Authorization): string {
  assertArgument(/^0x[0-9a-fA-F]{64}$/.test(auth.nonce), "nonce 必须为 0x + 32 字节 hex", "nonce", auth.nonce);
  return keccak256(
    concat([
      AUTHORIZATION_TYPEHASH,
      zeroPadValue(auth.from, 32),
      zeroPadValue(auth.to, 32),
      pad32(auth.value),
      pad32(auth.validAfter),
      pad32(auth.validBefore),
      auth.nonce,
    ])
  );
}

/** \x19\x01 + domainSeparator + structHash（黄金向量锁定的口径）。 */
export function eip712Digest(auth: Authorization, verifyingContract: string, chainId: bigint | number = CHAIN_ID): string {
  return keccak256(concat(["0x1901", domainSeparator(verifyingContract, chainId), authorizationStructHash(auth)]));
}

/** 本地私钥对 digest 签名（RFC6979 确定性，与 Python eth_keys 同曲线同结果）。 */
export function signAuthorization(
  wallet: Wallet,
  auth: Authorization,
  verifyingContract: string,
  chainId: bigint | number = CHAIN_ID
): PaymentSignature {
  const digest = eip712Digest(auth, verifyingContract, chainId);
  const sig = wallet.signingKey.sign(digest); // { r, s, v, recovery, serialized } v=27|28
  return { v: sig.v, r: sig.r, s: sig.s };
}

/** 随机 nonce（bytes32，0x-hex）。 */
export function randomNonce(): string {
  return hexlify(randomBytes(32));
}

/** 组装一次试用调用的授权（nonce 随机、valid_before = now + AUTH_WINDOW_S）。 */
export function buildCallAuthorization(from: string, to: string, valueRaw: bigint, nowSec: number): Authorization {
  return {
    from: getAddress(from),
    to: getAddress(to),
    value: valueRaw,
    validAfter: BigInt(nowSec),
    validBefore: BigInt(nowSec + AUTH_WINDOW_S),
    nonce: randomNonce(),
  };
}

/**
 * X-PAYMENT 头：base64(JSON{ from, to, value, validAfter, validBefore, nonce, v, r, s })。
 * 字段别名与网关 pydantic（extra="forbid"）严格对齐：value 为十进制字符串。
 */
export function buildPaymentHeader(auth: Authorization, sig: PaymentSignature): string {
  const payload = {
    from: getAddress(auth.from),
    to: getAddress(auth.to),
    value: auth.value.toString(),
    validAfter: Number(auth.validAfter),
    validBefore: Number(auth.validBefore),
    nonce: auth.nonce.toLowerCase(),
    v: sig.v,
    r: sig.r.toLowerCase(),
    s: sig.s.toLowerCase(),
  };
  return btoaOrNode(JSON.stringify(payload));
}

/** 浏览器 btoa / Node Buffer 双兼容（vitest 在 node 环境跑同一代码路径）。 */
function btoaOrNode(s: string): string {
  if (typeof btoa === "function") return btoa(s);
  return Buffer.from(s, "utf-8").toString("base64");
}

/** 解码 X-PAYMENT（自检/测试用）。 */
export function decodePaymentHeader(header: string): Record<string, unknown> {
  const raw = atobOrNode(header);
  return JSON.parse(raw) as Record<string, unknown>;
}

function atobOrNode(s: string): string {
  if (typeof atob === "function") return atob(s);
  return Buffer.from(s, "base64").toString("utf-8");
}
