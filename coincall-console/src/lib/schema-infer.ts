/**
 * JSON Schema 自动推断（探测「用结果生成 Schema」的纯函数核心）。
 *
 * 决策表：
 * - string → {type:"string"}；布尔 → boolean；整数 → integer；小数 → number
 * - null → {type:"null"}（数组里出现时并入类型并集，如 [1,null] → items {type:["integer","null"]}）
 * - 数组 → 合并全部元素：纯标量元素取去重类型集合（单一 → {type:X}，混合 → {type:[X,Y,…]}，
 *   含 null 时 "null" 排最后）；对象元素则递归合并各对象的 properties；空数组 → {type:"array"}（不猜 items）
 * - 对象 → {type:"object", properties: 递归}；嵌套深度超过 4 层截断，标 description「…（超过 4 层，已截断）」
 */

export type JsonSchema = Record<string, unknown>;

export const MAX_INFER_DEPTH = 4;

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function typeUnion(a: JsonSchema, b: JsonSchema): JsonSchema {
  const ta = a["type"];
  const tb = b["type"];
  if (JSON.stringify(ta) === JSON.stringify(tb)) return a;
  const list = [...new Set([...(Array.isArray(ta) ? (ta as string[]) : [String(ta)]), ...(Array.isArray(tb) ? (tb as string[]) : [String(tb)])])];
  // "null" 排最后（nullable 语义）
  list.sort((x, y) => (x === "null" ? 1 : y === "null" ? -1 : 0));
  return { type: list };
}

/** 合并多个 object schema：同名属性递归合并，异类型并集。 */
function mergeObjectSchemas(schemas: JsonSchema[]): JsonSchema {
  const props: Record<string, JsonSchema> = {};
  const required = new Set<string>();
  for (const s of schemas) {
    const p = (s["properties"] ?? {}) as Record<string, JsonSchema>;
    for (const [k, v] of Object.entries(p)) {
      props[k] = props[k] ? typeUnion(props[k], v) : v;
    }
    for (const r of ((s["required"] ?? []) as string[])) required.add(r);
  }
  const out: JsonSchema = { type: "object", properties: props };
  if (required.size > 0) out["required"] = [...required];
  return out;
}

/** 数组元素合并：对象元素 → 合并对象 schema；否则标量类型并集。 */
function mergeElementSchemas(elems: unknown[], depth: number): JsonSchema {
  const objectEls = elems.filter(isPlainObject);
  if (objectEls.length > 0) {
    return mergeObjectSchemas(objectEls.map((e) => inferSchema(e, depth)));
  }
  const hasNull = elems.some((e) => e === null);
  const scalarTypes = [...new Set(elems.filter((e) => e !== null).map((e) => String(inferSchema(e, depth)["type"])))];
  if (scalarTypes.length === 0) return { type: "null" };
  if (scalarTypes.length === 1 && !hasNull) return { type: scalarTypes[0]! };
  const list = [...scalarTypes];
  if (hasNull) list.push("null");
  return { type: list };
}

export function inferSchema(value: unknown, depth = 0): JsonSchema {
  if (typeof value === "string") return { type: "string" };
  if (typeof value === "boolean") return { type: "boolean" };
  if (typeof value === "number") return Number.isInteger(value) ? { type: "integer" } : { type: "number" };
  if (value === null) return { type: "null" };
  if (Array.isArray(value)) {
    if (value.length === 0) return { type: "array" };
    return { type: "array", items: mergeElementSchemas(value, depth) };
  }
  if (isPlainObject(value)) {
    if (depth >= MAX_INFER_DEPTH) return { type: "object", description: "…（超过 4 层，已截断）" };
    const props: Record<string, JsonSchema> = {};
    for (const [k, v] of Object.entries(value)) props[k] = inferSchema(v, depth + 1);
    return { type: "object", properties: props };
  }
  return { type: "string" };
}

// ---- 示例请求区 → input_schema ----

export interface ExampleParam {
  name: string;
  value: string;
}

/** 参数值类型自动识别（GET query 值最终都是字符串，仅用于 schema 类型）。 */
export function detectParamType(v: string): "string" | "integer" | "number" | "boolean" {
  if (v === "true" || v === "false") return "boolean";
  if (/^-?\d+$/.test(v)) return "integer";
  if (/^-?\d+\.\d+$/.test(v)) return "number";
  return "string";
}

/** GET：参数键值行 → input_schema（每个参数一个属性，required 全量）。 */
export function inputSchemaFromParams(params: ExampleParam[]): JsonSchema {
  const properties: Record<string, JsonSchema> = {};
  const required: string[] = [];
  for (const p of params) {
    const name = p.name.trim();
    if (!name) continue;
    properties[name] = { type: detectParamType(p.value.trim()) };
    required.push(name);
  }
  return { type: "object", properties, ...(required.length > 0 ? { required } : {}) };
}

/** POST：示例 JSON → input_schema（递归推断）。 */
export function inputSchemaFromExampleJson(text: string): JsonSchema | null {
  try {
    return inferSchema(JSON.parse(text));
  } catch {
    return null;
  }
}

/** 探测响应体 → output_schema（body 可能已是对象；字符串则尝试 JSON.parse，否则按 string）。 */
export function outputSchemaFromProbeBody(body: unknown): JsonSchema {
  if (typeof body === "string") {
    const trimmed = body.trim();
    if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
      try {
        return inferSchema(JSON.parse(trimmed));
      } catch {
        return { type: "string" };
      }
    }
    return { type: "string" };
  }
  return inferSchema(body);
}

/** input_schema 是否含嵌套对象属性（GET 模式即时预警用）。 */
export function schemaHasNestedObjects(schemaText: string): boolean {
  try {
    const parsed = JSON.parse(schemaText) as { type?: string; properties?: Record<string, { type?: string | string[] }> };
    if (parsed?.type !== "object" || !parsed.properties) return false;
    return Object.values(parsed.properties).some((p) => p?.type === "object" || (Array.isArray(p?.type) && (p.type as string[]).includes("object")));
  } catch {
    return false;
  }
}
