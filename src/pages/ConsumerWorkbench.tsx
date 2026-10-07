/**
 * 消费端工作台（核心页）：
 * 连接钱包（浏览器扩展，地址只读）→ 资金（余额/授权/可用 + mint + 授权滑条，扩展弹窗发交易）→
 * API key → 试用调用（动态表单 + 扩展弹窗 EIP-712 签名 + X-PAYMENT → 402 动作化）→ 历史 / 预算。
 * 控制台不接触任何私钥；无扩展的演示机可展开「一次性演示钱包」兜底（关页即焚）。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { hashSha256HexStringish } from "../lib/idempotency";
import { ApiError } from "../api/client";
import { coreApi } from "../api/core";
import { gatewayApi, type Gateway402Challenge } from "../api/gateway";
import { USDT, FAUCET_URL, PAY_VAULT as VAULT, SEL, fromRaw, toRaw } from "../chain/constants";
import { encodeAddrUint, fetchAllowance, fetchTokenBalance, type TxProgress } from "../chain/rpc";
import { isUserRejected } from "../chain/injected";
import { buildCallAuthorization, buildPaymentHeader } from "../chain/signing";
import { SchemaForm } from "../components/SchemaForm";
import { Badge, ConfirmDialog, CopyButton, Empty, ErrorBox, InfoBox, Spinner, SuccessBox, TxLink, WarnBox } from "../components/ui";
import { humanizeChallenge, humanizeError } from "../lib/errors";
import { addSpentRaw, appendHistory, clearHistory, loadBudgetRaw, loadHistory, loadSpentRaw, markHistoryRated, saveBudgetRaw, type CallHistoryEntry } from "../lib/storage";
import { feedbackApi } from "../api/core";
import { useAsync } from "../lib/useAsync";
import { useWallet } from "../state/WalletContext";
import { useMode } from "../state/ModeContext";
import { PageHeader, StatCard } from "../components/shell";

const APIKEY_STORE = "coincall.apikey";

export default function ConsumerWorkbench() {
  const { address } = useWallet();
  const connected = address != null;

  // 身份只能由 /welcome 选择卡设定：已连接但未选身份 → 送回选择页（禁止直达替用户选择）
  const { mode } = useMode();
  const needsChoice = connected && !mode;
  useEffect(() => {
    if (needsChoice) window.location.hash = "#/welcome";
  }, [needsChoice]);
  if (needsChoice) return null;

  return (
    <div>
      <PageHeader
        mode="consumer"
        title="消费端工作台"
        sub="从零完成一次付费调用：连接钱包 → 备好资金 → 签发 API key → 选服务 → 钱包签名授权 → 402 网关调用。签名与交易全部在你的浏览器钱包弹窗里完成，控制台不接触私钥。"
      />

      <ConnectSection />
      {connected && (
        <>
          <FundsSection />
          <ApiKeySection />
          <TrialCallSection />
          <HistorySection />
          <BudgetSection />
        </>
      )}
    </div>
  );
}

/* ============ 1. 连接钱包 ============ */
function ConnectSection() {
  const w = useWallet();

  if (w.address && w.mode) {
    return (
      <div className="card">
        <h3>① 钱包已连接</h3>
        <div className="alert ok" style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <span>
            {w.mode === "injected" ? (
              <>
                {w.walletName ?? "浏览器钱包"} <b className="mono">{w.address}</b>
              </>
            ) : (
              <>
                一次性演示钱包 <b className="mono">{w.address}</b> <Badge kind="warn">关页即焚</Badge>
              </>
            )}
          </span>
          <CopyButton text={w.address} />
          {w.mode === "injected" && w.candidates.length >= 2 && (
            <button className="btn small secondary" onClick={w.rechoose}>
              换钱包
            </button>
          )}
          {w.mode === "demo" && w.demoPrivateKey && (
            <button
              className="btn small secondary"
              onClick={() => {
                const blob = new Blob(
                  [JSON.stringify({ address: w.address, privateKey: w.demoPrivateKey, network: "BOT Chain 968 (testnet)", warning: "一次性演示钱包：仅测试网、关页即焚、勿存资金" }, null, 2)],
                  { type: "application/json" }
                );
                const a = document.createElement("a");
                a.href = URL.createObjectURL(blob);
                a.download = `coincall-demo-${w.address!.slice(0, 10)}.json`;
                a.click();
                URL.revokeObjectURL(a.href);
              }}
            >
              下载 key 文件（领 gas 用）
            </button>
          )}
          <button className="btn small danger" onClick={w.disconnect}>
            断开
          </button>
        </div>
        {w.mode === "injected" && !w.chainOk && (
          <WarnBox>
            当前钱包连的是链 {w.chainId ?? "?"}，不是 BOT Chain 测试网（968）——mint/授权/付费调用都会失败。
            <button className="btn small" style={{ marginLeft: 8 }} onClick={() => void w.ensureChain()}>
              引导钱包切链 / 添加 BOT Chain
            </button>
          </WarnBox>
        )}
        {w.mode === "demo" && <WarnBox>这是一次性演示钱包（仅测试网、关闭页面即焚毁、勿存资金）。主路径请安装 OKX / MetaMask 后「连接钱包」。</WarnBox>}
      </div>
    );
  }

  return (
    <div className="card">
      <h3>① 连接钱包</h3>
      <p className="card-desc">只读取你的钱包地址（eth_requestAccounts）；后续签名与交易都由钱包扩展弹窗确认，控制台不接触任何私钥。</p>

      {w.issue && (
        <div className="alert warn">
          <b>{w.issue.title}</b> —— {w.issue.hint}
        </div>
      )}

      <div className="btn-row">
        <button className="btn" onClick={() => void w.connect()} disabled={w.connecting}>
          {w.connecting ? <Spinner label="等待钱包确认…" /> : "连接钱包"}
        </button>
        {w.candidates.length > 0 && (
          <span className="dim">
            检测到 {w.candidates.length} 个钱包：{w.candidates.map((c) => c.name).join(" / ")}
          </span>
        )}
        {w.candidates.length === 0 && <span className="dim">未检测到浏览器钱包扩展（EIP-6963 与 legacy 槽位均无响应）。</span>}
      </div>

      {/* 兜底：无扩展演示机的一次性钱包（默认收起，与主路径强隔离） */}
      <details style={{ marginTop: 20 }}>
        <summary className="dim" style={{ cursor: "pointer", fontSize: 13 }}>
          没有浏览器钱包？
        </summary>
        <div className="demo-box" style={{ marginTop: 10 }}>
          <p className="big-warn">⚠ 仅测试网演示用 · 关页即焚 · 勿存任何资金</p>
          <p style={{ fontSize: 13, color: "var(--text-2)", margin: "0 0 10px" }}>
            创建一个一次性演示钱包（随机生成、只存 sessionStorage、关闭标签页即焚毁）。
            它与「连接浏览器钱包」主路径完全隔离，仅用于没有装扩展的演示机；用它在浏览器进程内本地完成签名（黄金向量锁定的同一条 digest 路径）。
            需要先给它充测试网 BOT 作 gas 与 USDT（可用页面内 key 文件去水龙头领）。
          </p>
          <button className="btn danger" onClick={w.createDemo}>
            创建一次性演示钱包
          </button>
        </div>
      </details>
    </div>
  );
}

