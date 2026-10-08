/**
 * Provider 工作台（认证先行，四步向导）：认领身份（AgentWalletSet 绑定 + 登记 One-shot）→
 * 发布服务 → 我的服务 → 提现。步骤间状态保持（父级 state），进度指示可点击回跳。
 */
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { coreApi, credentialsApi, teamCredentialsApi, teamsApi, type MyTeam, type ServiceManifest } from "../api/core";
import { CredentialHeadersEditor, rowsToHeaders, type CredentialRow } from "../components/CredentialHeadersEditor";
import { ExampleRequestEditor } from "../components/ExampleRequestEditor";
import { ProbeDialog } from "../components/ProbeDialog";
import { schemaHasNestedObjects, type ExampleParam } from "../lib/schema-infer";
import { agentWalletSetTypedData, botChainApi, type AgentIdentity } from "../api/gateway";
import { ApiError } from "../api/client";
import { CHAIN_ID, IDENTITY_REGISTRY, PAY_VAULT as VAULT_ADDR, SEL as SEL_C, PAY_VAULT, RPC_URL, EXPLORER_HOST, EXPLORER_URL, fromRaw, toRaw } from "../chain/constants";
import { fetchProviderCredits, encodeAddrUint } from "../chain/rpc";
import { browserProvider, isUserRejected, sendInjectedTx, waitForInjectedReceipt, connectInjected, ensureBotChain, silentAccounts } from "../chain/injected";
import { useWallet } from "../state/WalletContext";
import { CUSTODIAN } from "../lib/consts-extra";
import { AmountInput } from "../components/AmountInput";
import { ConnectWalletButton } from "../components/ConnectWalletButton";
import { JsonEditor } from "../components/JsonEditor";
import { AsyncSection, Badge, ConfirmDialog, Empty, ErrorBox, Spinner, SuccessBox, TxLink, WarnBox } from "../components/ui";
import { EvidencePair, PageHeader, StatCard } from "../components/shell";
import { humanizeError, labelField } from "../lib/errors";
import { useAsync } from "../lib/useAsync";
import { useMode } from "../state/ModeContext";

