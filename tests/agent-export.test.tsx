// @vitest-environment jsdom
/**
 * 「给我的 Agent 接入」导出卡测试：
 * 三 Tab 渲染；安全自检清单门控（4 项全勾才可复制）；MCP JSON 结构断言
 * （含"不含私钥明文"断言——WALLET_KEY 只出现路径占位符）；GATEWAY 动态。
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AgentExportCard } from "../src/components/AgentExportCard";
import { gatewayBaseUrl } from "../src/chain/constants";

beforeEach(() => {
  cleanup();
  localStorage.clear();
  localStorage.setItem("coincall.apikey", "cck_live_test_key_123");
});
afterEach(() => cleanup());

describe("导出卡三 Tab", () => {
  it("默认 MCP Tab：mcpServers JSON 片段渲染 + uv 命令 + 五工具 env", () => {
    render(<AgentExportCard />);
    expect(screen.getByText("给我的 Agent 接入")).toBeTruthy();
    const payload = screen.getByTestId("agent-export-payload").textContent ?? "";
    const parsed = JSON.parse(payload) as {
      mcpServers: Record<string, { command: string; args: string[]; env: Record<string, string> }>;
    };
    const mcp = parsed.mcpServers["coincall-mcp"]!;
    expect(mcp.command).toBe("uv");
    expect(mcp.args).toEqual(["run", "--from", "coincall-sdk", "python", "tools/mcp_server.py"]);
    // env 断言
    expect(mcp.env["COINCALL_API_KEY"]).toBe("cck_live_test_key_123"); // 预填本机 key
    expect(mcp.env["COINCALL_GATEWAY_URL"]).toBe(gatewayBaseUrl()); // 动态 origin
    expect(mcp.env["COINCALL_WALLET_KEY"]).toContain("0600"); // 路径占位符
    expect(mcp.env["COINCALL_WALLET_KEY"]).toContain("wallet.key");
    expect(mcp.env["COINCALL_TOTAL_BUDGET_RAW"]).toBe("10000000"); // L0 五变量
    expect(mcp.env["COINCALL_ALLOWED_SERVICES"]).toBeDefined();
  });

  it("绝不含私钥明文：payload 里只有路径占位（0x64 位 hex 不出现）", () => {
    render(<AgentExportCard />);
    const payload = screen.getByTestId("agent-export-payload").textContent ?? "";
    expect(payload).not.toMatch(/0x[0-9a-fA-F]{64}/); // 无私钥
    expect(payload).not.toMatch(/"private[_ ]?key"\s*:\s*"0x/);
    // WALLET_KEY 值不含 0x 开头的长 hex
    const parsed = JSON.parse(payload);
    expect(parsed.mcpServers["coincall-mcp"].env["COINCALL_WALLET_KEY"]).not.toMatch(/^0x/);
  });

  it("SDK Tab：Python 片段含 Client + PolicyConfig.l0_default + 五工具", () => {
    render(<AgentExportCard />);
    fireEvent.click(screen.getByText("Python SDK"));
    const payload = screen.getByTestId("agent-export-payload").textContent ?? "";
    expect(payload).toContain("from coincall import Client, PolicyConfig");
    expect(payload).toContain("PolicyConfig.l0_default()");
    expect(payload).toContain("wallet_status");
    expect(payload).toContain("paid_service_call");
    expect(payload).toContain("spend_report");
    expect(payload).toContain(gatewayBaseUrl()); // gateway 动态注入
    expect(payload).toContain("wallet.key"); // 仍是路径
  });

  it("Skill Tab：安装命令 + call.py 五命令", () => {
    render(<AgentExportCard />);
    fireEvent.click(screen.getByText("Skill"));
    const payload = screen.getByTestId("agent-export-payload").textContent ?? "";
    expect(payload).toContain("~/.agents/skills/");
    expect(payload).toContain("call.py status");
    expect(payload).toContain("call.py catalog");
    expect(payload).toContain("call.py quote");
    expect(payload).toContain("call.py call");
    expect(payload).toContain("call.py report");
  });
});

describe("安全自检清单门控", () => {
  it("未全勾：复制按钮禁用（🔒）；逐项勾选后解锁 CopyButton", () => {
    render(<AgentExportCard />);
    // 初始：4 项未勾
    expect(screen.getByText(/已勾 0\/4/)).toBeTruthy();
    const locked = screen.getByRole("button", { name: /复制配置（🔒 自检未通过）/ }) as HTMLButtonElement;
    expect(locked.disabled).toBe(true);

    // 勾满 4 项
    const labels = ["私钥文件权限 0600", "预算三顶已设", "白名单已配", "明文 key 不进代码库"];
    for (const l of labels) {
      fireEvent.click(screen.getByLabelText(new RegExp(l)));
    }
    expect(screen.getByText(/✓ 自检通过/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "复制 MCP 配置" })).toBeTruthy();
  });

  it("勾选有删除线视觉反馈（完成感）", () => {
    render(<AgentExportCard />);
    const cb = screen.getByLabelText(/私钥文件权限 0600/);
    fireEvent.click(cb);
    const label = cb.closest("label")!;
    const span = [...label.querySelectorAll("span")].find((sp) => (sp as HTMLElement).style.textDecoration === "line-through");
    expect(span).toBeTruthy(); // 勾掉后有删除线
  });
});
