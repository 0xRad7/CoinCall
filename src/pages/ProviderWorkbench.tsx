/**
 * Provider 工作台：五步向导（登记 → 发布服务 → 管理服务 → 身份钱包绑定 → 提现）。
 * 步骤间状态保持（父级 state），进度指示可点击回跳。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { coreApi, type ServiceManifest } from "../api/core";
import { agentWalletSetTypedData, botChainApi, type AgentIdentity } from "../api/gateway";
import { ApiError } from "../api/client";
import { CHAIN_ID, IDENTITY_REGISTRY, PAY_VAULT as VAULT_ADDR, SEL as SEL_C, PAY_VAULT, fromRaw, toRaw } from "../chain/constants";
import { fetchProviderCredits, encodeAddrUint } from "../chain/rpc";
import { browserProvider, isUserRejected, sendInjectedTx, waitForInjectedReceipt, connectInjected, ensureChain968, silentAccounts } from "../chain/injected";
import { useWallet } from "../state/WalletContext";
import { AmountInput } from "../components/AmountInput";
import { IdentityRegister } from "../components/IdentityRegister";
import { JsonEditor } from "../components/JsonEditor";
import { AsyncSection, Badge, ConfirmDialog, CopyButton, ErrorBox, InfoBox, Spinner, SuccessBox, TxLink, WarnBox } from "../components/ui";
import { humanizeError, labelField } from "../lib/errors";
import { useAsync } from "../lib/useAsync";

const STEPS = [
  { key: "register", label: "① 登记 Provider" },
  { key: "publish", label: "② 发布服务" },
  { key: "manage", label: "③ 我的服务" },
  { key: "bind", label: "④ 身份钱包绑定" },
  { key: "withdraw", label: "⑤ 提现" },
] as const;
type StepKey = (typeof STEPS)[number]["key"];

export default function ProviderWorkbench() {
  const [step, setStep] = useState<StepKey>("register");
  const [done, setDone] = useState<Record<string, boolean>>({});
  const goto = (k: StepKey, ok?: boolean) => {
    setStep(k);
    if (ok !== undefined) setDone((d) => ({ ...d, [step]: ok }));
  };

  // 步骤间共享状态
  const [registered, setRegistered] = useState<{ agent_id: number; display_name: string; wallet?: string } | null>(null);

  return (
    <div>
      <h1 className="page-title">Provider 工作台</h1>
      <p className="page-sub">把「登记身份 → 发布服务 → 收款」的五步接入流程变成点击流。所有上链动作都有二次确认与交易外链。</p>

      <div className="wizard-steps">
        {STEPS.map((s) => (
          <button
            key={s.key}
            className={`wizard-step${step === s.key ? " active" : ""}${done[s.key] ? " done" : ""}`}
            onClick={() => setStep(s.key)}
          >
            <span className="n">{done[s.key] ? "✓" : STEPS.findIndex((x) => x.key === s.key) + 1}</span>
            {s.label}
          </button>
        ))}
      </div>

      {step === "register" && (
        <RegisterStep
          initial={registered}
          onNext={(r) => {
            setRegistered(r);
            goto("publish", true);
          }}
        />
      )}
      {step === "publish" && <PublishStep registered={registered} onNext={() => goto("manage", true)} onBack={() => setStep("register")} />}
      {step === "manage" && (
        <ManageStep
          agentId={registered?.agent_id ?? null}
          onNext={() => goto("bind", true)}
          onBack={() => setStep("publish")}
        />
      )}
      {step === "bind" && <BindStep onNext={() => goto("withdraw", true)} onBack={() => setStep("manage")} />}
      {step === "withdraw" && <WithdrawStep />}

      <InfoBox>
        步骤说明：①②③ 为主流程（登记 → 发布 → 运营）；④⑤ 为进阶（链上身份钱包绑定与提现），绑定身份钱包是发布 http_json
        服务前的收款前提。顶部步骤条可随时回跳，已填内容在同页会话内保留。
      </InfoBox>
    </div>
  );
}

/* ============ 步骤 1：登记 ============ */
export function RegisterStep({ initial, onNext }: { initial: { agent_id: number; display_name: string; wallet?: string } | null; onNext: (r: { agent_id: number; display_name: string; wallet?: string }) => void }) {
  const [agentId, setAgentId] = useState(initial?.agent_id ? String(initial.agent_id) : "");
  const [name, setName] = useState(initial?.display_name ?? "");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ agent_id: number; display_name: string; wallet?: string } | null>(initial ?? null);
  const [error, setError] = useState<unknown>(null);

  const idNum = Number(agentId);
  const valid = Number.isInteger(idNum) && idNum >= 1 && name.trim().length >= 1 && name.trim().length <= 128;

  // 链上身份实时校验（登记前预检）
  const identity = useAsync<AgentIdentity | null>(
    () =>
      Number.isInteger(idNum) && idNum >= 1
        ? botChainApi.identity(idNum).catch((e) => {
            if (e instanceof ApiError && e.status === 404) return null;
            throw e;
          })
        : Promise.resolve(null),
    [agentId]
  );

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const row = await coreApi.registerProvider(idNum, name.trim());
      setResult({ agent_id: row.agent_id, display_name: row.display_name, wallet: row.wallet });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <h3>登记 Provider（链上身份 → 平台档案）</h3>
      <p className="card-desc">agent_id 必须是 ERC-8004 注册表里已铸造的身份（tokenId）。提交时平台会实时到链上校验。</p>
      <div className="field">
        <label>Agent ID（ERC-8004 tokenId）</label>
        <input type="number" value={agentId} placeholder="例如 162" onChange={(e) => setAgentId(e.target.value)} />
        {agentId !== "" && (
          <div className="help">
            {identity.loading ? (
              "正在查询链上身份…"
            ) : identity.error ? (
              <ErrorBox error={identity.error} />
            ) : identity.data ? (
              <span style={{ color: "var(--success)" }}>
                ✓ 链上身份存在 · owner {identity.data.owner.slice(0, 10)}…（平台代管）· 身份钱包 {identity.data.agent_wallet}
              </span>
            ) : (
              <span style={{ color: "var(--warn)" }}>⚠ 链上没有该 tokenId 的身份记录（登记会被 422 拒绝）——可在下方注册一个</span>
            )}
          </div>
        )}
        <IdentityRegister
          onRegistered={(id) => {
            setAgentId(String(id));
          }}
        />
      </div>
      <div className="field">
        <label>展示名称</label>
        <input type="text" value={name} maxLength={128} placeholder="例如 My Translate Booth" onChange={(e) => setName(e.target.value)} />
        <div className="help">1–128 字符，会出现在收入榜与服务目录。</div>
      </div>
      {error != null && <ErrorBox error={error} />}
      {result && (
        <SuccessBox>
          已登记：agent_id={result.agent_id}「{result.display_name}」身份钱包 {result.wallet ?? "（读链上身份）"}
          。下一步发布服务。
        </SuccessBox>
      )}
      <div className="btn-row">
        <button className="btn" disabled={!valid || busy} onClick={submit}>
          {busy ? <Spinner label="提交中…" /> : "登记"}
        </button>
        <button className="btn secondary" disabled={!result} onClick={() => onNext(result ?? { agent_id: idNum, display_name: name })}>
          下一步：发布服务 →
        </button>
      </div>
    </div>
  );
}