export default function ProviderWorkbench() {
  // 身份只能由 /welcome 选择卡设定：已连接但未选身份 → 送回选择页（禁止直达替用户选择）
  const { mode } = useMode();
  const pw = useWallet();
  const needsChoice = pw.address != null && !mode;
  // 互斥：Consumer 身份硬闯 Provider 台 → 弹回自己的台（换身份走顶栏 pill）
  useEffect(() => {
    if (mode === "consumer") window.location.hash = "#/consumer";
  }, [mode]);
  useEffect(() => {
    if (needsChoice) window.location.hash = "#/welcome";
  }, [needsChoice]);
  if (needsChoice) return null;

  // 仪表盘三层：收入 Grid → 我的 Teams → Team 下钻；发布为覆盖层
  const [publishing, setPublishing] = useState<{ agent_id: number; display_name: string; wallet: string } | null>(null);
  const [teamPage, setTeamPage] = useState<number | null>(null);

  const startPublish = (t: { agent_id: number; display_name: string; claim_wallet?: string | null }) => {
    setPublishing({ agent_id: t.agent_id, display_name: t.display_name, wallet: t.claim_wallet ?? "" });
  };

  if (publishing) {
    return (
      <div>
        <PageHeader
          mode="provider"
          title="发布新服务"
          sub={
            <span>
              团队 <b>{publishing.display_name}</b> · 服务收款钱包默认 = 认领钱包
            </span>
          }
          actions={
            <button className="btn secondary" onClick={() => setPublishing(null)}>
              ← 返回仪表盘
            </button>
          }
        />
        <PublishStep
          claimed={publishing}
          onNext={() => setPublishing(null)}
          onBack={() => setPublishing(null)}
        />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        mode="provider"
        title="Provider 仪表盘"
        sub="收入总览 → 我的 Teams → 团队下钻。所有上链动作都有二次确认与交易外链。"
      />

      <RevenueGrid onWithdrawn={undefined} />

      {teamPage == null ? (
        <MyTeamsStep
          onOpenTeam={(id) => setTeamPage(id)}
          onPublish={startPublish}
        />
      ) : (
        <TeamHome
          agentId={teamPage}
          onBack={() => setTeamPage(null)}
          onPublish={startPublish}
          onRenamed={undefined}
        />
      )}
    </div>
  );
}

/* ============ 步骤 1：登记 ============ */
/**
 * ① 认领身份（认证先行）：连接钱包 → agent_id 防抖预检 claim-state → 四态：
 *   a 身份不存在 → 注册闭环（铸造→自动绑定为连接钱包→绿态认领）；
 *   b agentWallet/认领者=我的地址 → 绿，可直接提交认领（POST /providers 带 claim_wallet）；
 *   c agentWallet=平台托管且无人认领 → 黄，「绑定我的钱包并认领」一键链（AgentWalletSet 签名→自动登记）；
 *   d 被他人认领/他人非托管绑定 → 红死路（明示，不给操作）。
 */

export function PublishStep({ claimed, onNext, onBack }: { claimed: { agent_id: number; display_name: string; wallet: string } | null; onNext: () => void; onBack: () => void }) {
  const wctx = useWallet();
  const [serviceId, setServiceId] = useState("");
  // 服务收款钱包（manifest.provider.wallet）：收入实际到账地址；默认=当前连接的钱包
  const [revenueWallet, setRevenueWallet] = useState("");
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [amount, setAmount] = useState("0.01");
  const [endpointType, setEndpointType] = useState<"internal" | "http_json">("internal");
  const [endpointMethod, setEndpointMethod] = useState<"GET" | "POST">("POST");
  const [exampleParams, setExampleParams] = useState<ExampleParam[]>([{ name: "", value: "" }]);
  const [exampleJson, setExampleJson] = useState('{\n  "text": "hello"\n}');
  const [probeOpen, setProbeOpen] = useState(false);
  const [outputAutoNote, setOutputAutoNote] = useState(false);
  const [inputAutoNote, setInputAutoNote] = useState(false);
  const [endpointUrl, setEndpointUrl] = useState("");
  const [timeoutMs, setTimeoutMs] = useState(30000);
  const [inputSchema, setInputSchema] = useState('{\n  "type": "object",\n  "properties": {\n    "text": { "type": "string" }\n  },\n  "required": ["text"]\n}');
  const [outputSchema, setOutputSchema] = useState('{\n  "type": "object"\n}');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [ok, setOk] = useState<{ service_id: string; manifest_hash: string } | null>(null);
  // 上游认证头（仅 http_json）：发布成功后链式 PUT credentials
  const [credRows, setCredRows] = useState<CredentialRow[]>([]);
  // 团队默认认证头（网关回退）：已配则开关默认开（复用，不发服务级 PUT）
  const [teamCredNames, setTeamCredNames] = useState<string[] | null>(null);
  const [teamCredLoaded, setTeamCredLoaded] = useState(false);
  const [useTeamCred, setUseTeamCred] = useState(true);
  const [credState, setCredState] = useState<{ kind: "ok" | "warn"; names: string[] } | null>(null);
  const [credError, setCredError] = useState<unknown>(null);

  const fieldErr = (f: string) => error?.fieldErrors?.[f];

  const identity = useAsync<AgentIdentity | null>(
    () => (claimed ? botChainApi.identity(claimed.agent_id).catch(() => null) : Promise.resolve(null)),
    [claimed?.agent_id]
  );

  // 团队默认认证头名（决定复用开关默认态）
  useEffect(() => {
    if (!claimed) return;
    let alive = true;
    teamCredentialsApi
      .list(claimed.agent_id)
      .then((info) => {
        if (!alive) return;
        setTeamCredNames(info.header_names);
        setTeamCredLoaded(true);
        setUseTeamCred(info.header_names.length > 0); // 团队已配 → 默认开
      })
      .catch(() => {
        if (alive) setTeamCredLoaded(true);
      });
    return () => {
      alive = false;
    };
  }, [claimed?.agent_id]);

  // 默认=认领钱包（认证先行：收入默认进经过认证的钱包）；认领后又连接了别的钱包不自动覆盖手填值
  useEffect(() => {
    if (!revenueWallet && claimed?.wallet) setRevenueWallet(claimed.wallet);
  }, [claimed?.wallet, revenueWallet]);
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
        agent_id: claimed?.agent_id ?? 0,
        wallet: revenueWallet.trim(),
        display_name: claimed?.display_name ?? "",
      },
      endpoint: {
        type: endpointType,
        url: endpointType === "http_json" ? endpointUrl.trim() : null,
        timeout_ms: timeoutMs,
        ...(endpointType === "http_json" ? { method: endpointMethod } : {}),
      },
      pricing: { token: "USDT", model: "per_call", amount, amount_raw: conv.ok ? conv.raw.toString() : "0" },
      chain: { network: CHAIN_ID },
      input_schema: inputParsed as Record<string, unknown>,
      output_schema: outputParsed as Record<string, unknown>,
      status: "active",
    };
    setCredState(null);
    setCredError(null);
    try {
      const ack = await coreApi.publishManifest(manifest);
      setOk({ service_id: ack.service_id, manifest_hash: ack.manifest_hash });
      // 链式保存上游认证头：复用团队头（开关开）→ 留空回退不发；否则填了才 PUT（失败不回滚 manifest）
      const headers = useTeamCred && teamCredNames != null && teamCredNames.length > 0 ? {} : rowsToHeaders(credRows);
      if (Object.keys(headers).length > 0) {
        try {
          const info = await credentialsApi.put(ack.service_id, headers);
          setCredState({ kind: "ok", names: info.header_names });
        } catch (ce) {
          setCredError(ce);
          setCredState({ kind: "warn", names: Object.keys(headers) });
        }
      }
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

  if (!claimed) {
    return (
      <div className="card">
        <WarnBox>请先从「我的 Teams」选择或创建一个团队——发布表单会自动带上团队信息与认领钱包。</WarnBox>
        <button className="btn secondary" onClick={onBack}>
          ← 回到登记
        </button>
      </div>
    );
  }

  const urlInvalid = endpointType === "http_json" && !/^https?:\/\//.test(endpointUrl.trim());

  return (
    <div>
      {/* 服务收款钱包（收入锚点，放最前） */}
      <div className="card form-group-card">
        <h3>服务收款钱包</h3>
        <p className="card-desc">付费调用的收入将进入此地址（PayVault Charged 记账键），与身份钱包相互独立。</p>
        <div className="field">
          <label>服务收款钱包（收入到账地址）</label>
          <div className="flex">
            <div className="grow">
              <input
                type="text"
                className={!revenueWalletValid || fieldErr("provider.wallet") ? "invalid" : ""}
                value={revenueWallet}
                placeholder="0x…（连接钱包自动填入，或手动填写）"
                onChange={(e) => setRevenueWallet(e.target.value.trim())}
                aria-label="服务收款钱包地址"
              />
            </div>
            {!wctx.address ? (
              <ConnectWalletButton size="small" label="连接钱包自动填" title="连接后此字段自动填入你的钱包地址" />
            ) : (
              <button
                type="button"
                className="btn small"
                onClick={() => setRevenueWallet(wctx.address!)}
                title="把字段设为当前连接的钱包地址（手动改过也能一键填回）"
              >
                使用当前钱包 {wctx.address.slice(0, 6)}…
              </button>
            )}
          </div>
          <div className="help">连接钱包后默认自动填入你的地址（默认=认领钱包 {claimed.wallet.slice(0, 10)}…），也可手动填写任意地址。</div>
          {walletDiffers && (
            <div className="help" style={{ color: "var(--warn)" }}>
              注意：此地址与链上身份钱包（{identityWallet!.slice(0, 10)}…）不同——收入只进上面的服务收款钱包。
            </div>
          )}
          {fieldErr("provider.wallet") && (
            <div className="err" style={{ color: "var(--danger)", fontSize: 12 }}>服务端：{fieldErr("provider.wallet")}</div>
          )}
        </div>
      </div>

      {/* 基本信息 */}
      <div className="card form-group-card">
        <h3>基本信息</h3>
        <div className="field">
          <label>所属团队（只读）</label>
          <input type="text" value={`${claimed.display_name} · team #${claimed.agent_id}`} disabled readOnly aria-label="所属团队" />
          <div className="help">团队由「我的 Teams」选择；发布后 provider.agent_id 自动指向该团队。</div>
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
      </div>

      {/* 定价 */}
      <div className="card form-group-card">
        <h3>定价</h3>
        <AmountInput human={amount} onHumanChange={setAmount} fieldError={fieldErr("amount") ?? fieldErr("amount_raw") ?? fieldErr("pricing")} />
      </div>

      {/* 上游认证头（已上移至端点上方；团队回退开关） */}
      {endpointType === "http_json" && (
        <div className="card form-group-card">
          <h3>上游认证头</h3>
          {teamCredNames != null && teamCredNames.length > 0 ? (
            <div className="field">
              <label className="flex" style={{ cursor: "pointer" }}>
                <input type="checkbox" checked={useTeamCred} onChange={(e) => setUseTeamCred(e.target.checked)} aria-label="默认复用团队认证头" style={{ width: "auto", marginRight: 8 }} />
                默认复用团队认证头
              </label>
              {useTeamCred ? (
                <div className="alert ok" style={{ fontSize: 13, marginTop: 8 }}>
                  将复用团队默认头：{teamCredNames.map((n) => (
                    <span key={n} className="badge ok" style={{ marginLeft: 4 }}>{n} ✓</span>
                  ))}
                  <span className="dim">（在团队详情管理；本服务不单独配置，网关自动回退团队头）</span>
                </div>
              ) : (
                <>
                  <div className="help">开关已关——下方为服务级凭证，优先于团队默认头。</div>
                  <CredentialHeadersEditor rows={credRows} onChange={(rows) => { setCredRows(rows); }} />
                  <div className="help">
                    此密钥<b>加密存储于平台</b>、仅网关转发你的上游 URL 时使用；不会出现在目录或公开 manifest 中。留空 = 不配置（回退团队默认头）。
                  </div>
                </>
              )}
            </div>
          ) : (
            <>
              {teamCredLoaded && (
                <div className="alert warn" style={{ fontSize: 13 }}>
                  团队还没有默认认证头，可在团队详情 →「团队默认认证头」配置后零配置复用。下方为服务级凭证：
                </div>
              )}
              <CredentialHeadersEditor rows={credRows} onChange={(rows) => { setCredRows(rows); }} />
              <div className="help">
                此密钥<b>加密存储于平台</b>、仅网关转发你的上游 URL 时使用；不会出现在目录或公开 manifest 中。留空 = 不配置。
              </div>
            </>
          )}
        </div>
      )}

      {/* 端点 */}
      <div className="card form-group-card">
        <h3>端点</h3>
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
          {endpointType === "internal" && <div className="help">internal 端点由平台内置模块实现（无需 URL），适合演示与兜底。</div>}
        </div>
        {endpointType === "http_json" && (
          <>
            <div className="field">
              <label>上游 URL</label>
              <input type="text" className={urlInvalid || fieldErr("endpoint.url") ? "invalid" : ""} value={endpointUrl} placeholder="https://your-host/endpoint" onChange={(e) => setEndpointUrl(e.target.value)} />
              <div className="help">网关会代理调用该 URL；请确保公网可达。</div>
            </div>
            <div className="field">
              <label>请求方式</label>
              <div className="flex">
                <div className="seg">
                  <button type="button" className={endpointMethod === "POST" ? "active" : ""} onClick={() => setEndpointMethod("POST")}>
                    POST（默认）
                  </button>
                  <button type="button" className={endpointMethod === "GET" ? "active" : ""} onClick={() => setEndpointMethod("GET")}>
                    GET
                  </button>
                </div>
                <button type="button" className="btn small secondary" onClick={() => setProbeOpen(true)} disabled={!/^https?:\/\//.test(endpointUrl.trim())}>
                  探测接口
                </button>
              </div>
              {endpointMethod === "GET" && (
                <div className="alert info" style={{ marginTop: 8, fontSize: 13 }}>
                  GET 模式：消费者仍 POST JSON 给网关，网关把参数映射成上游 query——仅支持<b>标量与标量数组</b>（嵌套对象请用 POST）。
                </div>
              )}
            </div>
            <div className="field">
              <label>超时 timeout_ms</label>
              <input type="number" value={timeoutMs} onChange={(e) => setTimeoutMs(Number(e.target.value) || 30000)} />
            </div>
          </>
        )}
      </div>

      {/* Schema 与探测 */}
      <div className="card form-group-card">
        <h3>Schema 与探测</h3>
        {endpointType === "http_json" && endpointMethod === "GET" && schemaHasNestedObjects(inputSchema) && (
          <div className="alert warn">
            <b>GET 模式不支持嵌套对象参数</b>：input_schema 里有 type 为 object 的属性——发布会被 422 拒绝。请改用 POST，或把参数拍平为标量/标量数组。
          </div>
        )}
        {endpointType === "http_json" && (
          <details className="raw-detail" style={{ marginBottom: 12 }}>
            <summary style={{ fontSize: 13 }}>示例请求区（探测 / 生成 input_schema 用）——点开编辑</summary>
            <div style={{ marginTop: 8 }}>
              <ExampleRequestEditor method={endpointMethod} params={exampleParams} onParamsChange={setExampleParams} json={exampleJson} onJsonChange={setExampleJson} />
            </div>
          </details>
        )}
        <JsonEditor
          label="input_schema（消费端参数校验，决定调用表单）"
          value={inputSchema}
          onChange={(v) => {
            setInputSchema(v);
            setInputAutoNote(false);
          }}
          fieldError={fieldErr("input_schema")}
          rows={8}
          badge={inputAutoNote ? "自动识别，请核对" : undefined}
        />
        <JsonEditor
          label="output_schema"
          value={outputSchema}
          onChange={(v) => {
            setOutputSchema(v);
            setOutputAutoNote(false);
          }}
          fieldError={fieldErr("output_schema")}
          rows={5}
          badge={outputAutoNote ? "自动识别，请核对" : undefined}
        />
      </div>

      {error && <ErrorBox error={error} />}
      {ok && (
        <SuccessBox>
          已发布 <b>{ok.service_id}</b>（manifest_hash <span className="mono">{ok.manifest_hash.slice(0, 20)}…</span>），目录立即可见，状态 active。
          {credState?.kind === "ok" && (
            <>
              {" "}上游认证头已加密保存：{credState.names.join("、")}（值不回显）。
            </>
          )}
          {ok && useTeamCred && teamCredNames != null && teamCredNames.length > 0 && (
            <div className="alert info" style={{ marginTop: 8, fontSize: 13 }}>
              本服务未单独配置凭证——转发时将自动复用团队默认头（{teamCredNames.join("、")}），在团队详情统一管理。
            </div>
          )}
        </SuccessBox>
      )}
      {ok && credState?.kind === "warn" && (
        <div className="alert warn">
          <b>服务已发布，凭证保存失败</b>（manifest 未回滚）——可在团队详情 → 服务管理重试。
        </div>
      )}
      {ok && credError != null && credState?.kind === "warn" && <ErrorBox error={credError} />}

      {endpointType === "http_json" && (
        <ProbeDialog
          open={probeOpen}
          onClose={() => setProbeOpen(false)}
          initialUrl={endpointUrl.trim()}
          method={endpointMethod}
          params={exampleParams}
          exampleJson={exampleJson}
          formCredHeaders={rowsToHeaders(credRows)}
          onApplySchema={({ outputSchemaText, inputSchemaText }) => {
            if (outputSchemaText) {
              setOutputSchema(outputSchemaText);
              setOutputAutoNote(true);
            }
            if (inputSchemaText) {
              setInputSchema(inputSchemaText);
              setInputAutoNote(true);
            }
            setProbeOpen(false);
          }}
        />
      )}

      {/* 粘性操作条：发布按钮常驻，不必滚到底 */}
      <div className="sticky-action-bar" role="toolbar" aria-label="发布操作">
        <span className="dim" style={{ fontSize: 12 }}>
          {ok ? "✓ 已发布——可返回仪表盘或继续调整" : `${endpointType === "http_json" ? "http_json" : "internal"} · ${amount} USDT/次`}
        </span>
        <div className="btn-row">
          <button className="btn secondary" onClick={onBack}>
            ← 返回仪表盘
          </button>
          <button className="btn" disabled={busy || !conv.ok || urlInvalid || !serviceId.trim() || !name.trim() || !revenueWalletValid} onClick={submit}>
            {busy ? <Spinner label="发布中…" /> : ok ? "重新发布（更新）" : "发布服务"}
          </button>
          <button className="btn secondary" disabled={!ok} onClick={onNext}>
            完成 →
          </button>
        </div>
      </div>
    </div>
  );
}

/* ============ 步骤 3：我的服务管理 ============ */
export function ManageStep({ claimedAgentId, onNext, onBack }: { claimedAgentId: number | null; onNext: () => void; onBack: () => void }) {
  const w = useWallet();
  const catalog = useAsync(() => coreApi.catalog(), [], { pollMs: 20_000 });
  // 认领身份集合的权威来源：GET /providers 过滤 claim_wallet == 我的地址（向导①认领的 agent_id 只作加速兜底）
  const providers = useAsync(() => (w.address ? coreApi.providers() : Promise.resolve(null)), [w.address]);
  const [confirm, setConfirm] = useState<null | { kind: "status" | "price"; svc: ServiceManifest; nextStatus?: string; nextAmount?: string }>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [editing, setEditing] = useState<Record<string, string>>({});
  const [credOpen, setCredOpen] = useState<Record<string, boolean>>({});
  const [editOpen, setEditOpen] = useState<Record<string, boolean>>({});

  // 所有权 = 收款所有权 ∪ 身份所有权：wallet==连接地址，或 agent_id ∈ 我认领的集合
  const my = w.address?.toLowerCase() ?? "";
  const claimedIds = useMemo(() => {
    const set = new Set<number>();
    if (claimedAgentId != null) set.add(claimedAgentId); // ①认领完成即默认纳入（加速；权威仍看 providers.claim_wallet）
    for (const p of providers.data?.providers ?? []) {
      if ((p.claim_wallet ?? "").toLowerCase() === my) set.add(p.agent_id);
    }
    return set;
  }, [providers.data, my, claimedAgentId]);

  const mine = useMemo(
    () =>
      (catalog.data?.services ?? []).filter((s) => {
        const walletHit = s.manifest.provider.wallet?.toLowerCase() === my;
        return walletHit || claimedIds.has(s.manifest.provider.agent_id);
      }),
    [catalog.data, my, claimedIds]
  );

  // 本机管理面内部通道：全量 manifest（含真实上游 url——公开 API 恒脱敏）。只对 mine 集合预取。
  const [fullManifests, setFullManifests] = useState<Record<string, ServiceManifest | null>>({});
  const prefetchQueuedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!catalog.data) return;
    const mineIds = new Set(mine.map((s) => s.service_id));
    for (const s of catalog.data.services) {
      const sid = s.service_id;
      if (!mineIds.has(sid) || fullManifests[sid] !== undefined || prefetchQueuedRef.current.has(sid)) continue;
      prefetchQueuedRef.current.add(sid); // 入队即去重（mine 集合异步扩张时避免重复请求）
      coreApi
        .internalManifest(sid)
        .then((m) => setFullManifests((prev) => ({ ...prev, [sid]: m })))
        .catch(() => setFullManifests((prev) => ({ ...prev, [sid]: null })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog.data, mine]);

  const repost = async (svc: ServiceManifest, patch: Partial<ServiceManifest>): Promise<boolean> => {
    setBusy(true);
    setError(null);
    try {
      const base = fullManifests[svc.service_id] ?? (await coreApi.internalManifest(svc.service_id).catch(() => svc));
      const next: ServiceManifest = {
        ...base,
        ...patch,
        version: bumpVersion(base.version),
      };
      if (patch.pricing) next.pricing = { ...base.pricing, ...patch.pricing };
      await coreApi.publishManifest(next);
      setConfirm(null);
      catalog.reload();
      return true;
    } catch (e) {
      setError(e);
      return false;
    } finally {
      setBusy(false);
    }
  };

  // 前置：所有权以连接钱包为准——未连接只给内联引导，没有全量兜底视图
  if (!w.address) {
    return (
      <div className="card">
        <h3>③ 我的服务</h3>
        <p className="card-desc">「我的服务」= 服务收款钱包是你的、或身份由你认领的服务——需要连接钱包判定所有权。</p>
        <div className="flex">
          <span className="dim">连接后按「收款所有权 ∪ 认领身份」过滤：</span>
          <ConnectWalletButton size="small" />
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="flex" style={{ justifyContent: "space-between" }}>
        <h3>
          我的服务
          <span className="dim" style={{ fontWeight: 400, fontSize: 12, marginLeft: 6 }}>
            服务收款钱包=你 或 身份=你认领（{w.address.slice(0, 8)}…）
          </span>
        </h3>
        <button className="btn small secondary" onClick={catalog.reload}>
          刷新
        </button>
      </div>
      <p className="card-desc">改描述/改价/暂停都是「重新提交 manifest」：描述与 schema 立即对 Agent 目录生效（这是 Agent 选工具时看到的全部信息）；改价对新调用生效；paused 服务调用会 404。</p>
      {error != null && <ErrorBox error={error} />}
      <AsyncSection state={catalog} empty="目录里还没有你的服务——去第 ② 步发布，或确认认领的身份与服务收款钱包">
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
                  <Fragment key={s.service_id}>
                    <tr>
                    <td>
                      <div>
                        <b>{m.name}</b>{" "}
                        {m.endpoint.type === "http_json" && (
                          <span className="badge muted" title="上游请求方式">
                            {m.endpoint.method ?? "POST"}
                          </span>
                        )}
                        {(() => {
                          const walletHit = m.provider.wallet?.toLowerCase() === my;
                          const identityHit = claimedIds.has(m.provider.agent_id);
                          return (
                            <>
                              {walletHit && (
                                <span className="badge ok" title="manifest.provider.wallet = 你的连接钱包">
                                  服务收款钱包=你
                                </span>
                              )}{" "}
                              {identityHit && (
                                <span className="badge info" title="agent_id 在你认领的身份集合里">
                                  身份 #{m.provider.agent_id}=你认领
                                </span>
                              )}
                              {identityHit && !walletHit && (
                                <span className="badge warn" title="他人可能冒用你的 agent_id 发布——收入进的不是你的地址">
                                  ⚠ 此服务收款钱包非你，请核实
                                </span>
                              )}
                            </>
                          );
                        })()}
                      </div>
                      <div className="mono dim">{s.service_id} · v{m.version}</div>
                      {m.description ? (
                        <div className="dim" style={{ fontSize: 12, marginTop: 2 }} title={m.description}>
                          {m.description.length > 90 ? m.description.slice(0, 90) + "…" : m.description}
                        </div>
                      ) : (
                        <div className="dim" style={{ fontSize: 12, marginTop: 2, color: "var(--warn)" }}>
                          无描述——Agent 无法判断这个服务能做什么，点「编辑信息」补齐
                        </div>
                      )}
                      {m.endpoint.type === "http_json" && (
                        <div className="mono dim" style={{ fontSize: 11 }} title="你的真实上游（仅管理面可见，公开目录恒脱敏）">
                          ↳ {fullManifests[s.service_id]?.endpoint.url ?? "…"}
                        </div>
                      )}
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
                        <button
                          className="btn small secondary"
                          onClick={() => {
                            const next = !editOpen[s.service_id];
                            setEditOpen((o) => ({ ...o, [s.service_id]: next }));
                            if (next) setCredOpen((o) => ({ ...o, [s.service_id]: false }));
                          }}
                        >
                          编辑信息
                        </button>
                        {m.endpoint.type === "http_json" && (
                          <button
                            className="btn small secondary"
                            onClick={() => {
                              const next = !credOpen[s.service_id];
                              setCredOpen((o) => ({ ...o, [s.service_id]: next }));
                              if (next) setEditOpen((o) => ({ ...o, [s.service_id]: false }));
                            }}
                          >
                            上游认证头
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                  {editOpen[s.service_id] && (
                    <tr>
                      <td colSpan={5} style={{ background: "var(--surface-2)" }}>
                        <div style={{ padding: "8px 10px" }}>
                          <ServiceInfoEditor
                            svc={m}
                            full={fullManifests[s.service_id] ?? null}
                            busy={busy}
                            onSave={(patch) => repost(m, patch)}
                            onDone={() => setEditOpen((o) => ({ ...o, [s.service_id]: false }))}
                          />
                        </div>
                      </td>
                    </tr>
                  )}
                  {m.endpoint.type === "http_json" && credOpen[s.service_id] && (
                    <tr>
                      <td colSpan={5} style={{ background: "var(--surface-2)" }}>
                        <div style={{ padding: "8px 10px" }}>
                          <ServiceCredentialsPanel serviceId={s.service_id} teamAgentId={m.provider.agent_id} />
                        </div>
                      </td>
                    </tr>
                  )}
                  </Fragment>
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

/** 上游认证头管理面板（仅 http_json；internal 无上游概念不显示）。值永不回显，只有头名。 */
/** 编辑信息面板：名称/描述/双 schema/类目/标签——重提交 manifest（版本自动 +1）。
 *  这些字段是 Agent 选工具时看到的全部信息：描述要写清「能回答什么问题、数据来自哪、入参含义」。 */
const CATEGORY_OPTIONS = ["translation", "data-feed", "on-chain-query", "analysis", "agent-tool", "other"];

function ServiceInfoEditor({ svc, full, busy, onSave, onDone }: {
  svc: ServiceManifest;
  full: ServiceManifest | null;
  busy: boolean;
  onSave: (patch: Partial<ServiceManifest>) => Promise<boolean>;
  onDone: () => void;
}) {
  const base = full ?? svc;
  const [name, setName] = useState(svc.name);
  const [desc, setDesc] = useState(svc.description ?? "");
  const [inputSchema, setInputSchema] = useState(() => JSON.stringify(base.input_schema ?? {}, null, 2));
  const [outputSchema, setOutputSchema] = useState(() => JSON.stringify(base.output_schema ?? {}, null, 2));
  const [category, setCategory] = useState(base.category ?? "other");
  const [tags, setTags] = useState((base.tags ?? []).join(", "));
  const [localErr, setLocalErr] = useState<string | null>(null);

  const save = async () => {
    let inputParsed: Record<string, unknown> = {};
    let outputParsed: Record<string, unknown> = {};
    try {
      inputParsed = JSON.parse(inputSchema || "{}");
      outputParsed = JSON.parse(outputSchema || "{}");
    } catch (e) {
      setLocalErr(`schema JSON 解析失败：${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    setLocalErr(null);
    const ok = await onSave({
      name: name.trim(),
      description: desc.trim(),
      input_schema: inputParsed,
      output_schema: outputParsed,
      category,
      tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
    });
    if (ok) onDone();
  };

  return (
    <div className="flex" style={{ gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
      <div style={{ flex: "1 1 260px", minWidth: 260 }}>
        <div className="field">
          <label>服务名称（Agent 匹配需求的第一眼）</label>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} aria-label="服务名称" />
        </div>
        <div className="field">
          <label>描述——写给 Agent 看：能回答什么问题、数据来自哪、入参含义（{svc.service_id}）</label>
          <textarea rows={5} value={desc} onChange={(e) => setDesc(e.target.value)} aria-label="服务描述" placeholder="例：币安 USDT 永续合约 AI 强势币 Top N 榜……适合『哪些币在涨』类问题。入参 limit=Top N。" />
        </div>
        <div className="field">
          <label>类目（决策层分区词表）</label>
          <select value={category} onChange={(e) => setCategory(e.target.value)} aria-label="类目">
            {CATEGORY_OPTIONS.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>标签（逗号分隔）</label>
          <input type="text" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="binance, futures, top-n" aria-label="标签" />
        </div>
        {localErr && <div className="alert warn">{localErr}</div>}
        <div className="btn-row" style={{ marginTop: 8 }}>
          <button className="btn small" disabled={busy || !name.trim()} onClick={() => void save()}>
            {busy ? <Spinner label="保存中…" /> : "保存并重新发布（版本自动 +1）"}
          </button>
          <button className="btn small secondary" disabled={busy} onClick={onDone}>
            取消
          </button>
        </div>
      </div>
      <div style={{ flex: "1 1 300px", minWidth: 300 }}>
        <JsonEditor label="input_schema（Agent 构造调用参数的依据）" value={inputSchema} onChange={setInputSchema} rows={9} />
        <JsonEditor label="output_schema（Agent 解读返回数据的依据）" value={outputSchema} onChange={setOutputSchema} rows={7} />
        <div className="help">GET 上游的 input_schema 仅支持标量/标量数组（嵌套对象会被 422 拒绝）。</div>
      </div>
    </div>
  );
}

function ServiceCredentialsPanel({ serviceId, teamAgentId }: { serviceId: string; teamAgentId: number | null }) {
  const [names, setNames] = useState<string[] | null>(null);
  // 团队默认认证头（网关回退）：服务级为空时展示
  const [teamNames, setTeamNames] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [rows, setRows] = useState<CredentialRow[]>([]);
  const [confirmReplace, setConfirmReplace] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const info = await credentialsApi.list(serviceId);
      setNames(info.header_names);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  };

  // 展开即自动加载已配置头名 + 团队默认头名
  useEffect(() => {
    void load();
    if (teamAgentId != null) {
      teamCredentialsApi.list(teamAgentId).then((i) => setTeamNames(i.header_names)).catch(() => setTeamNames(null));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serviceId, teamAgentId]);

  const currentCount = names?.length ?? 0;

  const doReplace = async () => {
    setBusy(true);
    setError(null);
    try {
      await credentialsApi.put(serviceId, rowsToHeaders(rows));
      setConfirmReplace(false);
      setEditing(false);
      setRows([]);
      await load();
    } catch (e) {
      setError(e);
      setConfirmReplace(false);
    } finally {
      setBusy(false);
    }
  };

  const doClear = async () => {
    setBusy(true);
    setError(null);
    try {
      await credentialsApi.remove(serviceId);
      setConfirmClear(false);
      await load();
    } catch (e) {
      setError(e);
      setConfirmClear(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="dim" style={{ marginBottom: 6 }}>
        上游认证头（仅网关转发时注入，消费者不可见；值加密存储、永不回显）——服务 {serviceId}：
      </div>

      {loading ? (
        <Spinner label="读取已配置头名…" />
      ) : names == null ? null : names.length === 0 ? (
        teamNames != null && teamNames.length > 0 ? (
          <div className="flex" style={{ marginBottom: 8 }}>
            <span className="badge info" title="服务未单独配置凭证——转发时自动复用团队默认认证头（网关回退）">
              团队默认（{teamNames.map((n) => `${n} ✓`).join(" ")}）
            </span>
            <span className="dim">服务级为空，网关自动回退团队头（在团队详情管理）</span>
          </div>
        ) : (
          <div className="dim">尚未配置任何认证头。</div>
        )
      ) : (
        <div className="flex" style={{ marginBottom: 8 }}>
          {names.map((n) => (
            <span key={n} className="badge ok">
              {n} ✓
            </span>
          ))}
          <span className="dim">（仅头名，值不回显）</span>
        </div>
      )}

      {!editing ? (
        <div className="btn-row">
          <button
            className="btn small"
            onClick={() => {
              setEditing(true);
              setRows([]);
            }}
          >
            更新凭证
          </button>
          <button className="btn small danger" disabled={currentCount === 0 || busy} onClick={() => setConfirmClear(true)}>
            清除凭证
          </button>
          <button className="btn small secondary" onClick={() => void load()}>
            刷新
          </button>
        </div>
      ) : (
        <div style={{ maxWidth: 640 }}>
          <div className="alert warn" style={{ fontSize: 13 }}>
            <b>全量替换语义</b>：保存时以这里的内容<b>完全替换</b>现有凭证——留空保存 = 清空全部；要保留的头必须在这里重新填写（出于安全，旧值不回显）。
          </div>
          <CredentialHeadersEditor rows={rows} onChange={setRows} />
          <div className="btn-row" style={{ marginTop: 8 }}>
            <button className="btn small" disabled={busy} onClick={() => setConfirmReplace(true)}>
              替换全部 {currentCount} 个头
            </button>
            <button className="btn small secondary" onClick={() => setEditing(false)}>
              取消
            </button>
          </div>
        </div>
      )}
      {error != null && <ErrorBox error={error} />}

      <ConfirmDialog
        open={confirmReplace}
        title={`替换全部 ${currentCount} 个认证头`}
        body={
          <div>
            将用编辑器里的 <b>{Object.keys(rowsToHeaders(rows)).length}</b> 个头完全替换服务 <b>{serviceId}</b> 现有的 {currentCount} 个认证头（留空保存即清空；旧值不回显，要保留的需重填）。替换后网关约 60s 内生效。
          </div>
        }
        confirmText="确认替换"
        onConfirm={() => void doReplace()}
        onCancel={() => setConfirmReplace(false)}
      />
      <ConfirmDialog
        open={confirmClear}
        title={`清空 ${currentCount} 个认证头`}
        body={<div>将删除服务 <b>{serviceId}</b> 的全部上游认证头（不可恢复，需重新填写）。清除后网关转发不再携带这些头，约 60s 内生效。</div>}
        confirmText="确认清除"
        onConfirm={() => void doClear()}
        onCancel={() => setConfirmClear(false)}
      />
    </div>
  );
}

function bumpVersion(v: string): string {
  const parts = v.split(".").map((x) => Number(x) || 0);
  parts[2] = (parts[2] ?? 0) + 1;
  return parts.join(".");
}

/* 旧独立「身份钱包绑定」步骤已并入第 ① 步认领（认证先行）；提现仍独立： */

/* ============ 步骤 5：提现 ============ */
export function WithdrawStep() {
  const w = useWallet();
  const [addr, setAddr] = useState("");
  // 「我的地址」语义：默认自动填连接钱包（提现/查余额的主场景），可改 + 快填按钮
  useEffect(() => {
    if (!addr && w.address) setAddr(w.address);
  }, [w.address, addr]);
  const [credits, setCredits] = useState<bigint | null>(null);
  const [busy, setBusy] = useState(false);
  const [waitingWallet, setWaitingWallet] = useState(false);
  const [cancelled, setCancelled] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [confirming, setConfirming] = useState(false);
  const [tx, setTx] = useState<string | null>(null);

  const queryWith = async (target: string) => {
    setBusy(true);
    setError(null);
    setTx(null);
    try {
      setCredits(await fetchProviderCredits(target.trim()));
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const query = () => queryWith(addr);

  const withdraw = async () => {
    setBusy(true);
    setWaitingWallet(true);
    setError(null);
    setCancelled(false);
    try {
      // 与消费端同一共享路径：用户选中的 provider → 先确保在目标链（CHAIN_ID），再 eth_sendTransaction（20 gwei 固定费率）
      const sel = await w.requireProvider();
      if (!sel) throw new Error("未选择浏览器钱包。");
      await ensureBotChain(sel.provider);
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
        <label>地址（默认 = 你的连接钱包；查别人的 credits 可手填）</label>
        <div className="flex">
          <div className="grow">
            <input
              type="text"
              value={addr}
              placeholder={w.address ? "" : "0x…（连接钱包自动填入，或手动填写）"}
              onChange={(e) => setAddr(e.target.value)}
              aria-label="提现查询地址"
            />
          </div>
          {w.address && (
            <button type="button" className="btn small" onClick={() => setAddr(w.address!)} title="一键填回当前连接的钱包地址">
              使用当前钱包 {w.address.slice(0, 6)}…
            </button>
          )}
        </div>
      </div>
      <div className="btn-row">
        <button
          className="btn"
          disabled={!w.address || busy}
          onClick={async () => {
            setAddr(w.address!); // 我的 credits 一键：用连接地址查（并同步输入框）
            await queryWith(w.address!);
          }}
        >
          {busy ? <Spinner label="eth_call 查询中…" /> : "查询我的 credits"}
        </button>
        <button className="btn secondary" disabled={!/^0x[0-9a-fA-F]{40}$/.test(addr.trim()) || busy} onClick={query}>
          查询上面填写的地址
        </button>
      </div>

      {credits !== null && (
        <div className="stat-grid" style={{ marginTop: 4 }}>
          <StatCard
            k="该地址在 PayVault 的未提现收入"
            value={`${fromRaw(credits)} USDT`}
            sub={`raw=${credits.toString()}`}
          />
          <StatCard
            k="记账合约（PayVault）"
            value={<span style={{ fontSize: 16 }}>{PAY_VAULT.slice(0, 10)}…{PAY_VAULT.slice(-6)}</span>}
            sub="I4：合约内 USDT 余额恒等于总 credits"
            evidence={<EvidencePair hash={PAY_VAULT} href={EXPLORER_URL} label={`去 ${EXPLORER_HOST} 查看合约`} />}
          />
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
        w.candidates.length > 0 ? (
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
            （chainId {CHAIN_ID}，RPC {RPC_URL}）；③ 刷新本页后点击「发起 providerWithdraw」。也可用任意脚本以该钱包调用
            <span className="mono"> PayVault({PAY_VAULT.slice(0, 10)}…).providerWithdraw(to, amount)</span>。
          </WarnBox>
        )
      )}
      {credits === 0n && <div className="dim">该地址暂无可提现 credits（keeper 结算入账后这里会有数字——去总览页看结算观测）。</div>}
    </div>
  );
}

/* ============ Teams 形态：我的 Teams（新首步）+ Team 主页 ============ */

/** 新首步：我的 Teams（连接钱包 → mine 列表 / 空态创建 / 老身份导入折叠在高级区）。 */

/* ============ 收入 Grid（仪表盘页首，自动查询） ============ */

export interface RevenueSummary {
  totalRaw: bigint; // Σ 各团队链上 Charged
  creditsRaw: bigint; // Σ 各服务收款钱包 PayVault credits（未提现）
  withdrawnRaw: bigint; // totalRaw - creditsRaw（页内口径）
  teamsCount: number;
  chargedCount: number;
  wallets: string[];
}

/** 拉取收入三数：mine → 团队详情建去重钱包集 → 排行榜按钱包聚合 Charged → credits 逐钱包。
 * Charged 的记账键是钱包而非团队：一个服务收款钱包可挂多团队（如同一钱包发 RadAI+RadTeam），
 * Σ 团队详情的 revenue 会把同一批事件数 N 遍——总收入/笔数必须按钱包集聚合（与排行榜/proof 同口径）。 */
async function fetchRevenueSummary(wallet: string): Promise<RevenueSummary> {
  const mine = await teamsApi.mine(wallet);
  const details = await Promise.all(mine.teams.map((t) => teamsApi.detail(t.agent_id).catch(() => null)));
  const walletSet = new Set<string>(); // 小写归一去重（revenue.wallets 与 manifest.wallet 大小写不一）
  for (const d of details) {
    if (!d) continue;
    for (const w of d.revenue.wallets) walletSet.add(w.toLowerCase());
    for (const s of d.services) if (s.manifest.provider.wallet) walletSet.add(s.manifest.provider.wallet.toLowerCase());
  }
  const wallets = [...walletSet];
  let totalRaw = 0n;
  let chargedCount = 0;
  if (wallets.length) {
    const board = await coreApi.leaderboardProviders().catch(() => null); // 排行榜不可达 → 收入按 0，不崩
    for (const row of board?.providers ?? []) {
      if (!walletSet.has(row.wallet.toLowerCase())) continue;
      totalRaw += BigInt(row.revenue_raw);
      chargedCount += row.charged_count;
    }
  }
  const creditsArr = await Promise.all(wallets.map((w) => fetchProviderCredits(w).catch(() => 0n)));
  const creditsRaw = creditsArr.reduce((a, b) => a + b, 0n);
  // 已提现 = 总收入 − 当前 credits（口径：credits 是 Charged 后尚未 providerWithdraw 的部分）
  const withdrawnRaw = totalRaw > creditsRaw ? totalRaw - creditsRaw : 0n;
  return { totalRaw, creditsRaw, withdrawnRaw, teamsCount: mine.teams.length, chargedCount, wallets };
}

/** 收入 Grid：三卡 + 提现内嵌（未提现卡带主按钮）。未连接钱包不渲染（父层控制）。 */
export function RevenueGrid({ onWithdrawn }: { onWithdrawn?: () => void }) {
  const w = useWallet();
  const [refreshTick, setRefreshTick] = useState(0);
  const [withdrawOpen, setWithdrawOpen] = useState(false);

  const rev = useAsync(
    () => (w.address ? fetchRevenueSummary(w.address) : Promise.resolve(null)),
    [w.address, refreshTick]
  );

  if (!w.address) return null; // 未连接不渲染（保险；父层也有前置）

  const loading = rev.loading && rev.data == null;
  const failed = rev.error != null && rev.data == null;

  return (
    <div className="card">
      <div className="flex" style={{ justifyContent: "space-between" }}>
        <h3 className="mb-0">收入总览</h3>
        <div className="btn-row">
          <button className="btn small secondary" onClick={() => setRefreshTick((t) => t + 1)} disabled={loading}>
            刷新
          </button>
        </div>
      </div>
      <p className="card-desc" style={{ marginTop: 4 }}>
        链上 Charged 聚合（{rev.data?.teamsCount ?? "…"} 个团队 · {rev.data?.chargedCount ?? "…"} 笔）· PayVault credits 为未提现口径
      </p>

      {loading && <Spinner label="自动查询收入三数（mine → teams → credits）…" />}
      {failed && (
        <div className="alert warn">
          <b>收入查询失败</b>——可能是链节点瞬断。点「刷新」重试；数字仅作展示，不影响链上资金。
          <ErrorBox error={rev.error} />
        </div>
      )}

      {rev.data && (
        <div className="stat-grid">
          <StatCard
            k="总收入（链上 Charged）"
            value={`${fromRaw(rev.data.totalRaw)} USDT`}
            sub={`raw=${rev.data.totalRaw.toString()} · ${rev.data.chargedCount} 笔`}
            evidence={<EvidencePair hash={`Σ teams revenue_raw=${rev.data.totalRaw.toString()}`} href={EXPLORER_URL} label="链上 Charged 口径，去 scan 核对" />}
          />
          <div className="stat-card">
            <div className="k">未提现收入（可提现）</div>
            <div className="v num tone-success">{fromRaw(rev.data.creditsRaw)} USDT</div>
            <div className="s num">raw={rev.data.creditsRaw.toString()} · PayVault credits</div>
            <div className="s">
              <button className="btn small" style={{ marginTop: 4 }} onClick={() => setWithdrawOpen((o) => !o)} disabled={rev.data!.creditsRaw === 0n}>
                {withdrawOpen ? "收起提现" : "提现"}
              </button>
            </div>
          </div>
        </div>
      )}

      {withdrawOpen && <WithdrawPanel wallets={rev.data?.wallets ?? []} onDone={() => { setWithdrawOpen(false); setRefreshTick((t) => t + 1); onWithdrawn?.(); }} />}
    </div>
  );
}

/** 内嵌提全面板：服务收款钱包逐个显示 credits → 全额 providerWithdraw（经注入钱包）。 */
function WithdrawPanel({ wallets, onDone }: { wallets: string[]; onDone: () => void }) {
  const w = useWallet();
  const [credits, setCredits] = useState<Record<string, bigint>>({});
  const [busy, setBusy] = useState(false);
  const [waitingWallet, setWaitingWallet] = useState(false);
  const [cancelled, setCancelled] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [tx, setTx] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.all(wallets.map((wv) => fetchProviderCredits(wv).catch(() => 0n).then((c) => [wv, c] as const))).then((pairs) => {
      if (alive) setCredits(Object.fromEntries(pairs));
    });
    return () => {
      alive = false;
    };
  }, [wallets]);

  const withdraw = async (walletAddr: string) => {
    const amount = credits[walletAddr] ?? 0n;
    if (amount <= 0n) return;
    setBusy(true);
    setWaitingWallet(true);
    setError(null);
    setCancelled(false);
    try {
      const sel = await w.requireProvider();
      if (!sel) throw new Error("未选择浏览器钱包。");
      await ensureBotChain(sel.provider);
      const from = (await silentAccounts(sel.provider)) ?? (await connectInjected(sel.provider)).address;
      const hash = await sendInjectedTx(sel.provider, from, VAULT_ADDR, encodeAddrUint(SEL_C.providerWithdraw, walletAddr, amount));
      setWaitingWallet(false);
      setTx(hash);
      await waitForInjectedReceipt(hash);
      onDone();
    } catch (e) {
      if (isUserRejected(e)) setCancelled(true);
      else setError(e);
    } finally {
      setBusy(false);
      setWaitingWallet(false);
      setConfirming(null);
    }
  };

  return (
    <div className="card" style={{ boxShadow: "none", background: "var(--surface-2)", marginTop: 12, marginBottom: 0 }}>
      <h3 className="mt-0" style={{ fontSize: 14 }}>提现（PayVault credits → 钱包，铁律 P8 路径恒开）</h3>
      {wallets.length === 0 ? (
        <Empty text="还没有服务收款钱包——先发布服务产生收入。" />
      ) : (
        <table className="list">
          <thead>
            <tr><th>服务收款钱包</th><th>credits（未提现）</th><th>操作</th></tr>
          </thead>
          <tbody>
            {wallets.map((wv) => {
              const c = credits[wv];
              return (
                <tr key={wv}>
                  <td className="mono" style={{ fontSize: 12 }}>{wv.slice(0, 10)}…{wv.slice(-6)}</td>
                  <td className="num">{c == null ? <Spinner /> : `${fromRaw(c)} USDT`}<span className="dim num"> · raw={c?.toString() ?? "…"}</span></td>
                  <td>
                    {c != null && c > 0n ? (
                      <button className="btn small danger" disabled={busy} onClick={() => setConfirming(wv)}>
                        提现全额
                      </button>
                    ) : (
                      <span className="dim">{c === 0n ? "无可提现" : "…"}</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <ConfirmDialog
        open={confirming != null}
        title="确认上链提现（不可逆）"
        body={
          <div>
            将从 PayVault 把 <b className="num">{confirming ? fromRaw(credits[confirming] ?? 0n) : "-"} USDT</b>（raw={confirming ? (credits[confirming] ?? 0n).toString() : "-"}）提现到{" "}
            <span className="mono">{confirming?.slice(0, 10)}…</span>。交易由注入钱包发送，上链后不可撤销。
          </div>
        }
        confirmText="确认提现"
        onConfirm={() => confirming && void withdraw(confirming)}
        onCancel={() => setConfirming(null)}
      />
      {cancelled && <WarnBox>你取消了提现交易（钱包弹窗里拒绝）。没有产生任何交易，可重新发起。</WarnBox>}
      {error != null && <ErrorBox error={error} />}
      {waitingWallet && (
        <div className="alert info"><span className="flex"><span className="spin" /> 等待钱包确认…（请在扩展弹窗里确认提现交易）</span></div>
      )}
      {tx && <SuccessBox>提现交易已发送：<TxLink hash={tx} /></SuccessBox>}
    </div>
  );
}

export function MyTeamsStep({ onOpenTeam, onPublish }: { onOpenTeam: (agentId: number) => void; onPublish: (team: MyTeam) => void }) {
  const w = useWallet();
  const mine = useAsync(() => (w.address ? teamsApi.mine(w.address) : Promise.resolve(null)), [w.address]);

  if (!w.address) {
    return (
      <div className="card" style={{ textAlign: "center", padding: "48px 32px" }}>
        <h2 style={{ margin: "0 0 8px" }}>创建你的第一个团队</h2>
        <p className="card-desc" style={{ fontSize: 14 }}>一次钱包签名，链上身份自动管理。先连接钱包开始。</p>
        <div style={{ marginTop: 20 }}>
          <ConnectWalletButton size="normal" label="连接钱包，开始创建" />
        </div>
      </div>
    );
  }

  const teams = mine.data?.teams ?? null;

  // 新用户空态：居中大卡主 CTA（一键发起全流程）
  if (mine.data != null && teams != null && teams.length === 0) {
    return (
      <div>
        <CreateTeamButton onCreated={onOpenTeam} hero />
      </div>
    );
  }

  // 有团队：维持现状（右上角 + 卡片网格）
  return (
    <div className="card">
      <div className="flex" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h3 className="mb-0">我的 Teams</h3>
          <p className="card-desc" style={{ margin: "2px 0 0" }}>钱包 {w.address.slice(0, 8)}… · 一个钱包可建多个团队</p>
        </div>
        <CreateTeamButton onCreated={onOpenTeam} inline />
      </div>
      <AsyncSection state={mine} empty="还没有团队">
        {(m) =>
          m.teams.length === 0 ? (
            <div className="empty" style={{ padding: "24px 0" }}>还没有团队——点右上角「+ 创建团队」开始上架服务。</div>
          ) : (
            <div className="svc-grid" style={{ marginTop: 12 }}>
              {m.teams.map((t) => (
                <div
                  key={t.agent_id}
                  className="svc-card clickable"
                  onClick={() => onOpenTeam(t.agent_id)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpenTeam(t.agent_id); } }}
                  role="button"
                  tabIndex={0}
                  aria-label={`打开团队 ${t.display_name}`}
                >
                  <div className="flex" style={{ justifyContent: "space-between" }}>
                    <span className="svc-name">{t.display_name}</span>
                    <Badge kind="ok">{t.service_count} 服务</Badge>
                  </div>
                  <div className="dim" style={{ fontSize: 11 }}>创建于 {t.created_at.slice(0, 10)}</div>
                  <div className="flex" style={{ justifyContent: "flex-end", marginTop: 8 }}>
                    <button
                      className="btn small secondary"
                      onClick={(e) => { e.stopPropagation(); onPublish(t); }}
                      title="为此团队发布新服务（也可点整卡进详情后再发布）"
                    >
                      发布新服务
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )
        }
      </AsyncSection>

    </div>
  );
}

/** 创建团队（一键流）：点击 → prepare（代发铸造，默认名「我的团队」，origin 进 agentURI）→ AgentWalletSet 签名 → 绑定 → providers → 落地 Team 详情。 */
export function CreateTeamButton({
  onCreated,
  inline,
  hero,
  label,
}: {
  onCreated?: (agentId: number) => void;
  /** 挂在标题行的小号形态 */
  inline?: boolean;
  /** 新用户空态大卡形态（居中主 CTA，一键发起） */
  hero?: boolean;
  label?: string;
}) {
  const w = useWallet();
  const [phase, setPhase] = useState<null | "prepare" | "sign" | "bind" | "claim" | "done">(null);
  const [deadline, setDeadline] = useState<number | null>(null);
  const [countdown, setCountdown] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const [cancelled, setCancelled] = useState(false);

  useEffect(() => {
    if (deadline == null) return;
    const t = setInterval(() => setCountdown(Math.max(0, deadline - Math.floor(Date.now() / 1000))), 1000);
    return () => clearInterval(t);
  }, [deadline]);

  const run = async () => {
    setError(null);
    setCancelled(false);
    setPhase("prepare");
    try {
      const prep = await teamsApi.prepare("我的团队", window.location.origin); // 链上 agentURI = {origin}/#/provider（内网可达）
      setPhase("sign");
      const sel = await w.requireProvider();
      if (!sel) throw new Error("未选择浏览器钱包。");
      const dl = Math.floor(Date.now() / 1000) + 300;
      setDeadline(dl);
      setCountdown(300);
      const td = agentWalletSetTypedData({
        agentId: prep.agent_id,
        newWallet: w.address!,
        owner: CUSTODIAN,
        deadline: dl,
        verifyingContract: IDENTITY_REGISTRY,
        chainId: CHAIN_ID,
      });
      const signer = await browserProvider(sel.provider).getSigner(w.address!);
      const sig = await signer.signTypedData(td.domain, td.types, td.message);
      setPhase("bind");
      await botChainApi.bindWallet(prep.agent_id, w.address!, sig, dl);
      setPhase("claim");
      await coreApi.registerProvider(prep.agent_id, "我的团队", w.address!);
      setPhase("done");
      onCreated?.(prep.agent_id);
    } catch (e) {
      if (isUserRejected(e)) setCancelled(true);
      else setError(e);
      setPhase(null);
    }
  };

  if (!w.address) return null;
  const busy = phase != null && phase !== "done";

  if (hero) {
    // 新用户空态：居中大卡，一键发起全流程（无名字输入步，默认「我的团队」，落地后可改名）
    return (
      <div className="card" style={{ textAlign: "center", padding: "48px 32px" }}>
        <h2 style={{ margin: "0 0 8px" }}>创建你的第一个团队</h2>
        <p className="card-desc" style={{ fontSize: 14 }}>一次钱包签名，链上身份自动管理。团队名默认「我的团队」，创建后可随时改。</p>
        <div style={{ marginTop: 20 }}>
          {phase == null && !cancelled && (
            <button className="btn" style={{ fontSize: 16, padding: "12px 36px" }} onClick={run}>
              创建团队
            </button>
          )}
          {busy && (
            <div className="alert info" style={{ maxWidth: 420, margin: "0 auto" }}>
              <span className="flex" style={{ justifyContent: "center" }}><span className="spin" />
                {phase === "prepare" ? "正在创建团队身份…" : phase === "sign" ? "等待钱包签名确认…" : phase === "bind" ? "绑定上链中…" : "登记团队…"}
              </span>
              {deadline != null && (phase === "sign" || phase === "bind") && (
                <div style={{ marginTop: 6 }}>
                  <span className={`badge ${countdown < 60 ? "warn" : "muted"}`}>签名窗口 {Math.floor(countdown / 60)}:{String(countdown % 60).padStart(2, "0")}</span>
                </div>
              )}
            </div>
          )}
          {phase === "done" && <SuccessBox>✓ 创建成功——正在进入团队…</SuccessBox>}
          {cancelled && (
            <div>
              <WarnBox>你取消了签名（钱包弹窗里拒绝）。团队身份未完成绑定，可重新点「创建团队」。</WarnBox>
              <button className="btn" style={{ marginTop: 12 }} onClick={run}>重试创建</button>
            </div>
          )}
          {error != null && <ErrorBox error={error} />}
        </div>
      </div>
    );
  }

  return (
    <div style={inline ? {} : { marginTop: 14 }}>
      <button className={inline ? "btn small" : "btn"} disabled={busy} onClick={run} title="一键创建：一次钱包签名，链上身份自动管理（默认名「我的团队」，进详情可改）">
        {busy ? (phase === "prepare" ? "创建身份…" : phase === "sign" ? "等待签名…" : phase === "bind" ? "绑定中…" : "登记…") : (label ?? "+ 创建团队")}
      </button>
      {phase === "done" && <span className="badge ok" style={{ marginLeft: 8 }}>✓ 已创建</span>}
      {cancelled && <WarnBox>你取消了签名——团队未完成创建，可重试。</WarnBox>}
      {error != null && <ErrorBox error={error} />}
    </div>
  );
}

/** 团队默认认证头弹窗：掩码 chips（值永不回显）+ 全量替换语义 + 清除二次确认。 */
function TeamCredentialsModal({ agentId, onClose }: { agentId: number; onClose: () => void }) {
  const [names, setNames] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [rows, setRows] = useState<CredentialRow[]>([]);
  const [confirmReplace, setConfirmReplace] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const info = await teamCredentialsApi.list(agentId);
      setNames(info.header_names);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  const currentCount = names?.length ?? 0;

  const doReplace = async () => {
    setBusy(true);
    setError(null);
    try {
      await teamCredentialsApi.put(agentId, rowsToHeaders(rows));
      setConfirmReplace(false);
      setEditing(false);
      setRows([]);
      await load();
    } catch (e) {
      setError(e);
      setConfirmReplace(false);
    } finally {
      setBusy(false);
    }
  };

  const doClear = async () => {
    setBusy(true);
    setError(null);
    try {
      await teamCredentialsApi.remove(agentId);
      setConfirmClear(false);
      await load();
    } catch (e) {
      setError(e);
      setConfirmClear(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="团队默认认证头"
      style={{ position: "fixed", inset: 0, background: "rgba(10,16,28,0.45)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 60, overflow: "auto" }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="card" style={{ maxWidth: 560, margin: 20, width: "94vw", maxHeight: "88vh", overflow: "auto" }}>
        <h3 className="mt-0">团队默认认证头（team #{agentId}）</h3>
        <div className="alert info" style={{ fontSize: 13 }}>
          团队默认认证头在<b>服务自身未配置凭证时自动生效</b>（网关回退）——改这里 = 所有未单独配置的服务一起换 key。
        </div>

        {loading ? (
          <Spinner label="读取已配置头名…" />
        ) : names == null ? null : names.length === 0 ? (
          <div className="dim">尚未配置团队默认认证头。配置后，团队内未单独配凭证的服务将自动复用。</div>
        ) : (
          <div className="flex" style={{ marginBottom: 12 }}>
            {names.map((n) => (
              <span key={n} className="badge ok">{n} ✓</span>
            ))}
            <span className="dim">（仅头名，值不回显）</span>
          </div>
        )}

        {!editing ? (
          <div className="btn-row">
            <button className="btn small" onClick={() => { setEditing(true); setRows([]); }}>更新凭证</button>
            <button className="btn small danger" disabled={currentCount === 0 || busy} onClick={() => setConfirmClear(true)}>清除凭证</button>
            <button className="btn small secondary" onClick={() => void load()}>刷新</button>
          </div>
        ) : (
          <div>
            <div className="alert warn" style={{ fontSize: 13 }}>
              <b>全量替换语义</b>：保存时以这里的内容<b>完全替换</b>现有凭证——留空保存 = 清空全部；要保留的头必须重新填写（出于安全，旧值不回显）。
            </div>
            <CredentialHeadersEditor rows={rows} onChange={setRows} />
            <div className="btn-row" style={{ marginTop: 8 }}>
              <button className="btn small" disabled={busy} onClick={() => setConfirmReplace(true)}>替换全部 {currentCount} 个头</button>
              <button className="btn small secondary" onClick={() => setEditing(false)}>取消</button>
            </div>
          </div>
        )}
        {error != null && <ErrorBox error={error} />}

        <div className="btn-row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
          <button className="btn secondary" onClick={onClose}>关闭</button>
        </div>
      </div>

      <ConfirmDialog
        open={confirmReplace}
        title={`替换团队全部 ${currentCount} 个认证头`}
        body={
          <div>
            将用编辑器里的 <b>{Object.keys(rowsToHeaders(rows)).length}</b> 个头完全替换团队 #{agentId} 现有的 {currentCount} 个默认认证头（留空保存即清空）。所有未单独配置凭证的服务将立即使用新头（网关约 60s 生效）。
          </div>
        }
        confirmText="确认替换"
        onConfirm={() => void doReplace()}
        onCancel={() => setConfirmReplace(false)}
      />
      <ConfirmDialog
        open={confirmClear}
        title={`清空团队 ${currentCount} 个默认认证头`}
        body={<div>将删除团队 #{agentId} 的全部默认认证头（不可恢复）。清除后，未单独配置的服务将不再携带认证头调用上游。</div>}
        confirmText="确认清除"
        onConfirm={() => void doClear()}
        onCancel={() => setConfirmClear(false)}
      />
    </div>
  );
}

/** Team 主页：聚合战绩卡 + 服务管理。 */
export function TeamHome({ agentId, onBack, onPublish, onRenamed }: { agentId: number; onBack: () => void; onPublish: (team: MyTeam) => void; onRenamed?: () => void }) {
  const w = useWallet();
  const team = useAsync(() => teamsApi.detail(agentId), [agentId]);
  const [teamCredOpen, setTeamCredOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [newName, setNewName] = useState("");
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameError, setRenameError] = useState<unknown>(null);

  const doRename = async () => {
    if (!team.data || !newName.trim()) return;
    setRenameBusy(true);
    setRenameError(null);
    try {
      // 改名 = 同钱包重复提交幂等更新（POST /providers 同 claim_wallet）
      await coreApi.registerProvider(agentId, newName.trim(), team.data.team.claim_wallet ?? w.address ?? "");
      setRenaming(false);
      team.reload();
      onRenamed?.();
    } catch (e) {
      setRenameError(e);
    } finally {
      setRenameBusy(false);
    }
  };

  return (
    <div className="card">
      {teamCredOpen && (
        <TeamCredentialsModal agentId={agentId} onClose={() => setTeamCredOpen(false)} />
      )}
      <div className="flex" style={{ justifyContent: "space-between" }}>
        <button className="btn small secondary" onClick={onBack}>← 我的 Teams</button>
        <div className="btn-row">
          <button className="btn small secondary" onClick={() => setTeamCredOpen(true)} title="服务自身未配凭证时自动生效的默认头（网关回退）">
            团队默认认证头
          </button>
          <button className="btn small" onClick={() => team.data && onPublish({ agent_id: agentId, display_name: team.data.team.display_name, claim_wallet: team.data.team.claim_wallet, service_count: team.data.services.length, created_at: team.data.team.created_at })}>
            发布新服务
          </button>
        </div>
      </div>

      <AsyncSection state={team} empty="团队不存在">
        {(t) => (
          <>
            <div className="flex" style={{ alignItems: "baseline", gap: 8, marginTop: 12 }}>
              <h3 style={{ margin: 0 }}>{t.team.display_name}</h3>
              {!renaming && (
                <button className="btn small ghost" onClick={() => { setRenaming(true); setNewName(t.team.display_name); }} title="同钱包重复提交=幂等改名">
                  改名
                </button>
              )}
            </div>
            {renaming && (
              <div className="flex" style={{ marginTop: 8, maxWidth: 420 }}>
                <input type="text" value={newName} maxLength={128} onChange={(e) => setNewName(e.target.value)} aria-label="团队新名称" placeholder="新名称" />
                <button className="btn small" disabled={renameBusy || !newName.trim()} onClick={doRename}>
                  {renameBusy ? "保存中…" : "保存"}
                </button>
                <button className="btn small secondary" disabled={renameBusy} onClick={() => setRenaming(false)}>取消</button>
              </div>
            )}
            {renameError != null && <ErrorBox error={renameError} />}
            <div className="dim mono" style={{ fontSize: 11, marginBottom: 10 }}>
              服务收款钱包 {t.team.claim_wallet?.slice(0, 10) ?? "未绑定"}… · 创建于 {t.team.created_at.slice(0, 19)}
            </div>
            <div className="stat-grid">
              <StatCard
                k="团队收入"
                value={fromRaw(BigInt(t.revenue.total_raw))}
                sub={`${t.revenue.charged_count} 笔 Charged · raw=${t.revenue.total_raw}`}
                evidence={<EvidencePair hash={`team_revenue_raw=${t.revenue.total_raw}`} href={EXPLORER_URL} label="链上 Charged 口径，去 scan 核对" />}
              />
              <StatCard k="服务数" value={t.services.length} sub="在售能力" />
              <StatCard
                k="履约汇总"
                value={(() => {
                  const svcs = t.fulfillment.services;
                  const ok = svcs.reduce((a, s) => a + s.calls_success, 0);
                  const abort = svcs.reduce((a, s) => a + s.calls_aborted, 0);
                  return `${ok + abort > 0 ? Math.round((ok / (ok + abort)) * 100) : 100}%`;
                })()}
                sub={`p95 最慢 ${Math.max(0, ...t.fulfillment.services.map((s) => s.p95_ms))}ms`}
              />
            </div>
            {t.degraded.length > 0 && <WarnBox>部分数据降级：{t.degraded.join("、")}</WarnBox>}

            <div className="section-title">团队服务（履约数据来自网关调用流水）</div>
            {t.services.length === 0 ? (
              <Empty text="还没有服务——点右上「发布新服务」" />
            ) : (
              <table className="list" aria-label="团队服务表">
                <thead>
                  <tr>
                    <th>服务</th>
                    <th>价格</th>
                    <th>调用成功</th>
                    <th>中止</th>
                    <th>p50</th>
                    <th>p95</th>
                    <th>支付者</th>
                    <th>最近活跃</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {t.services.map((s) => {
                    const m = s.manifest;
                    const ful = t.fulfillment.services.find((f) => f.service_id === s.service_id);
                    return (
                      <tr key={s.service_id}>
                        <td>
                          <div><b>{m.name}</b>{" "}{m.endpoint.type === "http_json" && <span className="badge muted">{m.endpoint.method ?? "POST"}</span>}</div>
                          <div className="mono dim" style={{ fontSize: 11 }}>{s.service_id}</div>
                        </td>
                        <td className="num">{m.pricing.amount}</td>
                        <td className="num">{ful?.calls_success ?? "—"}</td>
                        <td className="num dim">{ful?.calls_aborted ?? "—"}</td>
                        <td className="num dim">{ful != null ? `${ful.p50_ms}ms` : "—"}</td>
                        <td className="num dim">{ful != null ? `${ful.p95_ms}ms` : "—"}</td>
                        <td className="num dim">{ful?.distinct_payers ?? "—"}</td>
                        <td className="dim" style={{ fontSize: 12, whiteSpace: "nowrap" }}>
                          {ful?.last_activity_at ? timeAgoShort(ful.last_activity_at) : "—"}
                        </td>
                        <td>
                          <a className="btn small secondary" style={{ textDecoration: "none", display: "inline-flex" }} href="#/" onClick={(e) => { e.preventDefault(); window.dispatchEvent(new CustomEvent("coincall:view-team", { detail: agentId })); }}>
                            管理
                          </a>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
            <div className="dim" style={{ fontSize: 12, marginTop: 6 }}>
              凭证管理在「团队默认认证头」与服务级「上游认证头」；公共视图数据 = 总览目录。
            </div>
          </>
        )}
      </AsyncSection>
    </div>
  );
}


function timeAgoShort(iso: string): string {
  const h = (Date.now() - new Date(iso).getTime()) / 3_600_000;
  if (h < 1) return `${Math.round(h * 60)} 分钟前`;
  if (h < 48) return `${h.toFixed(1)} 小时前`;
  return `${(h / 24).toFixed(1)} 天前`;
}