/* ============ 2. 资金面板 ============ */
export function FundsSection() {
  const w = useWallet();
  const { address } = w;
  // 资金状态面板：只读三数（余额/授权/可用）。授权操作已并入试用调用①（授权→支付一条动线）。
  const [refreshTick, setRefreshTick] = useState(0);
  const refreshFunds = useCallback(() => setRefreshTick((t) => t + 1), []);

  const funds = useAsync<{ balance: bigint; allowance: bigint } | null>(
    () => (address ? Promise.all([fetchTokenBalance(address), fetchAllowance(address)]).then(([balance, allowance]) => ({ balance, allowance })) : Promise.resolve(null)),
    [address, refreshTick]
  );
  const d = funds.data;
  const available = d ? (d.allowance < d.balance ? d.allowance : d.balance) : null;

  return (
    <div className="card">
      <div className="flex" style={{ justifyContent: "space-between" }}>
        <h3 className="mb-0">② 资金状态面板（USDT · 6 位精度）</h3>
        <button className="btn small secondary" onClick={refreshFunds}>
          刷新
        </button>
      </div>
      <p className="card-desc">
        只读三数：可用额 = min(余额, 对 PayVault 的授权额)。授权与支付已合并为试用调用里的「① 授权额度 → ② 支付调用」一条动线。
      </p>

      {funds.loading && d == null ? (
        <Spinner label="eth_call 读链中…" />
      ) : funds.error != null ? (
        <ErrorBox error={funds.error} />
      ) : d ? (
        <div className="stat-grid">
          <StatCard
            k="token 余额"
            value={fromRaw(d.balance)}
            sub={`raw=${d.balance.toString()}`}
          />
          <StatCard
            k="对 PayVault 授权额"
            value={fromRaw(d.allowance)}
            sub={`raw=${d.allowance.toString()} · ${VAULT.slice(0, 10)}…`}
          />
          <StatCard
            k="可用额（可消费）"
            value={fromRaw(available ?? 0n)}
            sub={`raw={(available ?? 0n).toString()}`}
            tone={available && available > 0n ? "success" : "danger"}
          />
        </div>
      ) : null}

      <div className="card" style={{ boxShadow: "none", marginBottom: 0, padding: "12px 16px" }}>
        <div className="flex" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
          <div>
            <b>获取 USDT</b>
            <div className="dim" style={{ fontSize: 12 }}>
              计价 token 是测试网真 USDT（无公开 mint）：到{" "}
              <a href={FAUCET_URL} target="_blank" rel="noreferrer">
                测试网水龙头
              </a>{" "}
              领取到你的钱包地址，然后回来点「刷新」。
            </div>
          </div>
          <div className="btn-row">
            <CopyButton text={address ?? ""} label="复制钱包地址" />
            <a className="btn small secondary" href={FAUCET_URL} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
              去水龙头 ↗
            </a>
            <button className="btn small secondary" onClick={refreshFunds}>
              刷新余额
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ============ 3. API key（三层：本机已保存 / 粘贴旧 key / 为当前钱包签发） ============ */
export function ApiKeySection() {
  const { address } = useWallet();
  const [storedKey, setStoredKey] = useState<string | null>(() => localStorage.getItem(APIKEY_STORE));
  const [pasted, setPasted] = useState("");
  const [issued, setIssued] = useState<{ key_id: string; api_key: string } | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState<"issue" | "validate-local" | "validate-paste" | null>(null);
  const [error, setError] = useState<unknown>(null);

  // 本机 key 的服务端实检结果
  const [localCheck, setLocalCheck] = useState<{ ok: boolean; info?: { key_id: string; consumer_wallet: string; status: string } } | null>(null);
  // 粘贴 key 的实检结果
  const [pasteCheck, setPasteCheck] = useState<{ ok: boolean; info?: { key_id: string; consumer_wallet: string; status: string } } | null>(null);

  useEffect(() => {
    const h = () => setStoredKey(localStorage.getItem(APIKEY_STORE));
    window.addEventListener("coincall:apikey", h);
    return () => window.removeEventListener("coincall:apikey", h);
  }, []);

  // 名下 key 数（换机辅助信息：为什么不能找回、只能重签）
  const walletKeys = useAsync(() => (address ? coreApi.listApiKeysForWallet(address).catch(() => null) : Promise.resolve(null)), [address]);

  const mask = (k: string) => (k.length > 10 ? `${k.slice(0, 4)}…${k.slice(-4)}` : "…");
  const saveKey = (k: string) => {
    localStorage.setItem(APIKEY_STORE, k);
    window.dispatchEvent(new CustomEvent("coincall:apikey"));
  };

  const validate = async (key: string, which: "local" | "paste") => {
    setBusy(which === "local" ? "validate-local" : "validate-paste");
    setError(null);
    try {
      const info = await coreApi.validateApiKey(key.trim());
      const ok = info.status === "active";
      if (which === "local") setLocalCheck({ ok, info });
      else setPasteCheck({ ok, info });
    } catch (e) {
      if (which === "local") setLocalCheck({ ok: false });
      else setPasteCheck({ ok: false });
      setError(e);
    } finally {
      setBusy(null);
    }
  };

  const issue = async () => {
    setBusy("issue");
    setError(null);
    setSaved(false);
    try {
      const r = await coreApi.issueApiKey(address!);
      setIssued({ key_id: r.key_id, api_key: r.api_key });
      walletKeys.reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };

  const confirmSaved = () => {
    saveKey(issued?.api_key ?? "");
    setStoredKey(issued?.api_key ?? null);
    setSaved(true);
    setLocalCheck(null); // 稍后可实检
    void validate(issued?.api_key ?? "", "local");
  };

  const walletMismatch =
    localCheck?.ok && address && localCheck.info && localCheck.info.consumer_wallet.toLowerCase() !== address.toLowerCase();

  return (
    <div className="card">
      <h3>③ API key（X-Api-Key）</h3>
      <p className="card-desc">
        key 与钱包地址绑定——付费授权的签名人必须是这个地址。明文只在签发时显示一次（服务端只存 hash，不可找回）；
        换机器不用慌：从原机器复制 key 粘贴过来，或直接为当前钱包签发新 key。
      </p>

      {walletKeys.data && (
        <div className="dim" style={{ marginBottom: 10 }}>
          你的钱包名下已有 <b>{walletKeys.data.keys.length}</b> 个 key（明文只在签发时显示一次）。
        </div>
      )}

      {/* 第一层：本机已保存 */}
      {storedKey && (
        <div className="card" style={{ boxShadow: "none", background: "var(--surface-2)", marginBottom: 12 }}>
          <div className="flex" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
            <span>
              <b>本机已保存</b>：<span className="mono">{mask(storedKey)}</span>
            </span>
            <div className="btn-row">
              <button className="btn small secondary" disabled={busy != null} onClick={() => void validate(storedKey, "local")}>
                {busy === "validate-local" ? "实检中…" : "服务端实检"}
              </button>
              <button className="btn small danger" onClick={() => { localStorage.removeItem(APIKEY_STORE); window.dispatchEvent(new CustomEvent("coincall:apikey")); setStoredKey(null); setLocalCheck(null); }}>
                移除
              </button>
            </div>
          </div>
          {localCheck?.ok && (
            <div className="alert ok" style={{ marginTop: 8, marginBottom: 0 }}>
              ✓ 有效 · 绑定钱包 <span className="mono">{localCheck.info!.consumer_wallet.slice(0, 10)}…</span>
              {walletMismatch ? null : <>（=当前连接钱包，可直接支付）</>}
            </div>
          )}
          {walletMismatch && (
            <div className="alert warn" style={{ marginTop: 8, marginBottom: 0 }}>
              <b>该 key 绑定的钱包 ≠ 当前连接钱包</b>：支付时签名人不符会被网关 402——请断开后用绑定钱包连接，或为当前钱包签发新 key。
            </div>
          )}
          {localCheck && !localCheck.ok && (
            <div className="alert err" style={{ marginTop: 8, marginBottom: 0 }}>
              实检未通过（key 可能已吊销/不存在）。可刷新重试，或用下方粘贴/签发路径。
            </div>
          )}
        </div>
      )}

      {/* 第二层：粘贴已有 key */}
      <div className="field">
        <label>从原机器粘贴已有 key</label>
        <div className="flex">
          <input
            type="password"
            style={{ maxWidth: 420 }}
            value={pasted}
            placeholder="cck_…（明文只进本机与请求头，不外显）"
            onChange={(e) => { setPasted(e.target.value.trim()); setPasteCheck(null); }}
            aria-label="粘贴 API key"
            autoComplete="off"
          />
          <button className="btn secondary" disabled={pasted.length < 8 || busy != null} onClick={() => void validate(pasted, "paste")}>
            {busy === "validate-paste" ? "实检中…" : "验证并启用"}
          </button>
        </div>
        {pasteCheck?.ok && (
          <div className="alert ok" style={{ marginTop: 8 }}>
            ✓ 有效 · 绑定钱包 <span className="mono">{pasteCheck.info!.consumer_wallet.slice(0, 10)}…</span>
            {pasteCheck.info!.consumer_wallet.toLowerCase() !== address?.toLowerCase() ? (
              <b>（≠当前连接钱包，支付会 402——建议改用当前钱包签发新 key）</b>
            ) : (
              <span>（=当前连接钱包，可直接支付）</span>
            )}
            <div className="btn-row" style={{ marginTop: 6 }}>
              <button className="btn small" onClick={() => { saveKey(pasted); setStoredKey(pasted); setPasteCheck(null); setPasted(""); setLocalCheck(null); }}>
                存入本机并启用
              </button>
            </div>
          </div>
        )}
        {pasteCheck && !pasteCheck.ok && (
          <div className="alert err" style={{ marginTop: 8 }}>
            验证未通过（key 不存在/已吊销）。确认从原机器复制的是完整明文，或直接为当前钱包签发新 key。
          </div>
        )}
      </div>

      {/* 第三层（换机主推）：为当前钱包签发新 key */}
      <div className="btn-row" style={{ marginTop: 4, marginBottom: 12 }}>
        <button className="btn" disabled={!address || busy != null} onClick={issue}>
          {busy === "issue" ? <Spinner label="签发中…" /> : `为当前钱包签发新 key${address ? `（${address.slice(0, 8)}…）` : ""}`}
        </button>
      </div>

      {error != null && <ErrorBox error={error} />}

      {issued && !saved && (
        <div>
          <WarnBox>
            <b>明文 key 仅此一次展示</b>（服务端只存 hash，关掉就找不回）。请复制保存：
          </WarnBox>
          <div className="key-reveal">{issued.api_key}</div>
          <div className="btn-row" style={{ marginTop: 12 }}>
            <CopyButton text={issued.api_key} />
            <button className="btn" onClick={confirmSaved}>
              我已保存（存入本机并实检）
            </button>
          </div>
        </div>
      )}
      {issued && saved && <SuccessBox>已确认保存并实检通过，试用调用将自动携带该 key（key_id={issued.key_id}）。</SuccessBox>}

      {walletKeys.data && walletKeys.data.keys.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div className="dim" style={{ marginBottom: 6 }}>
            该钱包的历史 key（服务端视角，明文不可见）：
          </div>
          <table className="list">
            <thead>
              <tr>
                <th>key_id</th>
                <th>状态</th>
                <th>签发时间</th>
                <th>配额</th>
              </tr>
            </thead>
            <tbody>
              {walletKeys.data.keys.map((k) => (
                <tr key={k.key_id}>
                  <td className="mono">{k.key_id}</td>
                  <td>
                    <Badge kind={k.status === "active" ? "ok" : "muted"}>{k.status}</Badge>
                  </td>
                  <td className="num">{k.created_at.slice(0, 19)}</td>
                  <td className="num">{k.quota_raw ?? "不限"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}


/* ============ 4. 试用调用 ============ */
export function TrialCallSection() {
  const w = useWallet();
  const { address } = w;
  const catalog = useAsync(() => coreApi.catalog(), []);
  const services = catalog.data?.services.filter((s) => s.status === "active") ?? [];
  const [svcId, setSvcId] = useState<string>("");
  // ① 授权额度 → ② 支付调用（同一张卡内的顺序动线）
  const [stepOpen, setStepOpen] = useState(false); // ①用户手动展开标记；展示逻辑：额度不足自动展开、充足默认折叠直达②
  const [approveAmount, setApproveAmount] = useState<string>(""); // 默认=单价×10（选中服务时置入）
  const [approveTx, setApproveTx] = useState<TxProgress | null>(null);
  const [approveBusy, setApproveBusy] = useState(false);
  const [approveCancelled, setApproveCancelled] = useState<string | null>(null);
  const [approveError, setApproveError] = useState<unknown>(null);
  const [approvedThisSession, setApprovedThisSession] = useState(false); // 本会话是否经历过授权步骤（历史标记用）
  const [fundsTick, setFundsTick] = useState(0);
  const APPROVED_FLAG = "coincall.approvedOnce"; // localStorage：已授权过（额度仍充足时①保持折叠）
  const [paramsJson, setParamsJson] = useState("{}");
  const [parsedParams, setParsedParams] = useState<Record<string, unknown> | null>({});
  const [calling, setCalling] = useState(false);
  const [waitingSign, setWaitingSign] = useState(false);
  const [cancelled, setCancelled] = useState<string | null>(null);
  const [result, setResult] = useState<{ ok: boolean; body: unknown; receipt: { receiptId: string | null; chargedRaw: string | null }; challenge?: Gateway402Challenge } | null>(null);
  const [error, setError] = useState<unknown>(null);

  const [apiKey, setApiKey] = useState<string | null>(() => localStorage.getItem(APIKEY_STORE));
  useEffect(() => {
    const h = () => setApiKey(localStorage.getItem(APIKEY_STORE));
    window.addEventListener("coincall:apikey", h);
    return () => window.removeEventListener("coincall:apikey", h);
  }, []);
  const svc = services.find((s) => s.service_id === svcId) ?? null;
  const priceRaw = svc ? BigInt(svc.manifest.pricing.amount_raw) : null;
  const priceHuman = svc?.manifest.pricing.amount ?? "0";

  // 该服务的可用授权（三数可折叠展示；充足→折叠直达②）
  const funds = useAsync<{ balance: bigint; allowance: bigint } | null>(
    () => (address ? Promise.all([fetchTokenBalance(address), fetchAllowance(address)]).then(([balance, allowance]) => ({ balance, allowance })) : Promise.resolve(null)),
    [address, fundsTick]
  );
  const allowance = funds.data?.allowance ?? null;
  const allowanceOk = priceRaw != null && allowance != null && allowance >= priceRaw;
  const rememberedApproved = localStorage.getItem(APPROVED_FLAG) === "1";
  // 折叠条件：额度充足，或（历史已授权过且数据未到/未变坏）
  const step1Collapsed = allowanceOk || (rememberedApproved && allowance == null);

  // 选中服务时预置授权金额 = 单价×10（一次授权可供多次调用）
  useEffect(() => {
    if (svc) {
      try {
        setApproveAmount((v) => v || fromRaw(toRaw(priceHuman) * 10n));
      } catch {
        /* 定价异常则留空手填 */
      }
    }
  }, [svcId, priceHuman]);

  const runApprove = async () => {
    if (!address) return;
    setApproveBusy(true);
    setApproveError(null);
    setApproveCancelled(null);
    setApproveTx({ status: "waiting" });
    try {
      const data = encodeAddrUint(SEL.approve, VAULT, toRaw(approveAmount));
      await w.sendTransaction(USDT, data, setApproveTx);
      localStorage.setItem(APPROVED_FLAG, "1");
      setApprovedThisSession(true);
      setStepOpen(false); // 收起① → 动线进入②
      setFundsTick((t) => t + 1); // 刷新授权额 → 折叠态展示最新可用授权
    } catch (e) {
      if (isUserRejected(e)) {
        setApproveCancelled("你取消了授权交易（钱包弹窗里拒绝）");
        setApproveTx(null);
      } else {
        setApproveError(e);
        setApproveTx({ status: "failed", error: humanizeError(e).title });
      }
    } finally {
      setApproveBusy(false);
    }
  };

  const [spent, setSpent] = useState<bigint>(() => loadSpentRaw());
  useEffect(() => {
    const h = () => setSpent(loadSpentRaw());
    window.addEventListener("coincall:spent", h);
    return () => window.removeEventListener("coincall:spent", h);
  }, []);
  const [budgetTick, setBudgetTick] = useState(0);
  useEffect(() => {
    const h = () => setBudgetTick((t) => t + 1);
    window.addEventListener("coincall:budget", h);
    return () => window.removeEventListener("coincall:budget", h);
  }, []);
  const budgetNow = useMemo(() => loadBudgetRaw(), [budgetTick]);

  const overBudget = budgetNow != null && priceRaw != null && spent + priceRaw > budgetNow;
  const chainBlocked = w.mode === "injected" && !w.chainOk;

  const call = async () => {
    if (!address || !svc || priceRaw == null || !parsedParams) return;
    setCalling(true);
    setWaitingSign(true);
    setError(null);
    setResult(null);
    setCancelled(null);
    try {
      // ① 组装授权六元组（nonce 随机、10 分钟窗口）→ 钱包弹窗签名（digest 由扩展计算）
      const now = Math.floor(Date.now() / 1000);
      const auth = buildCallAuthorization(address, VAULT, priceRaw, now);
      const sig = await w.signTypedDataAuth(auth, VAULT);
      setWaitingSign(false);
      // ② X-PAYMENT（v 已归一 27|28）+ 幂等键（参数哈希，同参重试同键防双扣）
      const payment = buildPaymentHeader(auth, sig);
      const idem = await hashSha256HexStringish(`${svc.service_id}:${JSON.stringify(parsedParams)}`);
      const headers = { "X-Api-Key": apiKey ?? "", "X-PAYMENT": payment, "X-Idempotency-Key": idem };
      // ③ 调用（402 在 apiFetch 里抛 ApiError，这里接住转质询）
      const outcome = await gatewayApi.call(svc.service_id, parsedParams, headers);
      setResult({ ok: true, body: outcome.body, receipt: { receiptId: outcome.receipt.receiptId, chargedRaw: outcome.receipt.chargedRaw } });
      addSpentRaw(priceRaw);
      appendHistory({
        ts: Date.now(),
        serviceId: svc.service_id,
        serviceName: svc.manifest.name,
        amountHuman: svc.manifest.pricing.amount,
        amountRaw: svc.manifest.pricing.amount_raw,
        receiptId: outcome.receipt.receiptId,
        chargedRaw: outcome.receipt.chargedRaw,
        receiptSigEd: outcome.receipt.receiptSigEd,
        receiptTs: outcome.receipt.receiptTs,
        ok: true,
        withApprove: approvedThisSession,
        resultPreview: JSON.stringify(outcome.body).slice(0, 120),
      });
    } catch (e) {
      setWaitingSign(false);
      if (isUserRejected(e)) {
        setCancelled("你取消了支付授权签名（钱包弹窗里拒绝）。未产生任何扣款，可随时重试。");
      } else if (e instanceof ApiError && e.status === 402) {
        const challenge = e.raw as Gateway402Challenge;
        // 授权不足：不再跨区跳转——直接展开①并预置覆盖本单的金额（单价×10）
        if (challenge.code === "insufficient_allowance") {
          setStepOpen(true);
          try {
            setApproveAmount(fromRaw((priceRaw ?? 0n) * 10n));
          } catch {
            /* 保留现值 */
          }
        }
        setResult({ ok: false, body: e.raw, receipt: { receiptId: null, chargedRaw: null }, challenge });
        appendHistory({
          ts: Date.now(),
          serviceId: svc.service_id,
          serviceName: svc.manifest.name,
          amountHuman: svc.manifest.pricing.amount,
          amountRaw: svc.manifest.pricing.amount_raw,
          receiptId: null,
          chargedRaw: null,
          ok: false,
          errorDetail: `${challenge.code}: ${challenge.detail}`,
        });
      } else {
        setError(e);
      }
    } finally {
      setCalling(false);
    }
  };

  return (
    <div className="card">
      <h3>④ 试用调用（钱包签名 → 402 网关）</h3>
      <p className="card-desc">
        选择服务 → 按其 input_schema 生成的表单填参数 → 点「付费调用」。钱包对 Authorization 六元组做 EIP-712
        签名（域 PayVault/1/968，窗口 10 分钟）组 X-PAYMENT 头。Provider 失败不扣费；同参数重试自动带幂等键。
      </p>

      <div className="field">
        <label>服务</label>
        <select
          value={svcId}
          onChange={(e) => {
            setSvcId(e.target.value);
            setParamsJson("{}");
            setParsedParams({});
            setResult(null);
          }}
        >
          <option value="">— 选择服务（目录 {services.length} 个在售） —</option>
          {services.map((s) => (
            <option key={s.service_id} value={s.service_id}>
              {s.manifest.name} · {s.manifest.pricing.amount} USDT/次（raw={s.manifest.pricing.amount_raw}）
            </option>
          ))}
        </select>
      </div>

      {svc && (
        <>
          {/* ① 授权额度 → ② 支付调用（同一张卡内的顺序动线） */}
          <div className="wizard-steps" style={{ marginBottom: 12 }}>
            <button type="button" className={`wizard-step${step1Collapsed ? " done" : stepOpen ? " active" : ""}`} onClick={() => setStepOpen((o) => !o)}>
              <span className="n">{step1Collapsed ? "✓" : "1"}</span>
              ① 授权额度
            </button>
            <span className="arrow" style={{ color: "var(--primary)", fontWeight: 700 }}>→</span>
            <span className={`wizard-step${step1Collapsed ? " active" : ""}`}>
              <span className="n">2</span>
              ② 支付调用
            </span>
          </div>

          {step1Collapsed && !stepOpen ? (
            <div className="alert ok flex" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
              <span>
                ✓ 已授权，可直接支付（对 PayVault 可用授权 {allowance != null ? fromRaw(allowance) : "…"} USDT
                <span className="dim num"> raw={allowance?.toString() ?? "…"}</span>）
              </span>
              <button type="button" className="btn small secondary" onClick={() => setStepOpen(true)}>
                查看/调整授权
              </button>
            </div>
          ) : (
            <div className="card" style={{ boxShadow: "none", background: "var(--surface-2)", marginBottom: 12 }}>
              <div className="flex" style={{ justifyContent: "space-between" }}>
                <h3 className="mt-0" style={{ fontSize: 14 }}>① 授权额度（approve 给 PayVault）</h3>
                {step1Collapsed && (
                  <button type="button" className="btn small secondary" onClick={() => setStepOpen(false)}>
                    收起
                  </button>
                )}
              </div>
              <p className="card-desc" style={{ margin: "0 0 8px" }}>
                keeper 只按你逐笔签名的付费授权划款，这里的 approve 是扣款上限——<b>一次授权可供多次调用</b>（默认 = 本单价格 ×10）。
              </p>
              <div className="flex" style={{ marginBottom: 8, flexWrap: "wrap" }}>
                <span className="dim" style={{ fontSize: 12 }}>当前授权 {allowance != null ? fromRaw(allowance) : "…"} USDT · 余额 {funds.data ? fromRaw(funds.data.balance) : "…"} USDT</span>
                <button type="button" className="btn small secondary" onClick={() => setFundsTick((t) => t + 1)}>
                  刷新
                </button>
              </div>
              <div className="field" style={{ marginBottom: 8 }}>
                <label>授权金额（USDT）</label>
                <div className="flex">
                  <input
                    type="text"
                    style={{ width: 140 }}
                    value={approveAmount}
                    onChange={(e) => setApproveAmount(e.target.value.trim())}
                    aria-label="授权金额"
                  />
                  <div className="seg">
                    <button type="button" className="btn small secondary" onClick={() => setApproveAmount(fromRaw((priceRaw ?? 0n) * 10n))}>
                      单价×10
                    </button>
                    <button type="button" className="btn small secondary" onClick={() => setApproveAmount("0.1")}>
                      0.1
                    </button>
                    <button type="button" className="btn small secondary" onClick={() => setApproveAmount("1")}>
                      1
                    </button>
                  </div>
                </div>
                <div className="help num">
                  = raw {(() => { try { return toRaw(approveAmount).toString(); } catch { return "格式错误"; } })()} · 交易经钱包扩展弹窗确认（20 gwei）
                </div>
              </div>
              <div className="btn-row">
                <button className="btn" disabled={approveBusy || chainBlocked || !/^[\d.]+$/.test(approveAmount)} onClick={runApprove}>
                  {approveBusy ? "等待钱包确认…" : "去钱包授权"}
                </button>
                {chainBlocked && <span className="dim">钱包不在 968 链，先在顶栏切链。</span>}
              </div>
              {approveTx && (
                <div className={`alert ${approveTx.status === "confirmed" ? "ok" : approveTx.status === "failed" ? "err" : "info"}`} style={{ marginTop: 8 }}>
                  {approveTx.status === "waiting" && (
                    <span className="flex"><span className="spin" /> 等待钱包确认…（请在扩展弹窗里确认授权交易）</span>
                  )}
                  {approveTx.status === "pending" && (
                    <span className="flex"><span className="spin" /> 授权交易已广播，等待打包…</span>
                  )}
                  {approveTx.status === "confirmed" && <span>✓ 授权已上链——进入 ② 支付调用。</span>}
                  {approveTx.status === "failed" && <span>✗ 失败：{approveTx.error}</span>}
                  {approveTx.hash && <> · <TxLink hash={approveTx.hash} /></>}
                </div>
              )}
              {approveCancelled && <WarnBox>{approveCancelled}。没有产生任何交易，可随时重试。</WarnBox>}
              {approveError != null && <ErrorBox error={approveError} />}
            </div>
          )}

          <div className="alert info">
            <b>{svc.manifest.name}</b> · 单价 <b className="num">{svc.manifest.pricing.amount} USDT</b>
            <span className="dim num">（raw={svc.manifest.pricing.amount_raw}）</span> · Provider {svc.manifest.provider.display_name} · 本次将请钱包签名授权 PayVault 划扣该金额。
          </div>
          <SchemaForm
            schema={svc.manifest.input_schema}
            value={paramsJson}
            onChange={(text, parsed) => {
              setParamsJson(text);
              setParsedParams(parsed);
            }}
          />
        </>
      )}

      {chainBlocked && (
        <WarnBox>
          钱包当前在链 {w.chainId ?? "?"}，签名域是 BOT Chain（968）——先切链再调用。
          <button className="btn small" style={{ marginLeft: 8 }} onClick={() => void w.ensureChain()}>
            引导切链
          </button>
        </WarnBox>
      )}
      {!apiKey && svc && (
        <WarnBox>
          API key 未就绪（网关会 402 payment_missing）——到第 ③ 步任选其一：<b>粘贴原机器的 key</b>（验证并启用）、或<b>为当前钱包签发新 key</b>。
        </WarnBox>
      )}
      {overBudget && (
        <div className="alert warn">
          <b>预算拦截</b>：本会话已花费 {fromRaw(spent)} USDT（raw={spent.toString()}）+ 本次 {svc?.manifest.pricing.amount ?? "-"}{" "}
          超出预算 {budgetNow != null ? fromRaw(budgetNow) : "-"} USDT。调用按钮已禁用；如需继续请到「预算设置」调整。
        </div>
      )}
      {cancelled && <WarnBox>{cancelled}</WarnBox>}

      <div className="btn-row">
        <button className="btn" disabled={!svc || !parsedParams || calling || overBudget || !apiKey || chainBlocked} onClick={call}>
          {calling ? (
            waitingSign ? (
              <Spinner label="等待钱包签名确认…" />
            ) : (
              <Spinner label="调用网关中…" />
            )
          ) : (
            `付费调用（${svc ? svc.manifest.pricing.amount + " USDT" : ""}）`
          )}
        </button>
        {svc && (
          <span className="dim">
            本次签名：value=raw {svc.manifest.pricing.amount_raw} · to={VAULT.slice(0, 10)}… · 窗口 600s · digest 由钱包扩展计算
          </span>
        )}
      </div>

      {error != null && <ErrorBox error={error} />}

      {result?.ok && (
        <div>
          <SuccessBox>
            ✓ 调用成功并已计费
            {result.receipt.receiptId && (
              <>
                {" "}
                · 收据 <span className="mono">{result.receipt.receiptId}</span>
              </>
            )}
            {result.receipt.chargedRaw && (
              <>
                {" "}
                · 扣款 raw=<span className="num">{result.receipt.chargedRaw}</span>（{fromRaw(result.receipt.chargedRaw)} USDT）
              </>
            )}
            。keeper 稍后批量上链（总览页可观测）。
          </SuccessBox>
          <div className="section-title">服务响应体</div>
          <pre className="code-box" style={{ padding: 12, borderRadius: 8, maxHeight: 320, overflow: "auto" }}>{JSON.stringify(result.body, null, 2)}</pre>
        </div>
      )}

      {result && !result.ok && result.challenge && <ChallengePanel challenge={result.challenge} />}
    </div>
  );
}

/** 402 质询 → 人话（授权不足已在上方①内联展开并预置金额，不再跨区跳转）。 */
function ChallengePanel({ challenge }: { challenge: Gateway402Challenge }) {
  const h = humanizeChallenge(challenge);
  return (
    <div className="alert err">
      <div style={{ fontWeight: 700 }}>
        402 · {h.title} <span className="dim">（code: {challenge.code}）</span>
      </div>
      <div>{h.hint}</div>
      {h.action === "approve" && <div>→ 已在上方「① 授权额度」展开并预置金额，完成授权后回来支付。</div>}
      <div className="btn-row" style={{ marginTop: 8 }}>
        <CopyButton text={JSON.stringify(challenge, null, 2)} label="复制质询 JSON" />
      </div>
      <details className="raw-detail">
        <summary>详细信息（质询原始 JSON）</summary>
        <pre>{JSON.stringify(challenge, null, 2)}</pre>
      </details>
    </div>
  );
}

/* ============ 5. 调用历史 ============ */
function HistorySection() {
  const [list, setList] = useState<CallHistoryEntry[]>(() => loadHistory());
  const [confirmClear, setConfirmClear] = useState(false);
  const [ratingFor, setRatingFor] = useState<CallHistoryEntry | null>(null);
  useEffect(() => {
    const h = () => setList(loadHistory());
    window.addEventListener("coincall:history", h);
    return () => window.removeEventListener("coincall:history", h);
  }, []);

  return (
    <div className="card">
      <div className="flex" style={{ justifyContent: "space-between" }}>
        <h3 className="mb-0">⑤ 调用历史（本机 localStorage）</h3>
        {list.length > 0 && (
          <button className="btn small danger" onClick={() => setConfirmClear(true)}>
            清空历史
          </button>
        )}
      </div>
      {list.length === 0 ? (
        <Empty text="还没有调用记录——去第 ④ 步发起第一次付费调用" />
      ) : (
        <table className="list" style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>时间</th>
              <th>服务</th>
              <th>金额</th>
              <th>收据</th>
              <th>结果</th>
            </tr>
          </thead>
          <tbody>
            {list.map((e, i) => (
              <tr key={`${e.ts}-${i}`}>
                <td className="num">{new Date(e.ts).toLocaleTimeString("zh-CN")}</td>
                <td>{e.serviceName}</td>
                <td className="num">
                  {e.amountHuman} <span className="dim">raw={e.amountRaw}</span>
                </td>
                <td className="mono dim">{e.receiptId ? e.receiptId.slice(0, 16) + "…" : "-"}</td>
                <td>
                  {e.ok ? (
                    <>
                      <Badge kind="ok">成功{e.chargedRaw ? ` · ${fromRaw(e.chargedRaw)}` : ""}</Badge>
                      {e.withApprove && <span className="badge muted" title="本会话首单：先完成了授权步骤">含授权</span>}
                      {e.rated ? (
                        <span className="badge info" title="该次调用的收据已提交评价">已评价</span>
                      ) : e.receiptSigEd && e.receiptTs ? (
                        <button className="btn small secondary" onClick={() => setRatingFor(e)}>
                          评价
                        </button>
                      ) : (
                        <span className="badge muted" title="旧记录未存签名头，无法验证评价（新调用即可评价）">不可评</span>
                      )}
                    </>
                  ) : (
                    <Badge kind="err">{e.errorDetail?.slice(0, 40) ?? "失败"}</Badge>
                  )}
                  {e.resultPreview && <div className="dim mono" style={{ maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis" }}>{e.resultPreview}</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <RatingDialog
        entry={ratingFor}
        onClose={() => setRatingFor(null)}
        onRated={() => {
          if (ratingFor) markHistoryRated(ratingFor.ts);
          setRatingFor(null);
        }}
      />

      <ConfirmDialog
        open={confirmClear}
        title="清空调用历史"
        body="只清除本机 localStorage 的记录，不影响链上与服务端流水。"
        confirmText="确认清空"
        onConfirm={() => {
          clearHistory();
          setConfirmClear(false);
        }}
        onCancel={() => setConfirmClear(false)}
      />
    </div>
  );
}

/** 评价弹层：星级 1-5 + 可选一句话 → 收据五元组（历史存的签名头）→ POST /feedback。 */
function RatingDialog({ entry, onClose, onRated }: { entry: CallHistoryEntry | null; onClose: () => void; onRated: () => void }) {
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [conflict, setConflict] = useState<string | null>(null);

  if (!entry) return null;

  const submit = async () => {
    setBusy(true);
    setError(null);
    setConflict(null);
    try {
      await feedbackApi.submit(
        entry.serviceId,
        {
          receipt_id: entry.receiptId ?? "",
          service_id: entry.serviceId,
          amount_raw: entry.amountRaw,
          status: "success", // 网关收据五元组口径：成功计费
          ts: entry.receiptTs ?? 0,
          receipt_sig_hex: entry.receiptSigEd ?? "",
        },
        rating,
        comment.trim() || undefined,
      );
      onRated();
    } catch (e) {
      const ae = e as { status?: number; error?: string };
      if (ae?.status === 409) setConflict("该次调用已评价过（一收据一评）。");
      else if (ae?.status === 401) setConflict("收据签名验证未通过——该记录的收据材料无效，无法评价。");
      else if (ae?.status === 429) setConflict("提交太频繁，请稍后再试。");
      else setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="评价服务"
      style={{ position: "fixed", inset: 0, background: "rgba(10,16,28,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 60 }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="card" style={{ maxWidth: 440, margin: 20, width: "94vw" }}>
        <h3 className="mt-0">评价「{entry.serviceName}」</h3>
        <p className="card-desc">
          付费即发言权：评价会用该次调用的链上收据验证（收据 {entry.receiptId?.slice(0, 14)}…），一收据一评。
        </p>
        <div className="field">
          <label>星级（1-5）</label>
          <div className="flex" style={{ gap: 4 }}>
            {[1, 2, 3, 4, 5].map((n) => (
              <button
                key={n}
                type="button"
                className="btn small secondary"
                style={{ fontSize: 18, padding: "4px 10px", color: n <= rating ? "#f5a623" : undefined }}
                onClick={() => setRating(n)}
                aria-label={`${n} 星`}
              >
                ★
              </button>
            ))}
          </div>
        </div>
        <div className="field">
          <label>一句话（可选）</label>
          <input type="text" value={comment} maxLength={200} placeholder="翻译质量如何？" onChange={(e) => setComment(e.target.value)} aria-label="评价内容" />
        </div>
        {conflict && <WarnBox>{conflict}</WarnBox>}
        {error != null && <ErrorBox error={error} />}
        <div className="btn-row" style={{ justifyContent: "flex-end" }}>
          <button className="btn secondary" onClick={onClose}>
            取消
          </button>
          <button className="btn" disabled={rating < 1 || rating > 5 || busy} onClick={submit}>
            {busy ? "提交中…" : "提交评价"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ============ 6. 预算 ============ */
function BudgetSection() {
  const [amount, setAmount] = useState(() => {
    const b = loadBudgetRaw();
    return b != null ? fromRaw(b) : "";
  });
  const [savedFlash, setSavedFlash] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [spent, setSpent] = useState<bigint>(() => loadSpentRaw());
  useEffect(() => {
    const h = () => setSpent(loadSpentRaw());
    window.addEventListener("coincall:spent", h);
    return () => window.removeEventListener("coincall:spent", h);
  }, []);

  const save = () => {
    setErr(null);
    try {
      saveBudgetRaw(amount.trim() === "" ? null : toRaw(amount.trim()));
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 1500);
    } catch (e) {
      setErr(humanizeError(e).title);
    }
  };

  return (
    <div className="card">
      <h3>⑥ 预算设置（本机）</h3>
      <p className="card-desc">
        设置后，试用调用在「本会话已花费 + 本次价格 &gt; 预算」时会禁用按钮并解释。留空 = 不限制。当前会话已花费{" "}
        <b className="num">{fromRaw(spent)}</b> USDT（raw={spent.toString()}）。
      </p>
      <div className="flex">
        <input type="text" style={{ width: 200 }} value={amount} placeholder="例如 0.5（USDT）" onChange={(e) => setAmount(e.target.value)} />
        <button className="btn" onClick={save}>
          {savedFlash ? "已保存 ✓" : "保存预算"}
        </button>
        <button className="btn secondary" onClick={() => { setAmount(""); saveBudgetRaw(null); }}>
          清除限制
        </button>
      </div>
      {err && <div className="err" style={{ color: "var(--danger)", fontSize: 12, marginTop: 8 }}>{err}</div>}
      <InfoBox>预算只是消费端本地护栏（可随时清除）；链上层面的硬上限是你钱包的余额与授权额。</InfoBox>
    </div>
  );
}