/* ============ 步骤 2：发布服务 ============ */
export function PublishStep({ registered, onNext, onBack }: { registered: { agent_id: number; display_name: string } | null; onNext: () => void; onBack: () => void }) {
  const wctx = useWallet();
  const [serviceId, setServiceId] = useState("");
  // 服务收款钱包（manifest.provider.wallet）：收入实际到账地址；默认=当前连接的钱包
  const [revenueWallet, setRevenueWallet] = useState("");
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [amount, setAmount] = useState("0.01");
  const [endpointType, setEndpointType] = useState<"internal" | "http_json">("internal");
  const [endpointUrl, setEndpointUrl] = useState("");
  const [timeoutMs, setTimeoutMs] = useState(30000);
  const [inputSchema, setInputSchema] = useState('{\n  "type": "object",\n  "properties": {\n    "text": { "type": "string" }\n  },\n  "required": ["text"]\n}');
  const [outputSchema, setOutputSchema] = useState('{\n  "type": "object"\n}');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [ok, setOk] = useState<{ service_id: string; manifest_hash: string } | null>(null);

  const fieldErr = (f: string) => error?.fieldErrors?.[f];

  const identity = useAsync<AgentIdentity | null>(
    () => (registered ? botChainApi.identity(registered.agent_id).catch(() => null) : Promise.resolve(null)),
    [registered?.agent_id]
  );

  // 连接钱包后自动填默认值（未连接则留空，用户可手填）
  useEffect(() => {
    if (!revenueWallet && wctx.address) setRevenueWallet(wctx.address);
  }, [wctx.address, revenueWallet]);
  const revenueWalletValid = /^0x[0-9a-fA-F]{40}$/.test(revenueWallet.trim());
  const identityWallet = identity.data?.agent_wallet ?? null;
  const walletDiffers = identityWallet != null && revenueWalletValid && identityWallet.toLowerCase() !== revenueWallet.trim().toLowerCase();

  const conv = useMemo(() => {
    try {
      return { ok: true as const, raw: toRaw(amount) };
    } catch (e) {
      return { ok: false as const, error: e instanceof Error ? e.message : String(e) };
    }
  }, [amount]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    setOk(null);
    let inputParsed: unknown, outputParsed: unknown;
    try {
      inputParsed = JSON.parse(inputSchema);
      outputParsed = JSON.parse(outputSchema);
    } catch (e) {
      setError(new ApiError({ status: 0, error: "local_json", detail: `Schema JSON 语法错误：${e instanceof Error ? e.message : e}`, code: "", traceId: "", fieldErrors: {}, raw: null }));
      setBusy(false);
      return;
    }
    const manifest: ServiceManifest = {
      service_id: serviceId.trim(),
      name: name.trim(),
      description: desc.trim(),
      version,
      provider: {
        agent_id: registered?.agent_id ?? 0,
        wallet: revenueWallet.trim(),
        display_name: registered?.display_name ?? "",
      },
      endpoint: {
        type: endpointType,
        url: endpointType === "http_json" ? endpointUrl.trim() : null,
        timeout_ms: timeoutMs,
      },
      pricing: { token: "USDT", model: "per_call", amount, amount_raw: conv.ok ? conv.raw.toString() : "0" },
      chain: { network: CHAIN_ID },
      input_schema: inputParsed as Record<string, unknown>,
      output_schema: outputParsed as Record<string, unknown>,
      status: "active",
    };
    try {
      const ack = await coreApi.publishManifest(manifest);
      setOk({ service_id: ack.service_id, manifest_hash: ack.manifest_hash });
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e
          : new ApiError({ status: 0, error: "unknown", detail: humanizeError(e).title, code: "", traceId: "", fieldErrors: {}, raw: e })
      );
    } finally {
      setBusy(false);
    }
  };

  if (!registered) {
    return (
      <div className="card">
        <WarnBox>请先完成第 ① 步登记（需要 agent_id 与展示名称），发布表单会自动带上 Provider 信息。</WarnBox>
        <button className="btn secondary" onClick={onBack}>
          ← 回到登记
        </button>
      </div>
    );
  }

  const urlInvalid = endpointType === "http_json" && !/^https?:\/\//.test(endpointUrl.trim());

  return (
    <div className="card">
      <h3>发布服务（ServiceManifest 表单）</h3>
      <p className="card-desc">
        Provider：agent_id={registered.agent_id}「{registered.display_name}」
        {identityWallet ? ` · 链上身份钱包 ${identityWallet.slice(0, 10)}…` : " · 正在读取链上身份钱包…"}
      </p>

      <div className="field">
        <label>服务收款钱包（收入到账地址）</label>
        <input
          type="text"
          className={!revenueWalletValid || fieldErr("provider.wallet") ? "invalid" : ""}
          value={revenueWallet}
          placeholder={wctx.address ? "" : "0x…（未连接钱包——先连接，或手动填写）"}
          onChange={(e) => setRevenueWallet(e.target.value.trim())}
        />
        <div className="help">
          付费调用的收入将进入此地址（PayVault Charged 记账键），与身份钱包相互独立；默认=你当前连接的钱包
          {wctx.address ? `（${wctx.address.slice(0, 10)}…）` : "（当前未连接——可先到消费端工作台 ① 连接，或手填一个地址）"}。
        </div>
        {walletDiffers && (
          <div className="help" style={{ color: "var(--warn)" }}>
            注意：此地址与链上身份钱包（{identityWallet!.slice(0, 10)}…）不同——收入只进上面的服务收款钱包；若想用身份钱包收款，请到第 ①/④ 步把身份钱包绑成同一地址。
          </div>
        )}
        {fieldErr("provider.wallet") && (
          <div className="err" style={{ color: "var(--danger)", fontSize: 12 }}>服务端：{fieldErr("provider.wallet")}</div>
        )}
      </div>

      <div className="field">
        <label>服务 ID（全局唯一 slug）</label>
        <input type="text" className={fieldErr("service_id") ? "invalid" : ""} value={serviceId} placeholder="例如 svc_my_translate" onChange={(e) => setServiceId(e.target.value)} />
        {fieldErr("service_id") && <div className="err" style={{ color: "var(--danger)", fontSize: 12 }}>{labelField("service_id")}：{fieldErr("service_id")}</div>}
      </div>
      <div className="field">
        <label>服务名称</label>
        <input type="text" className={fieldErr("name") ? "invalid" : ""} value={name} placeholder="例如 中英技术翻译" onChange={(e) => setName(e.target.value)} />
        {fieldErr("name") && <div className="err" style={{ color: "var(--danger)", fontSize: 12 }}>{fieldErr("name")}</div>}
      </div>
      <div className="field">
        <label>描述（可空）</label>
        <input type="text" value={desc} onChange={(e) => setDesc(e.target.value)} />
      </div>
      <div className="field">
        <label>版本号</label>
        <input type="text" value={version} onChange={(e) => setVersion(e.target.value)} />
        <div className="help">semantic version；改价/改状态重新发布时建议递增。</div>
      </div>

      <AmountInput human={amount} onHumanChange={setAmount} fieldError={fieldErr("amount") ?? fieldErr("amount_raw") ?? fieldErr("pricing")} />

      <div className="field">
        <label>端点类型</label>
        <div className="seg">
          <button className={endpointType === "internal" ? "active" : ""} onClick={() => setEndpointType("internal")}>
            internal（平台内置实现）
          </button>
          <button className={endpointType === "http_json" ? "active" : ""} onClick={() => setEndpointType("http_json")}>
            http_json（自运营 URL）
          </button>
        </div>
        {endpointType === "http_json" && (
          <div style={{ marginTop: 10 }}>
            <input type="text" className={urlInvalid || fieldErr("endpoint.url") ? "invalid" : ""} value={endpointUrl} placeholder="https://your-host/endpoint（POST JSON→JSON）" onChange={(e) => setEndpointUrl(e.target.value)} />
            <div className="help">网关会以 POST JSON 代理调用该 URL；请确保公网可达且返回 2xx。</div>
          </div>
        )}
        {endpointType === "internal" && <div className="help">internal 端点由平台内置模块实现（无需 URL），适合演示与兜底。</div>}
      </div>

      <div className="field">
        <label>超时 timeout_ms</label>
        <input type="number" value={timeoutMs} onChange={(e) => setTimeoutMs(Number(e.target.value) || 30000)} />
      </div>

      <JsonEditor label="input_schema（消费端参数校验，决定调用表单）" value={inputSchema} onChange={setInputSchema} fieldError={fieldErr("input_schema")} rows={8} />
      <JsonEditor label="output_schema" value={outputSchema} onChange={setOutputSchema} fieldError={fieldErr("output_schema")} rows={5} />

      {error && <ErrorBox error={error} />}
      {ok && (
        <SuccessBox>
          已发布 <b>{ok.service_id}</b>（manifest_hash <span className="mono">{ok.manifest_hash.slice(0, 20)}…</span>），目录立即可见，状态 active。
        </SuccessBox>
      )}

      <div className="btn-row">
        <button className="btn secondary" onClick={onBack}>
          ← 上一步
        </button>
        <button className="btn" disabled={busy || !conv.ok || urlInvalid || !serviceId.trim() || !name.trim() || !revenueWalletValid} onClick={submit}>
          {busy ? <Spinner label="发布中…" /> : "发布服务"}
        </button>
        <button className="btn secondary" disabled={!ok} onClick={onNext}>
          下一步：我的服务 →
        </button>
      </div>
    </div>
  );
}

