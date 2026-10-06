/**
 * 「注册新 Agent 身份」内置闭环（Provider 工作台第 1 步展开区）：
 * 发起注册（8010 服务端出资账户代发，dry_run=false 真实上链，无需浏览器签名）→
 * pending（交易广播/等待上链，tx 外链）→ 2s×30 轮询 register-result →
 * 成功自动把新 agentId 回填上方输入框；60s 未上链给超时友好态 + 手动查询入口。
 */
import { useEffect, useRef, useState } from "react";
import { botChainApi } from "../api/gateway";
import { ErrorBox, InfoBox, TxLink, WarnBox } from "./ui";

const DEFAULT_AGENT_URI = "https://coincall-bot-chain-api.local/agents/console";

export interface IdentityRegisterProps {
  onRegistered: (agentId: number, owner: string | null, agentWallet: string | null) => void;
  /** 轮询间隔与次数（默认 2s×30=60s；测试可注入小值） */
  pollMs?: number;
  maxAttempts?: number;
}

type Phase = "idle" | "submitting" | "polling" | "done" | "timeout" | "error";

export function IdentityRegister({ onRegistered, pollMs = 2_000, maxAttempts = 30 }: IdentityRegisterProps) {
  const [agentUri, setAgentUri] = useState(DEFAULT_AGENT_URI);
  const [phase, setPhase] = useState<Phase>("idle");
  const [txHash, setTxHash] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [minted, setMinted] = useState<{ agentId: number; owner: string | null; agentWallet: string | null } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [manualHash, setManualHash] = useState("");
  const [manualMsg, setManualMsg] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const settle = (found: Awaited<ReturnType<typeof botChainApi.registerResult>>) => {
    if (!found.found || found.agent_ids.length === 0) return false;
    const agentId = found.agent_ids[0]!;
    setMinted({ agentId, owner: found.owner ?? null, agentWallet: found.agent_wallet ?? null });
    setPhase("done");
    onRegistered(agentId, found.owner ?? null, found.agent_wallet ?? null);
    return true;
  };

  const poll = (hash: string, n: number) => {
    if (!aliveRef.current) return;
    botChainApi
      .registerResult(hash)
      .then((r) => {
        if (!aliveRef.current) return;
        if (settle(r)) return;
        if (n + 1 >= maxAttempts) {
          setPhase("timeout");
          return;
        }
        setAttempt(n + 1);
        timerRef.current = setTimeout(() => poll(hash, n + 1), pollMs);
      })
      .catch((e) => {
        if (!aliveRef.current) return;
        setError(e);
        setPhase("error");
      });
  };

  const submit = async () => {
    setPhase("submitting");
    setError(null);
    setMinted(null);
    setAttempt(0);
    try {
      const receipt = await botChainApi.registerIdentity(agentUri.trim() || DEFAULT_AGENT_URI);
      setTxHash(receipt.tx_hash);
      setPhase("polling");
      timerRef.current = setTimeout(() => poll(receipt.tx_hash, 0), pollMs);
    } catch (e) {
      setError(e);
      setPhase("error");
    }
  };

  const manualQuery = async () => {
    setError(null);
    setManualMsg(null);
    try {
      const r = await botChainApi.registerResult(manualHash.trim());
      if (!settle(r)) {
        setPhase("timeout"); // 保持手动查询入口
        setManualMsg(`该交易仍${r.found ? "未解析到铸造事件（可能不是注册交易）" : "未上链"}。稍后再查，或在 scan.bohr.life 搜该哈希确认。`);
      }
    } catch (e) {
      setError(e);
      setPhase("error");
    }
  };

  return (
    <details style={{ marginTop: 10 }}>
      <summary className="dim" style={{ cursor: "pointer", fontSize: 13 }}>
        没有身份？↓ 注册一个
      </summary>
      <div style={{ marginTop: 10, padding: 14, border: "1px dashed var(--border-strong)", borderRadius: 8, background: "var(--surface-2)" }}>
        <div className="field">
          <label>agent_uri（身份元数据 URI，仅测试网）</label>
          <input type="text" value={agentUri} onChange={(e) => setAgentUri(e.target.value)} placeholder={DEFAULT_AGENT_URI} />
        </div>
        <InfoBox>
          注册由平台链上服务代发（消耗测试网 gas），身份 owner 为平台代管账户；无需你的钱包签名。
          接口默认 dry_run 仅预览，点「发起注册」即以 dry_run=false 真实上链（ERC-8004 铸造，不可逆）。
        </InfoBox>
        <div className="btn-row">
          <button className="btn" disabled={phase === "submitting" || phase === "polling"} onClick={submit}>
            {phase === "submitting" ? "注册交易广播中…" : "发起注册"}
          </button>
          {phase === "polling" && (
            <span className="dim">
              <span className="spin" style={{ width: 12, height: 12, borderWidth: 2 }} /> 等待上链（2s 轮询，第 {attempt + 1}/{maxAttempts} 次）…
            </span>
          )}
        </div>

        {txHash && (
          <div style={{ fontSize: 13, marginTop: 8 }}>
            注册交易：<TxLink hash={txHash} />
          </div>
        )}
        {minted && (
          <div className="alert ok" style={{ marginTop: 8 }}>
            ✓ 身份 <b>#{minted.agentId}</b> 已铸造{minted.owner ? <>，owner=<span className="mono">{minted.owner.slice(0, 10)}…</span></> : null}
            {minted.agentWallet ? <>，当前收款钱包 <span className="mono">{minted.agentWallet.slice(0, 10)}…</span></> : null}
            ，已自动填入上方 Agent ID，可直接「登记」继续向导。
          </div>
        )}
        {phase === "timeout" && !minted && (
          <div style={{ marginTop: 8 }}>
            <WarnBox>
              {maxAttempts * (pollMs / 1000)}s 内未查到铸造回执（链上偶发延迟）。可稍后在下方粘贴交易哈希手动查询，或到 scan.bohr.life 搜该哈希确认。
            </WarnBox>
            <div className="flex" style={{ marginTop: 8 }}>
              <input
                type="text"
                style={{ width: 380 }}
                placeholder="粘贴注册交易哈希 0x…"
                value={manualHash}
                onChange={(e) => setManualHash(e.target.value)}
              />
              <button className="btn secondary" disabled={!/^0x[0-9a-fA-F]{64}$/.test(manualHash.trim())} onClick={manualQuery}>
                查询铸造结果
              </button>
            </div>
            {manualMsg && <WarnBox>{manualMsg}</WarnBox>}
          </div>
        )}
        {phase === "error" && error != null && <ErrorBox error={error} />}
      </div>
    </details>
  );
}
