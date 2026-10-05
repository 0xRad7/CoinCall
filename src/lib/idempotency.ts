/** 幂等键：service_id + 规范化参数的 SHA-256（浏览器/Node 双端可用）。 */
export async function hashSha256HexStringish(input: string): Promise<string> {
  const bytes = new TextEncoder().encode(input);
  if (typeof crypto !== "undefined" && crypto.subtle) {
    const buf = await crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(buf))
      .map((b) => b.toString(16).padStart(2, "0"))
      .join("");
  }
  // Node（vitest）兜底
  const { createHash } = await import("node:crypto");
  return createHash("sha256").update(bytes).digest("hex");
}