/* ============ 步骤 3：我的服务管理 ============ */
function ManageStep({ agentId, onNext, onBack }: { agentId: number | null; onNext: () => void; onBack: () => void }) {
  const catalog = useAsync(() => coreApi.catalog(), [], { pollMs: 20_000 });
  const [confirm, setConfirm] = useState<null | { kind: "status" | "price"; svc: ServiceManifest; nextStatus?: string; nextAmount?: string }>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [editing, setEditing] = useState<Record<string, string>>({});

  const mine = (catalog.data?.services ?? []).filter((s) => agentId == null || s.manifest.provider.agent_id === agentId);

  const repost = async (svc: ServiceManifest, patch: Partial<ServiceManifest>) => {
    setBusy(true);
    setError(null);
    try {
      const next: ServiceManifest = {
        ...svc,
        ...patch,
        version: bumpVersion(svc.version),
      };
      if (patch.pricing) next.pricing = { ...svc.pricing, ...patch.pricing };
      await coreApi.publishManifest(next);
      setConfirm(null);
      catalog.reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <div className="flex" style={{ justifyContent: "space-between" }}>
        <h3>我的服务{agentId ? `（agent_id=${agentId}）` : "（未指定 agent_id，显示全部）"}</h3>
        <button className="btn small secondary" onClick={catalog.reload}>
          刷新
        </button>
      </div>
      <p className="card-desc">状态切换与改价都是「重新提交 manifest」：改价立即对新调用生效；paused 服务调用会 404。</p>
      {error != null && <ErrorBox error={error} />}
      <AsyncSection state={catalog} empty="目录里还没有你的服务——回到第 ② 步发布">
        {() => (
          <table className="list">
            <thead>
              <tr>
                <th>服务</th>
                <th>状态</th>
                <th>定价</th>
                <th>改价（USDT）</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {mine.map((s) => {
                const m = s.manifest;
                const newAmount = editing[s.service_id] ?? m.pricing.amount;
                let newRaw: bigint | null = null;
                try {
                  newRaw = toRaw(newAmount);
                } catch {
                  newRaw = null;
                }
                const priceChanged = newRaw !== null && newRaw.toString() !== m.pricing.amount_raw;
                return (
                  <tr key={s.service_id}>
                    <td>
                      <div>
                        <b>{m.name}</b>
                      </div>
                      <div className="mono dim">{s.service_id} · v{m.version}</div>
                    </td>
                    <td>
                      <Badge kind={s.status === "active" ? "ok" : "warn"}>{s.status}</Badge>
                    </td>
                    <td className="num">
                      {m.pricing.amount}
                      <div className="dim num">raw={m.pricing.amount_raw}</div>
                    </td>
                    <td>
                      <input
                        type="text"
                        style={{ width: 110 }}
                        value={newAmount}
                        onChange={(e) => setEditing((ed) => ({ ...ed, [s.service_id]: e.target.value }))}
                      />
                      {newRaw !== null && <div className="dim num">→ raw {newRaw.toString()}{priceChanged ? "（已变更）" : ""}</div>}
                    </td>
                    <td>
                      <div className="btn-row">
                        <button className="btn small secondary" disabled={busy} onClick={() => setConfirm({ kind: "status", svc: m, nextStatus: s.status === "active" ? "paused" : "active" })}>
                          {s.status === "active" ? "暂停（paused）" : "恢复（active）"}
                        </button>
                        <button className="btn small" disabled={busy || !priceChanged} onClick={() => setConfirm({ kind: "price", svc: m, nextAmount: newAmount! })}>
                          应用改价
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </AsyncSection>

      <ConfirmDialog
        open={confirm !== null}
        title={confirm?.kind === "status" ? `确认${confirm.nextStatus === "paused" ? "暂停" : "恢复"}服务` : "确认改价"}
        body={
          confirm?.kind === "status" ? (
            <div>
              服务 <b>{confirm.svc.name}</b>（{confirm.svc.service_id}）将切换为 <b>{confirm.nextStatus}</b>。
              {confirm.nextStatus === "paused" ? "暂停后消费者调用会得到 404（不产生计费）。" : "恢复后立即可调用。"}
              该操作会重新提交 manifest，版本号自动递增。
            </div>
          ) : (
            <div>
              服务 <b>{confirm?.svc.name}</b>（{confirm?.svc.service_id}）定价 {confirm?.svc.pricing.amount} →{" "}
              <b>{confirm?.nextAmount}</b> USDT（raw {confirm?.svc.pricing.amount_raw} →{" "}
              {confirm ? toRaw(confirm.nextAmount!).toString() : ""}）。新定价立即对后续调用生效。
            </div>
          )
        }
        onConfirm={() => {
          if (!confirm) return;
          if (confirm.kind === "status") {
            void repost(confirm.svc, { status: confirm.nextStatus! });
          } else {
            void repost(confirm.svc, { pricing: { ...confirm.svc.pricing, amount: confirm.nextAmount!, amount_raw: toRaw(confirm.nextAmount!).toString() } });
          }
        }}
        onCancel={() => setConfirm(null)}
      />

      <div className="btn-row" style={{ marginTop: 16 }}>
        <button className="btn secondary" onClick={onBack}>
          ← 上一步
        </button>
        <button className="btn secondary" onClick={onNext}>
          进阶：身份钱包绑定 →
        </button>
      </div>
    </div>
  );
}

function bumpVersion(v: string): string {
  const parts = v.split(".").map((x) => Number(x) || 0);
  parts[2] = (parts[2] ?? 0) + 1;
  return parts.join(".");
}

/* ============ 步骤 4：身份钱包绑定 ============ */
function BindStep({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const [tokenId, setTokenId] = useState("162");
  const [newWallet, setNewWallet] = useState("");
  const [mode, setMode] = useState<"injected" | "paste">("injected");
  const [pastedSig, setPastedSig] = useState("");
  const [deadline, setDeadline] = useState<number | null>(null);
  const [countdown, setCountdown] = useState(0);
  const [typedData, setTypedData] = useState<ReturnType<typeof agentWalletSetTypedData> | null>(null);
  const [signature, setSignature] = useState<string | null>(null);
  const [cancelled, setCancelled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<{ tx_hash: string } | null>(null);
  const [needRebuild, setNeedRebuild] = useState(false);

  const identity = useAsync<AgentIdentity | null>(
    () => (Number.isInteger(Number(tokenId)) && Number(tokenId) > 0 ? botChainApi.identity(Number(tokenId)).catch(() => null) : Promise.resolve(null)),
    [tokenId]
  );

  // 倒计时
  useEffect(() => {
    if (deadline == null) return;
    const t = setInterval(() => {
      const left = deadline - Math.floor(Date.now() / 1000);
      setCountdown(left);
      if (left <= 0) setNeedRebuild(true);
    }, 1000);
    return () => clearInterval(t);
  }, [deadline]);

  const build = useCallback(() => {
    if (!identity.data) return;
    const dl = Math.floor(Date.now() / 1000) + 300; // 链上窗口 [now, now+300s]
    const td = agentWalletSetTypedData({
      agentId: Number(tokenId),
      newWallet: newWallet.trim(),
      owner: identity.data.owner,
      deadline: dl,
      verifyingContract: IDENTITY_REGISTRY,
      chainId: CHAIN_ID,
    });
    setTypedData(td);
    setDeadline(dl);
    setCountdown(dl - Math.floor(Date.now() / 1000));
    setSignature(null);
    setNeedRebuild(false);
    setError(null);
  }, [identity.data, newWallet, tokenId]);

  const wctx = useWallet();

  const signWithInjected = async () => {
    setBusy(true);
    setError(null);
    setCancelled(false);
    try {
      // 与消费端同一共享路径：用户选中的 provider → BrowserProvider.signTypedData（digest 由扩展计算）
      const sel = await wctx.requireProvider();
      if (!sel) throw new Error("未选择浏览器钱包。");
      const signer = await browserProvider(sel.provider).getSigner(newWallet.trim());
      const sig = await signer.signTypedData(typedData!.domain, typedData!.types, typedData!.message);
      setSignature(sig);
    } catch (e) {
      if (isUserRejected(e)) setCancelled(true);
      else setError(e);
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await botChainApi.bindWallet(Number(tokenId), newWallet.trim(), (mode === "injected" ? signature : pastedSig.trim())!, deadline!);
      setResult({ tx_hash: r.tx_hash });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const sigValid = /^0x[0-9a-fA-F]{130}$/.test((mode === "injected" ? signature ?? "" : pastedSig));
  const expired = deadline != null && countdown <= 0;

  return (
    <div className="card">
      <h3>身份钱包绑定（进阶 · 上链，setAgentWallet）</h3>
      <p className="card-desc">
        setAgentWallet(agentId, newWallet, deadline, signature)：把身份钱包（agentWallet）换成新地址。签名者是<b>新钱包本人</b>（EIP-712，域 ERC8004IdentityRegistry/1/{CHAIN_ID}/代理 {IDENTITY_REGISTRY.slice(0, 8)}…），
        deadline 链上窗口 5 分钟。
      </p>

      <div className="field">
        <label>Token ID（agentId）</label>
        <input type="number" value={tokenId} onChange={(e) => setTokenId(e.target.value)} />
        {identity.data && (
          <div className="help">
            当前身份：owner <span className="mono">{identity.data.owner}</span>（平台代管账户）· 身份钱包 <span className="mono">{identity.data.agent_wallet}</span>
          </div>
        )}
      </div>
      <div className="field">
        <label>新身份钱包地址</label>
        <input type="text" value={newWallet} placeholder="0x…（签名必须用这个地址的私钥/钱包完成）" onChange={(e) => setNewWallet(e.target.value)} className={newWallet && !/^0x[0-9a-fA-F]{40}$/.test(newWallet.trim()) ? "invalid" : ""} />
      </div>

      <div className="btn-row" style={{ marginBottom: 16 }}>
        <button className="btn" disabled={!identity.data || !/^0x[0-9a-fA-F]{40}$/.test(newWallet.trim())} onClick={build}>
          生成待签数据
        </button>
        {typedData && (
          <Badge kind={expired ? "err" : countdown < 60 ? "warn" : "ok"}>
            {expired ? "已过期（请重新生成并签名）" : `签名窗口剩 ${Math.floor(countdown / 60)}:${String(countdown % 60).padStart(2, "0")}`}
          </Badge>
        )}
      </div>

      {typedData && (
        <>
          <div className="section-title">待签 EIP-712 Typed Data</div>
          <pre className="code-box" style={{ padding: 12, borderRadius: 8, overflowX: "auto", maxHeight: 260 }}>{JSON.stringify(typedData, null, 2)}</pre>
          <div className="flex" style={{ marginBottom: 12 }}>
            <CopyButton text={JSON.stringify(typedData)} label="复制 typed data JSON" />
          </div>

          <div className="seg" style={{ marginBottom: 12 }}>
            <button className={mode === "injected" ? "active" : ""} onClick={() => setMode("injected")}>
              A · 注入钱包签名（OKX / MetaMask）
            </button>
            <button className={mode === "paste" ? "active" : ""} onClick={() => setMode("paste")}>
              B · 粘贴本地签名
            </button>
          </div>

          {mode === "injected" ? (
            <div>
              <div className="help" style={{ marginBottom: 8 }}>
                用 <b>新钱包地址</b> 对应的浏览器钱包签名（window.ethereum signTypedData_v4）。钱包里需已导入该地址。
              </div>
              <div className="btn-row">
                <button className="btn" disabled={busy || expired || needRebuild || wctx.candidates.length === 0} onClick={signWithInjected}>
                  {busy ? <Spinner label="等待钱包确认…" /> : "调起钱包签名"}
                </button>
                {wctx.candidates.length === 0 && <span className="dim">未检测到注入钱包——改用 B 模式。</span>}
              </div>
            </div>
          ) : (
            <div className="field">
              <label>本地签名结果（0x + 65 字节）</label>
              <textarea className="code" rows={3} value={pastedSig} placeholder="0x…" onChange={(e) => setPastedSig(e.target.value)} />
              <div className="help">可在任何可信环境用新钱包私钥对上方 typed data 签名后粘贴；私钥不经过本页面。</div>
            </div>
          )}

          {signature && mode === "injected" && (
            <SuccessBox>
              签名完成：<span className="mono">{signature.slice(0, 34)}…</span>（65 字节）——等待提交。
            </SuccessBox>
          )}
          {cancelled && (
            <WarnBox>你取消了签名（钱包弹窗里拒绝）。没有产生任何签名，可重新点「调起钱包签名」。</WarnBox>
          )}

          {error != null && <ErrorBox error={error} />}

          <div className="btn-row" style={{ marginTop: 12 }}>
            <button className="btn danger" disabled={busy || expired || needRebuild || !sigValid} onClick={submit}>
              {busy ? <Spinner label="提交上链…" /> : "提交绑定（dry_run=false，真实上链）"}
            </button>
            {expired && <span style={{ color: "var(--danger)", fontSize: 13 }}>deadline 已过——点「生成待签数据」刷新窗口并重新签名。</span>}
          </div>
        </>
      )}

      {result && (
        <SuccessBox>
          绑定交易已上链：<TxLink hash={result.tx_hash} /> （约几秒后可在身份查询里看到新 agent_wallet）。身份钱包从此指向你自己的地址（发布 http_json 服务前的收款绑定校验口径见帮助页三钱包角色图）。
        </SuccessBox>
      )}

      <div className="btn-row" style={{ marginTop: 16 }}>
        <button className="btn secondary" onClick={onBack}>
          ← 上一步
        </button>
        <button className="btn secondary" onClick={onNext}>
          下一步：提现视图 →
        </button>
      </div>
    </div>
  );
}

/* ============ 步骤 5：提现 ============ */
function WithdrawStep() {
  const [addr, setAddr] = useState("");
  const [credits, setCredits] = useState<bigint | null>(null);
  const [busy, setBusy] = useState(false);
  const [waitingWallet, setWaitingWallet] = useState(false);
  const [cancelled, setCancelled] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [confirming, setConfirming] = useState(false);
  const [tx, setTx] = useState<string | null>(null);

  const query = async () => {
    setBusy(true);
    setError(null);
    setTx(null);
    try {
      setCredits(await fetchProviderCredits(addr.trim()));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const wctx = useWallet();

  const withdraw = async () => {
    setBusy(true);
    setWaitingWallet(true);
    setError(null);
    setCancelled(false);
    try {
      // 与消费端同一共享路径：用户选中的 provider → 先确保在 968 链，再 eth_sendTransaction（20 gwei 固定费率）
      const sel = await wctx.requireProvider();
      if (!sel) throw new Error("未选择浏览器钱包。");
      await ensureChain968(sel.provider);
      const from = (await silentAccounts(sel.provider)) ?? (await connectInjected(sel.provider)).address;
      const hash = await sendInjectedTx(sel.provider, from, VAULT_ADDR, encodeAddrUint(SEL_C.providerWithdraw, addr.trim(), credits!));
      setWaitingWallet(false);
      setTx(hash);
      await waitForInjectedReceipt(hash);
      const c = await fetchProviderCredits(addr.trim());
      setCredits(c);
    } catch (e) {
      if (isUserRejected(e)) setCancelled(true);
      else setError(e);
    } finally {
      setBusy(false);
      setWaitingWallet(false);
      setConfirming(false);
    }
  };

  return (
    <div className="card">
      <h3>提现视图（PayVault credits → 你的钱包）</h3>
      <p className="card-desc">
        credits 是链上 PayVault 记账的未提现收入（I4：合约内 USDT 余额恒等于总 credits）。providerWithdraw 只能由钱包本人发起（铁律 P8：路径恒开）。
      </p>
      <div className="field">
        <label>服务收款钱包地址（收入到账地址，持有 credits）</label>
        <input type="text" value={addr} placeholder="0x…（持有 credits 的地址）" onChange={(e) => setAddr(e.target.value)} />
      </div>
      <div className="btn-row">
        <button className="btn" disabled={!/^0x[0-9a-fA-F]{40}$/.test(addr.trim()) || busy} onClick={query}>
          {busy ? <Spinner label="eth_call 查询中…" /> : "查询 credits（eth_call）"}
        </button>
      </div>

      {credits !== null && (
        <div className="alert info">
          该地址在 PayVault 的未提现收入：<b className="num">{fromRaw(credits)} USDT</b>
          <span className="dim num">（raw={credits.toString()}）</span> · 合约 {PAY_VAULT.slice(0, 10)}…
        </div>
      )}
      {error != null && <ErrorBox error={error} />}
      {cancelled && <WarnBox>你取消了提现交易（钱包弹窗里拒绝）。没有产生任何交易，可重新发起。</WarnBox>}
      {waitingWallet && (
        <div className="alert info">
          <span className="flex"><span className="spin" /> 等待钱包确认…（请在扩展弹窗里确认提现交易）</span>
        </div>
      )}
      {tx && (
        <SuccessBox>
          提现交易已发送：<TxLink hash={tx} />
        </SuccessBox>
      )}

      {credits !== null && credits > 0n && (
        wctx.candidates.length > 0 ? (
          <>
            <button className="btn danger" disabled={busy} onClick={() => setConfirming(true)}>
              {busy && waitingWallet ? "等待钱包确认…" : "发起 providerWithdraw（全额）"}
            </button>
            <ConfirmDialog
              open={confirming}
              title="确认上链提现（不可逆）"
              body={
                <div>
                  将从 PayVault 把 <b className="num">{fromRaw(credits)} USDT</b>（raw={credits.toString()}）提现到{" "}
                  <span className="mono">{addr.trim()}</span>。交易由注入钱包（该地址本人）发送，上链后不可撤销。
                </div>
              }
              confirmText="确认提现"
              onConfirm={withdraw}
              onCancel={() => setConfirming(false)}
            />
          </>
        ) : (
          <WarnBox>
            检测到可提现余额，但本浏览器没有注入钱包。操作指引：① 在 OKX/MetaMask 中导入该服务收款钱包的账户；② 切到 BOT Chain
            （chainId 968，RPC https://rpc.bohr.life/）；③ 刷新本页后点击「发起 providerWithdraw」。也可用任意脚本以该钱包调用
            <span className="mono"> PayVault({PAY_VAULT.slice(0, 10)}…).providerWithdraw(to, amount)</span>。
          </WarnBox>
        )
      )}
      {credits === 0n && <div className="dim">该地址暂无可提现 credits（keeper 结算入账后这里会有数字——去总览页看结算观测）。</div>}
    </div>
  );
}
