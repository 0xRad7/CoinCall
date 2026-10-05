/** 关键表单校验测试：金额联动、schema 简单/复杂分派、签名窗口。 */
import { describe, expect, it } from "vitest";
import { isSimpleSchema } from "../src/components/SchemaForm";
import { toRaw } from "../src/chain/constants";
import { buildCallAuthorization, AUTH_WINDOW_S } from "../src/chain/signing";

describe("SchemaForm 分派逻辑（简单 → 输入框；复杂 → JSON 编辑器）", () => {
  it("svc_translate 型 schema → 简单表单", () => {
    expect(
      isSimpleSchema({ type: "object", properties: { text: { type: "string" } }, required: ["text"] })
    ).toBe(true);
  });
  it("svc_contract_scan 型（integer + min/max）→ 简单表单", () => {
    expect(
      isSimpleSchema({ type: "object", properties: { window_blocks: { type: "integer", minimum: 1, maximum: 5000 } } })
    ).toBe(true);
  });
  it("嵌套对象 / 数组 → 退化为 JSON 编辑器", () => {
    expect(isSimpleSchema({ type: "object", properties: { nested: { type: "object" } } })).toBe(false);
    expect(isSimpleSchema({ type: "object", properties: { list: { type: "array" } } })).toBe(false);
  });
  it("空 properties（svc_chain_report 型：任意 JSON）→ JSON 编辑器", () => {
    expect(isSimpleSchema({ type: "object" })).toBe(false);
    expect(isSimpleSchema({ type: "object", properties: {} })).toBe(false);
  });
});

describe("发布服务表单：定价联动", () => {
  it("amount → amount_raw（权威值）", () => {
    expect(toRaw("0.01").toString()).toBe("10000");
    expect(toRaw("1").toString()).toBe("1000000");
  });
  it("7 位小数被拦（提示精度）", () => {
    expect(() => toRaw("0.0123456")).toThrow(/精度/);
  });
});

describe("试用调用授权窗口", () => {
  it("valid_before = now + 600s（02 §5a）", () => {
    const a = buildCallAuthorization("0x70997970C51812dc3A010C7d01b50e0d17dc79C8", "0xFe91F55C0e7Ccbc4A6619C67544Ab453cf79C471", 20000n, 1234);
    expect(Number(a.validBefore - a.validAfter)).toBe(AUTH_WINDOW_S);
  });
});
