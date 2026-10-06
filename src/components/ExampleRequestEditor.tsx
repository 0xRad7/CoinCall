/** 示例请求区（method 联动）：GET=参数键值行（类型自动识别），POST=示例 JSON textarea（带模板插入）。 */
import { detectParamType, type ExampleParam } from "../lib/schema-infer";

export function ExampleRequestEditor({
  method,
  params,
  onParamsChange,
  json,
  onJsonChange,
}: {
  method: "GET" | "POST";
  params: ExampleParam[];
  onParamsChange: (p: ExampleParam[]) => void;
  json: string;
  onJsonChange: (s: string) => void;
}) {
  if (method === "GET") {
    return (
      <div>
        <div className="dim" style={{ marginBottom: 6 }}>
          示例 query 参数（探测与生成 input_schema 用；类型按值自动识别）：
        </div>
        {params.map((p, i) => (
          <div key={i} className="flex" style={{ marginBottom: 6 }}>
            <input
              type="text"
              style={{ width: 180 }}
              placeholder="参数名，如 tag"
              value={p.name}
              onChange={(e) => onParamsChange(params.map((x, idx) => (idx === i ? { ...x, name: e.target.value } : x)))}
              aria-label={`参数名 ${i + 1}`}
            />
            <input
              type="text"
              style={{ flex: 1, minWidth: 140 }}
              placeholder="示例值，如 hello"
              value={p.value}
              onChange={(e) => onParamsChange(params.map((x, idx) => (idx === i ? { ...x, value: e.target.value } : x)))}
              aria-label={`示例值 ${i + 1}`}
            />
            <span className="badge muted" title="按值自动识别的参数类型">
              {detectParamType(p.value.trim())}
            </span>
            <button className="btn small danger" onClick={() => onParamsChange(params.filter((_, idx) => idx !== i))}>
              删除
            </button>
          </div>
        ))}
        <button type="button" className="btn small secondary" onClick={() => onParamsChange([...params, { name: "", value: "" }])}>
          + 添加参数
        </button>
      </div>
    );
  }
  return (
    <div>
      <div className="flex" style={{ justifyContent: "space-between", marginBottom: 4 }}>
        <span className="dim">示例请求体 JSON（探测与生成 input_schema 用）：</span>
        <div className="btn-row">
          <button type="button" className="btn small secondary" onClick={() => onJsonChange('{\n  "text": "hello"\n}')}>
            插入模板
          </button>
        </div>
      </div>
      <textarea
        className="code"
        rows={4}
        spellCheck={false}
        value={json}
        onChange={(e) => onJsonChange(e.target.value)}
        aria-label="示例请求体 JSON"
      />
    </div>
  );
}
