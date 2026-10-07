/** schema 推断纯函数测试（探测「用结果生成 Schema」核心）。 */
import { describe, expect, it } from "vitest";
import {
  MAX_INFER_DEPTH,
  detectParamType,
  inferSchema,
  inputSchemaFromExampleJson,
  inputSchemaFromParams,
  outputSchemaFromProbeBody,
  schemaHasNestedObjects,
} from "../src/lib/schema-infer";

describe("inferSchema 标量", () => {
  it("string / boolean / integer / number / null", () => {
    expect(inferSchema("hi")).toEqual({ type: "string" });
    expect(inferSchema(true)).toEqual({ type: "boolean" });
    expect(inferSchema(42)).toEqual({ type: "integer" });
    expect(inferSchema(-7)).toEqual({ type: "integer" });
    expect(inferSchema(3.14)).toEqual({ type: "number" });
    expect(inferSchema(null)).toEqual({ type: "null" });
  });
});

describe("inferSchema 数组（元素类型合并）", () => {
  it("单一标量类型 → items {type:X}", () => {
    expect(inferSchema([1, 2, 3])).toEqual({ type: "array", items: { type: "integer" } });
    expect(inferSchema(["a", "b"])).toEqual({ type: "array", items: { type: "string" } });
  });
  it("混合标量 → 类型并集数组", () => {
    expect(inferSchema([1, "a"])).toEqual({ type: "array", items: { type: ["integer", "string"] } });
  });
  it("含 null → 并入并集且排最后（nullable 语义）", () => {
    expect(inferSchema([1, null])).toEqual({ type: "array", items: { type: ["integer", "null"] } });
    expect(inferSchema([null])).toEqual({ type: "array", items: { type: "null" } });
  });
  it("对象元素 → 合并各对象 properties", () => {
    expect(inferSchema([{ a: 1 }, { b: "x" }])).toEqual({
      type: "array",
      items: { type: "object", properties: { a: { type: "integer" }, b: { type: "string" } } },
    });
    // 同名属性异类型 → 并集
    expect(inferSchema([{ v: 1 }, { v: "s" }])).toEqual({
      type: "array",
      items: { type: "object", properties: { v: { type: ["integer", "string"] } } },
    });
  });
  it("空数组 → 不猜 items", () => {
    expect(inferSchema([])).toEqual({ type: "array" });
  });
});

describe("inferSchema 嵌套与深度截断", () => {
  it("嵌套对象递归", () => {
    expect(inferSchema({ user: { name: "a", age: 1 } })).toEqual({
      type: "object",
      properties: { user: { type: "object", properties: { name: { type: "string" }, age: { type: "integer" } } } },
    });
  });
  it("超过 4 层截断并标注", () => {
    const deep = { a: { b: { c: { d: { e: { f: 1 } } } } } }; // a=1 b=2 c=3 d=4 e=5
    const out = inferSchema(deep);
    expect(out).toEqual({
      type: "object",
      properties: {
        a: { type: "object", properties: { b: { type: "object", properties: { c: { type: "object", properties: { d: { type: "object", description: "…（超过 4 层，已截断）" } } } } } } },
      },
    });
    expect(MAX_INFER_DEPTH).toBe(4);
  });
});

describe("GET 参数行 → input_schema", () => {
  it("类型自动识别 + required 全量", () => {
    expect(
      inputSchemaFromParams([
        { name: "tag", value: "a" },
        { name: "limit", value: "2" },
        { name: "ratio", value: "0.5" },
        { name: "verbose", value: "true" },
      ])
    ).toEqual({
      type: "object",
      properties: {
        tag: { type: "string" },
        limit: { type: "integer" },
        ratio: { type: "number" },
        verbose: { type: "boolean" },
      },
      required: ["tag", "limit", "ratio", "verbose"],
    });
  });
  it("detectParamType 边界", () => {
    expect(detectParamType("true")).toBe("boolean");
    expect(detectParamType("-3")).toBe("integer");
    expect(detectParamType("1.5")).toBe("number");
    expect(detectParamType("1.5.5")).toBe("string");
    expect(detectParamType("")).toBe("string");
  });
});

describe("POST 示例 JSON / 探测响应体", () => {
  it("示例 JSON 递归推断；非法 JSON 返回 null", () => {
    expect(inputSchemaFromExampleJson('{"text":"hi","n":1}')).toEqual({
      type: "object",
      properties: { text: { type: "string" }, n: { type: "integer" } },
    });
    expect(inputSchemaFromExampleJson("not json")).toBeNull();
  });
  it("探测 body 已是对象 → 直接推断；字符串尝试 parse；纯文本按 string", () => {
    expect(outputSchemaFromProbeBody({ ok: true })).toEqual({ type: "object", properties: { ok: { type: "boolean" } } });
    expect(outputSchemaFromProbeBody('{"a":[1,2]}')).toEqual({ type: "object", properties: { a: { type: "array", items: { type: "integer" } } } });
    expect(outputSchemaFromProbeBody("plain text")).toEqual({ type: "string" });
    expect(outputSchemaFromProbeBody("{broken")).toEqual({ type: "string" });
  });
});

describe("schemaHasNestedObjects（GET 即时预警）", () => {
  it("扁平 schema=false；嵌套对象属性=true；type 数组含 object=true；非法 JSON=false", () => {
    expect(schemaHasNestedObjects('{"type":"object","properties":{"text":{"type":"string"}}}')).toBe(false);
    expect(schemaHasNestedObjects('{"type":"object","properties":{"cfg":{"type":"object"}}}')).toBe(true);
    expect(schemaHasNestedObjects('{"type":"object","properties":{"cfg":{"type":["object","null"]}}}')).toBe(true);
    expect(schemaHasNestedObjects("{broken")).toBe(false);
  });
});
