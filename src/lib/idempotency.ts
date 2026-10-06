/** 幂等键：service_id + 规范化参数的 SHA-256。
 * 用 ethers 的 sha256（纯 JS）——不依赖 crypto.subtle：
 * 局域网 http 访问（非安全上下文）下 subtle 为 undefined，node:crypto 桩会炸。 */
import { sha256, toUtf8Bytes } from "ethers";

export async function hashSha256HexStringish(input: string): Promise<string> {
  return sha256(toUtf8Bytes(input)).slice(2);  // 去 0x 前缀，保持 64 位 hex 契约
}
