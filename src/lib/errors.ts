/** 服务端错误 → 人话 + 下一步动作。UX 核心：不裸抛 JSON。 */
import { ApiError } from "../api/client";
import type { Gateway402Challenge } from "../api/gateway";

export interface HumanError {
  title: string;
  hint: string; // 下一步动作
  fieldErrors?: Record<string, string>;
  raw?: unknown;
  traceId?: string;
  status?: number;
}

const FIELD_LABELS: Record<string, string> = {
  agent_id: "Agent ID",
  display_name: "展示名称",
  service_id: "服务 ID",
  name: "服务名称",
  description: "描述",
  version: "版本号",
  provider: "Provider 信息",
  endpoint: "端点",
  url: "端点 URL",
  timeout_ms: "超时(ms)",
  pricing: "定价",
  amount: "定价(人类可读)",
  amount_raw: "定价(最小单位)",
  token: "计价代币",
  model: "计费模式",
  input_schema: "输入 Schema",
  output_schema: "输出 Schema",
  consumer_wallet: "钱包地址",
};

export function labelField(f: string): string {
  return FIELD_LABELS[f] ?? f;
}

export function humanizeError(e: unknown): HumanError {
  if (e instanceof ApiError) {
    const base: HumanError = {
      title: e.detail || e.error,
      hint: defaultHint(e.status, e.error, e.code),
      fieldErrors: e.fieldErrors,
      raw: e.raw,
      traceId: e.traceId,
      status: e.status,
    };
    // 业务语义码 → 人话
    if (e.code === "identity_not_found" || /identity_not_found|未注册|not.*registered/i.test(e.detail + e.error)) {
      base.title = "链上找不到这个 Agent 身份";
      base.hint = "该 agent_id 尚未在 ERC-8004 注册表铸造身份。请先到 bot-chain-api 完成身份注册（POST /api/v1/agent-identity/register），或核对 token_id 是否填对。";
    } else if (/agent_wallet|wallet.*not.*bind|收款钱包/i.test(e.detail + e.error)) {
      base.title = "该身份还没有绑定收款钱包";
      base.hint = "发布服务前须先把 agentWallet 绑定到链上身份。请到本页第 4 步「收款钱包绑定」完成绑定后再发布。";
    } else if (e.code === "service_exists|duplicate" || /already exists|已存在/i.test(e.detail + e.error)) {
      base.title = "服务 ID 已被占用";
      base.hint = "service_id 全局唯一。换一个 ID，或在「我的服务」里直接改价/改状态（重新提交 manifest）。";
    } else if (e.status === 422 && Object.keys(e.fieldErrors).length > 0) {
      base.title = "提交内容有字段不合规";
      base.hint = "已把服务端校验错误定位到具体表单项（红字标注），修正后重新提交。";
    }
    return base;
  }
  if (e instanceof Error) {
    // ethers 常见
    if (/invalid private key|private key/i.test(e.message)) {
      return { title: "私钥格式不正确", hint: "应为 0x 开头 64 位十六进制字符。", raw: e.message };
    }
    if (/could not detect network|failed to fetch|NetworkError/i.test(e.message)) {
      return { title: "连不上链节点（rpc.bohr.life）", hint: "检查本机网络（RPC 需要公网访问；npm 代理不影响浏览器直连）。", raw: e.message };
    }
    if (/insufficient funds|余额不足/i.test(e.message)) {
      return { title: "钱包原生代币(gas)不足", hint: "链上交易需少量 BOHR 作 gas（恒 20 gwei）。请给该地址充值测试网代币后重试。", raw: e.message };
    }
    if (/replacement transaction|nonce/i.test(e.message) && /too low/i.test(e.message)) {
      return { title: "交易 nonce 冲突", hint: "上一笔交易还在打包。等几秒（或刷新余额）后重试。", raw: e.message };
    }
    return { title: e.message, hint: "若持续出现，展开「详细信息」查看原始返回并向平台反馈。", raw: e.message };
  }
  return { title: "未知错误", hint: "请重试或查看详细信息。", raw: e };
}

function defaultHint(status: number, error: string, code: string): string {
  if (status === 0) return "确认后端服务已启动、控制台经 npm run dev（5173 端口同源代理）访问。";
  if (status === 401) return "API key 无效或已吊销。到「消费端工作台 → API key」重新签发。";
  if (status === 404) return "服务不存在或已下架(paused)。刷新目录确认 service_id 与状态。";
  if (status === 409) return "幂等键冲突：同一 Idempotency-Key 换了不同参数。稍后重试或换个参数。";
  if (status === 502) return "Provider 端点本次失败（provider_failed 不会扣费）。可直接重试。";
  if (status >= 500) return "服务端内部错误。稍后重试；持续出现请反馈 trace_id。";
  if (code) return `错误码 ${code}：请根据「详细信息」定位，或查看帮助页错误码表。`;
  return `HTTP ${status}${error ? ` / ${error}` : ""}。展开「详细信息」查看原始响应。`;
}

/** 402 质询 → 动作化提示。 */
export function humanizeChallenge(c: Gateway402Challenge): { title: string; hint: string; action: "approve" | "apikey" | "none"; amount?: string } {
  const price = c.pricing;
  switch (c.code) {
    case "missing_api_key":
      return { title: "缺少 API key", hint: "付费服务需要 X-Api-Key。到「API key」一步用当前钱包签发后自动填入。", action: "apikey" };
    case "insufficient_balance":
      return {
        title: `钱包余额不足（需 ${price.amount} USDT）`,
        hint: "到「资金面板」铸造测试 MockUSDT 后重试。",
        action: "approve",
        amount: price.amount,
      };
    case "insufficient_allowance":
      return {
        title: `对 PayVault 的授权不足（本次需 ${price.amount} USDT）`,
        hint: `到「资金面板 → 授权滑条」给 PayVault 授权至少 ${price.amount} USDT 后重试。`,
        action: "approve",
        amount: price.amount,
      };
    default:
      return { title: c.detail || "需要付费", hint: "按指引完成支付前置条件后重试。", action: "none", amount: price?.amount };
  }
}
