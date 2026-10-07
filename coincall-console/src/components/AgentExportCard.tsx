/**
 * 「给我的 Agent 接入」导出卡（design/consumer-agent-interface.md §3）：
 * 三 Tab（MCP / SDK / Skill）+ 安全自检清单门控（4 项全勾才能复制）。
 * 导出配置绝不包含私钥明文——WALLET_KEY 只出现路径占位符。
 */
import { useMemo, useState } from "react";
import { CopyButton } from "./ui";
import { gatewayBaseUrl } from "../chain/constants";

const APIKEY_STORE = "coincall.apikey";

/** MCP env：L0 五变量带推荐默认值 + 密钥三件（key 预填 / gateway 动态 / wallet 只路径）。 */
function buildMcpEnv(apiKey: string | null): Record<string, string> {
  return {
    COINCALL_API_KEY: apiKey || "cck_…（先到工作台 ③ 签发并保存）",
    COINCALL_GATEWAY_URL: gatewayBaseUrl(),
    COINCALL_WALLET_KEY: "~/.coincall/wallet.key（0600 私钥文件路径——绝不填明文）",
    // L0 五变量（推荐默认值）
    COINCALL_TOTAL_BUDGET_RAW: "10000000", // 10 USDT 总额
    COINCALL_DAILY_BUDGET_RAW: "2000000", // 2 USDT/日
    COINCALL_PER_CALL_BUDGET_RAW: "50000", // 0.05 USDT/笔
    COINCALL_ALLOWED_SERVICES: "", // 空=白名单未配（建议列出 service_id 逗号分隔）
    COINCALL_MIN_INTERVAL_S: "30", // 付费调用最小间隔
    COINCALL_MAX_CALLS_PER_HOUR: "20",
  };
}

function buildMcpJson(apiKey: string | null): string {
  const env = buildMcpEnv(apiKey);
  return JSON.stringify(
    {
      mcpServers: {
        "coincall-mcp": {
          command: "uv",
          args: ["run", "--from", "coincall-sdk", "python", "tools/mcp_server.py"],
          env,
        },
      },
    },
    null,
    2
  );
}

const SDK_SNIPPET = `from coincall import Client, PolicyConfig

client = Client(
    api_key="cck_…",
    gateway_url="{GATEWAY}",
    wallet_key_file="~/.coincall/wallet.key",  # 0600 文件路径
    policy=PolicyConfig.l0_default(),          # L0 全开：预算三顶+白名单+速率
)

# 五工具面（Agent 决策闭环）
print(client.wallet_status())                  # 自查：余额/授权/L0 现值
catalog = client.catalog()                     # 看目录
quote = client.service_quote("svc_translate")  # 看价（含预算余量）
result = client.paid_service_call(             # 付费调用（唯一花钱）
    "svc_translate", {"text": "hello world"}
)
print(client.spend_report(recent=5))            # 汇报：已花/剩余/最近 N 笔`;

const SKILL_SNIPPET = `# 安装（ZCode：同步 skills/ 到 ~/.agents/skills/；Claude：项目级 .agents/skills/）
cp -r coincall-sdk/skills/coincall-consumer ~/.agents/skills/

# call.py 五命令一览（内部走 SDK，天然带 L0 策略引擎）
uv run python ~/.agents/skills/coincall-consumer/scripts/call.py status              # 钱包健康自查
uv run python ~/.agents/skills/coincall-consumer/scripts/call.py catalog            # 列付费服务
uv run python ~/.agents/skills/coincall-consumer/scripts/call.py quote svc_x        # 单服务报价
uv run python ~/.agents/skills/coincall-consumer/scripts/call.py call svc_x '{"text":"hi"}'  # 付费调用
uv run python ~/.agents/skills/coincall-consumer/scripts/call.py report --recent 10 # 账本汇报`;

const CHECKS = [
  { id: "keyfile", label: "私钥文件权限 0600（chmod 600 ~/.coincall/wallet.key）" },
  { id: "budget", label: "预算三顶已设（总额 / 日额 / 单笔）" },
  { id: "allowlist", label: "白名单已配（COINCALL_ALLOWED_SERVICES 非空）" },
  { id: "nogit", label: "明文 key 不进代码库（.env 入 .gitignore）" },
];

