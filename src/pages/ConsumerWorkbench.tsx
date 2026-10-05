/**
 * 消费端工作台（核心页）：
 * 连接钱包（浏览器扩展，地址只读）→ 资金（余额/授权/可用 + mint + 授权滑条，扩展弹窗发交易）→
 * API key → 试用调用（动态表单 + 扩展弹窗 EIP-712 签名 + X-PAYMENT → 402 动作化）→ 历史 / 预算。
 * 控制台不接触任何私钥；无扩展的演示机可展开「一次性演示钱包」兜底（关页即焚）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { hashSha256HexStringish } from "../lib/idempotency";
import { ApiError } from "../api/client";
import { coreApi } from "../api/core";
import { gatewayApi, type Gateway402Challenge } from "../api/gateway";
import { MOCK_USDT, PAY_VAULT as VAULT, SEL, fromRaw, toRaw } from "../chain/constants";
import { encodeAddrUint, fetchAllowance, fetchTokenBalance, type TxProgress } from "../chain/rpc";
import { isUserRejected } from "../chain/injected";
import { buildCallAuthorization, buildPaymentHeader } from "../chain/signing";
import { SchemaForm } from "../components/SchemaForm";
import { AsyncSection, Badge, ConfirmDialog, CopyButton, Empty, ErrorBox, InfoBox, Spinner, SuccessBox, TxLink, WarnBox } from "../components/ui";
import { humanizeChallenge, humanizeError } from "../lib/errors";
import { addSpentRaw, appendHistory, clearHistory, loadBudgetRaw, loadHistory, loadSpentRaw, saveBudgetRaw, type CallHistoryEntry } from "../lib/storage";
import { useAsync } from "../lib/useAsync";
import { useWallet } from "../state/WalletContext";

const APIKEY_STORE = "coincall.apikey";

export default function ConsumerWorkbench() {
  const { address } = useWallet();
  const connected = address != null;

  return (
    <div>
      <h1 className="page-title">消费端工作台</h1>
      <p className="page-sub">从零完成一次付费调用：连接钱包 → 备好资金 → 签发 API key → 选服务 → 钱包签名授权 → 402 网关调用。签名与交易全部在你的浏览器钱包弹窗里完成，控制台不接触私钥。</p>

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
                浏览器钱包 <b className="mono">{w.address}</b>
              </>
            ) : (
              <>
                一次性演示钱包 <b className="mono">{w.address}</b> <Badge kind="warn">关页即焚</Badge>
              </>
            )}
          </span>
          <CopyButton text={w.address} />
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
          {w.connecting ? <Spinner label="等待钱包确认…" /> : "连接钱包（OKX / MetaMask）"}
        </button>
        {!w.hasInjected && <span className="dim">未检测到浏览器钱包扩展。</span>}
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
            需要先给它充测试网 BOT 作 gas 与 MockUSDT（可用页面内 key 文件去水龙头领）。
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
function FundsSection() {
  const w = useWallet();
  const { address } = w;
  const [refreshTick, setRefreshTick] = useState(0);
  const [tx, setTx] = useState<TxProgress | null>(null);
  const [cancelled, setCancelled] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [approvePreset, setApprovePreset] = useState<string>("0.1");
  const [presetFromChallenge, setPresetFromChallenge] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<null | "mint" | "approve">(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // 402 质询跳转：预置滑条金额并滚动到本面板
  useEffect(() => {
    const handler = (e: Event) => {
      const amount = (e as CustomEvent<string>).detail;
      setApprovePreset(amount);
      setPresetFromChallenge(amount);
      panelRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    };
    window.addEventListener("coincall:goto-approve", handler);
    return () => window.removeEventListener("coincall:goto-approve", handler);
  }, []);

  const funds = useAsync<{ balance: bigint; allowance: bigint } | null>(
    () => (address ? Promise.all([fetchTokenBalance(address), fetchAllowance(address)]).then(([balance, allowance]) => ({ balance, allowance })) : Promise.resolve(null)),
    [address, refreshTick]
  );

  const refresh = useCallback(() => setRefreshTick((t) => t + 1), []);

  const runTx = async (kind: "mint" | "approve") => {
    if (!address) return;
    setBusy(true);
    setError(null);
    setCancelled(null);
    setTx({ status: "waiting" }); // 等待扩展弹窗确认
    try {
      const data =
        kind === "mint"
          ? encodeAddrUint(SEL.mint, address, toRaw("10"))
          : encodeAddrUint(SEL.approve, VAULT, toRaw(approvePreset));
      await w.sendTransaction(MOCK_USDT, data, setTx);
      refresh();
    } catch (e) {
      if (isUserRejected(e)) {
        setCancelled(kind === "mint" ? "你取消了铸造交易（钱包弹窗里拒绝）" : "你取消了授权交易（钱包弹窗里拒绝）");
        setTx(null);
      } else {
        setError(e);
        setTx({ status: "failed", error: humanizeError(e).title });
      }
    } finally {
      setBusy(false);
      setConfirming(null);
      setPresetFromChallenge(null);
    }
  };

  const d = funds.data;
  const available = d ? (d.allowance < d.balance ? d.allowance : d.balance) : null;
  const chainBlocked = w.mode === "injected" && !w.chainOk;

  return (
    <div className="card" ref={panelRef}>
      <div className="flex" style={{ justifyContent: "space-between" }}>
        <h3 className="mb-0">② 资金面板（MockUSDT · 6 位精度）</h3>
        <button className="btn small secondary" onClick={refresh}>
          刷新
        </button>
      </div>
      <p className="card-desc">可用额 = min(余额, 对 PayVault 的授权额)，即当前真正能用于付费调用的额度。铸造/授权都是钱包弹窗确认的交易（gas 恒 20 gwei）。</p>

      {funds.loading && d == null ? (
        <Spinner label="eth_call 读链中…" />
      ) : funds.error != null ? (
        <ErrorBox error={funds.error} />
      ) : d ? (
        <div className="stat-grid">
          <div className="stat-card">
            <div className="k">token 余额</div>
            <div className="v num">{fromRaw(d.balance)}</div>
            <div className="s num">raw={d.balance.toString()}</div>
          </div>
          <div className="stat-card">
            <div className="k">对 PayVault 授权额</div>
            <div className="v num">{fromRaw(d.allowance)}</div>
            <div className="s num">raw={d.allowance.toString()} · {VAULT.slice(0, 10)}…</div>
          </div>
          <div className="stat-card">
            <div className="k">可用额（可消费）</div>
            <div className="v num" style={{ color: available && available > 0n ? "var(--success)" : "var(--danger)" }}>
              {fromRaw(available ?? 0n)}
            </div>
            <div className="s num">raw={(available ?? 0n).toString()}</div>
          </div>
        </div>
      ) : null}

      {chainBlocked && (
        <WarnBox>
          钱包当前在链 {w.chainId ?? "?"}，先切到 BOT Chain（968）再操作资金。
          <button className="btn small" style={{ marginLeft: 8 }} onClick={() => void w.ensureChain()}>
            引导切链
          </button>
        </WarnBox>
      )}
      {presetFromChallenge && (
        <div className="alert warn">
          网关 402 质询提示授权不足：已为你把滑条预置到 <b>{presetFromChallenge} USDT</b>，确认后点「授权」（钱包弹窗确认）。
        </div>
      )}
      {cancelled && <WarnBox>{cancelled}。没有产生任何交易，可随时重试。</WarnBox>}

      <div className="flex" style={{ alignItems: "flex-end" }}>
        <button className="btn secondary" disabled={busy || chainBlocked} onClick={() => setConfirming("mint")}>
          铸造 10 MockUSDT（测试网公开 mint）
        </button>
        <div className="grow" style={{ maxWidth: 420 }}>
          <div className="field" style={{ margin: 0 }}>
            <label>授权 PayVault 可花费额度（滑条）</label>
            <div className="flex">
              <div className="seg">
                {["0.01", "0.1", "1"].map((v) => (
                  <button key={v} className={approvePreset === v ? "active" : ""} onClick={() => setApprovePreset(v)}>
                    {v}
                  </button>
                ))}
              </div>
              <input
                type="text"
                style={{ width: 120 }}
                value={approvePreset}
                onChange={(e) => setApprovePreset(e.target.value.trim())}
                aria-label="自定义授权额度"
              />
              <button className="btn" disabled={busy || chainBlocked} onClick={() => setConfirming("approve")}>
                授权
              </button>
            </div>
            <div className="help num">
              = raw {(() => { try { return toRaw(approvePreset).toString(); } catch { return "格式错误"; } })()} ·
              {w.mode === "injected" ? " 钱包弹窗确认（eth_sendTransaction，20 gwei 固定费率）" : " 演示钱包本地直签（20 gwei）"}
            </div>
          </div>
        </div>
      </div>

      {tx && (
        <div className={`alert ${tx.status === "confirmed" ? "ok" : tx.status === "failed" ? "err" : "info"}`}>
          {tx.status === "waiting" && (
            <span className="flex">
              <span className="spin" /> 等待钱包确认…（请在扩展弹窗里确认）
            </span>
          )}
          {tx.status === "pending" && (
            <span className="flex">
              <span className="spin" /> 交易已广播，等待打包…
            </span>
          )}
          {tx.status === "confirmed" && <span>✓ 已上链确认</span>}
          {tx.status === "failed" && <span>✗ 失败：{tx.error}</span>}
          {tx.hash && (
            <span>
              {" "}
              · <TxLink hash={tx.hash} />
            </span>
          )}
        </div>
      )}
      {error != null && <ErrorBox error={error} />}

      <ConfirmDialog
        open={confirming === "mint"}
        title="确认铸造（上链，不可逆）"
        body={<>向 <span className="mono">{address}</span> 铸造 <b>10 MockUSDT</b>（raw=10000000）。测试网公开 mint，无真实价值；将弹出钱包确认。</>}
        confirmText="去钱包确认"
        onConfirm={() => void runTx("mint")}
        onCancel={() => setConfirming(null)}
      />
      <ConfirmDialog
        open={confirming === "approve"}
        title="确认授权（上链，不可逆）"
        body={
          <>
            允许 PayVault（{VAULT.slice(0, 10)}…）最多从你的钱包划走 <b className="num">{approvePreset} USDT</b>
            （raw {(() => { try { return toRaw(approvePreset).toString(); } catch { return "?"; } })()}）。keeper
            只按你逐笔签名的授权扣款，本授权是扣款的上限。可随时重新授权调整额度；将弹出钱包确认。
          </>
        }
        confirmText="去钱包确认"
        onConfirm={() => void runTx("approve")}
        onCancel={() => setConfirming(null)}
      />
    </div>
  );
}

/* ============ 3. API key ============ */
function ApiKeySection() {
  const { address } = useWallet();
  const [issued, setIssued] = useState<{ key_id: string; api_key: string } | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [storedKey, setStoredKey] = useState<string | null>(() => localStorage.getItem(APIKEY_STORE));

  const existing = useAsync(() => coreApi.listApiKeys(), []);
  const mine = (existing.data?.keys ?? []).filter((k) => address && k.consumer_wallet.toLowerCase() === address.toLowerCase());

  const issue = async () => {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const r = await coreApi.issueApiKey(address!);
      setIssued({ key_id: r.key_id, api_key: r.api_key });
      existing.reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const confirmSaved = () => {
    localStorage.setItem(APIKEY_STORE, issued?.api_key ?? "");
    setStoredKey(issued?.api_key ?? null);
    setSaved(true);
  };

  return (
    <div className="card">
      <h3>③ API key（X-Api-Key）</h3>
      <p className="card-desc">
        key 与当前连接的钱包地址绑定（<span className="mono">{address ?? "-"}</span>）；付费授权签名人必须是这个地址，否则网关 402。
      </p>
      <div className="btn-row" style={{ marginBottom: 12 }}>
        <button className="btn" disabled={!address || busy} onClick={issue}>
          {busy ? <Spinner label="签发中…" /> : "签发新 key"}
        </button>
        {storedKey && (
          <span className="dim">
            本机已保存 key：<span className="mono">{storedKey.slice(0, 14)}…</span>（用于试用调用）
          </span>
        )}
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
              我已保存（存入本机供试用调用）
            </button>
          </div>
        </div>
      )}
      {issued && saved && <SuccessBox>已确认保存。试用调用将自动携带该 key（key_id={issued.key_id}）。</SuccessBox>}

      <div style={{ marginTop: 16 }}>
        <div className="dim" style={{ marginBottom: 6 }}>
          该钱包的历史 key（服务端视角，明文不可见）：
        </div>
        <AsyncSection state={existing} empty="尚无 key">
          {() =>
            mine.length === 0 ? (
              <Empty text="当前钱包还没有签发记录" />
            ) : (
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
                  {mine.map((k) => (
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
            )
          }
        </AsyncSection>
      </div>
    </div>
  );
}

/* ============ 4. 试用调用 ============ */
function TrialCallSection() {
  const w = useWallet();
  const { address } = w;
  const catalog = useAsync(() => coreApi.catalog(), []);
  const services = catalog.data?.services.filter((s) => s.status === "active") ?? [];
  const [svcId, setSvcId] = useState<string>("");
  const [paramsJson, setParamsJson] = useState("{}");
  const [parsedParams, setParsedParams] = useState<Record<string, unknown> | null>({});
  const [calling, setCalling] = useState(false);
  const [waitingSign, setWaitingSign] = useState(false);
  const [cancelled, setCancelled] = useState<string | null>(null);
  const [result, setResult] = useState<{ ok: boolean; body: unknown; receipt: { receiptId: string | null; chargedRaw: string | null }; challenge?: Gateway402Challenge } | null>(null);
  const [error, setError] = useState<unknown>(null);

  const apiKey = localStorage.getItem(APIKEY_STORE);
  const svc = services.find((s) => s.service_id === svcId) ?? null;
  const priceRaw = svc ? BigInt(svc.manifest.pricing.amount_raw) : null;

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
        ok: true,
        resultPreview: JSON.stringify(outcome.body).slice(0, 120),
      });
    } catch (e) {
      setWaitingSign(false);
      if (isUserRejected(e)) {
        setCancelled("你取消了支付授权签名（钱包弹窗里拒绝）。未产生任何扣款，可随时重试。");
      } else if (e instanceof ApiError && e.status === 402) {
        const challenge = e.raw as Gateway402Challenge;
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
      {!apiKey && svc && <WarnBox>本机没有 API key——先到第 ③ 步签发（否则网关会 402 payment_missing）。</WarnBox>}
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

/** 402 质询 → 人话 + 动作按钮（如「去授权」→ 资金面板授权滑条，钱包弹窗确认）。 */
function ChallengePanel({ challenge }: { challenge: Gateway402Challenge }) {
  const h = humanizeChallenge(challenge);
  return (
    <div className="alert err">
      <div style={{ fontWeight: 700 }}>
        402 · {h.title} <span className="dim">（code: {challenge.code}）</span>
      </div>
      <div>{h.hint}</div>
      <div className="btn-row" style={{ marginTop: 8 }}>
        {h.action === "approve" && h.amount && (
          <button
            className="btn"
            onClick={() => window.dispatchEvent(new CustomEvent("coincall:goto-approve", { detail: h.amount }))}
          >
            去授权 {h.amount} USDT（钱包弹窗确认） →
          </button>
        )}
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
                    <Badge kind="ok">成功{e.chargedRaw ? ` · ${fromRaw(e.chargedRaw)}` : ""}</Badge>
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