export function AgentExportCard() {
  const [tab, setTab] = useState<"mcp" | "sdk" | "skill">("mcp");
  const [checks, setChecks] = useState<Record<string, boolean>>({});
  const apiKey = typeof localStorage !== "undefined" ? localStorage.getItem(APIKEY_STORE) : null;

  const mcpJson = useMemo(() => buildMcpJson(apiKey), [apiKey]);
  const sdkCode = useMemo(() => SDK_SNIPPET.replace("{GATEWAY}", gatewayBaseUrl()), []);
  const allChecked = CHECKS.every((c) => checks[c.id]);

  const payload = tab === "mcp" ? mcpJson : tab === "sdk" ? sdkCode : SKILL_SNIPPET;

  return (
    <div className="card">
      <h3>给我的 Agent 接入</h3>
      <p className="card-desc">
        已调通？把付费能力交给你的 Agent——三种形态（MCP 主路径 / Python SDK / Skill 纪律层）。配置里
        <b>绝不包含私钥明文</b>（只有路径引用）。
      </p>

      {/* 安全自检清单（全勾才能复制——劝退项做成引导完成项） */}
      <div className="card" style={{ boxShadow: "none", background: "var(--surface-2)", marginBottom: 12 }}>
        <b style={{ fontSize: 13 }}>安全自检清单（全勾后可复制）</b>
        <div style={{ marginTop: 8, display: "grid", gap: 6 }}>
          {CHECKS.map((c) => (
            <label key={c.id} className="flex" style={{ cursor: "pointer", fontSize: 13 }}>
              <input
                type="checkbox"
                checked={!!checks[c.id]}
                onChange={(e) => setChecks((prev) => ({ ...prev, [c.id]: e.target.checked }))}
                aria-label={c.label}
                style={{ width: "auto", marginRight: 8 }}
              />
              <span style={{ textDecoration: checks[c.id] ? "line-through" : "none", color: checks[c.id] ? "var(--text-3)" : "inherit" }}>
                {c.label}
              </span>
            </label>
          ))}
        </div>
        {!allChecked && <div className="dim" style={{ fontSize: 12, marginTop: 6 }}>完成上述 4 项后「复制配置」解锁——这不是形式，是 Agent 花真钱前的最低护栏。</div>}
      </div>

      {/* 三 Tab */}
      <div className="seg" style={{ marginBottom: 10 }}>
        <button className={tab === "mcp" ? "active" : ""} onClick={() => setTab("mcp")}>MCP（主路径）</button>
        <button className={tab === "sdk" ? "active" : ""} onClick={() => setTab("sdk")}>Python SDK</button>
        <button className={tab === "skill" ? "active" : ""} onClick={() => setTab("skill")}>Skill</button>
      </div>

      {tab === "mcp" && (
        <div className="dim" style={{ fontSize: 12, marginBottom: 6 }}>
          mcpServers 片段（Claude Code / ZCode / Cursor 等标准客户端直接粘贴）。env 内 L0 五变量是 Agent 花钱的本地强制护栏。
        </div>
      )}
      {tab === "sdk" && (
        <div className="dim" style={{ fontSize: 12, marginBottom: 6 }}>
          五分钟接入：pip 安装 coincall-sdk 后，Client + PolicyConfig.l0_default() 即 L0 全开。
        </div>
      )}
      {tab === "skill" && (
        <div className="dim" style={{ fontSize: 12, marginBottom: 6 }}>
          Skill 教 Agent「如何安全地」用 MCP/SDK：自查→目录→报价→超限问人→调用→汇报，付费失败不自动重试。
        </div>
      )}

      <pre className="code-box" style={{ padding: 12, borderRadius: 8, maxHeight: 360, overflow: "auto", fontSize: 12 }} data-testid="agent-export-payload">
        {payload}
      </pre>

      <div className="btn-row" style={{ marginTop: 10 }}>
        <span className={allChecked ? "badge ok" : "badge muted"}>{allChecked ? "✓ 自检通过" : `已勾 ${CHECKS.filter((c) => checks[c.id]).length}/${CHECKS.length}`}</span>
        {allChecked ? (
          <CopyButton text={payload} label={tab === "mcp" ? "复制 MCP 配置" : "复制代码"} />
        ) : (
          <button className="btn" disabled title="完成安全自检清单后解锁">复制配置（🔒 自检未通过）</button>
        )}
      </div>
    </div>
  );
}
