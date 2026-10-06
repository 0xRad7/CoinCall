/** 总览页（默认，只读）：四卡 + degraded、Provider 收入榜（proof 外链）、服务目录、keeper 观测。 */
import { useState } from "react";
import { coreApi, type ProofResponse } from "../api/core";
import { gatewayApi } from "../api/gateway";
import { useAsync } from "../lib/useAsync";
import { AsyncSection, Badge, ErrorBox, Spinner, TxLink } from "../components/ui";
import { fromRaw } from "../chain/constants";

export default function Overview() {
  const overview = useAsync(() => coreApi.overview(), []);
  const providers = useAsync(() => coreApi.leaderboardProviders(), []);
  const catalog = useAsync(() => coreApi.catalog(), []);
  const [poll, setPoll] = useState(true);
  const keeper = useAsync(() => gatewayApi.keeperStatus(), [], { pollMs: poll ? 10_000 : 0 });

  return (
    <div>
      <h1 className="page-title">总览</h1>
      <p className="page-sub">平台全局状态：链上 GMV（唯一真相）、服务目录、keeper 结算观测。数据每 30s 内随访问刷新。</p>

      {/* 四卡 */}
      <AsyncSection state={overview}>
        {(o) => (
          <>
            <div className="stat-grid">
              <div className="stat-card">
                <div className="k">链上 GMV（Charged 总额）</div>
                <div className="v num">{o.gmv} USDT</div>
                <div className="s num">raw={o.gmv_raw} · {o.charged_count} 笔</div>
              </div>
              <div className="stat-card">
                <div className="k">调用笔数（成功 / 中止）</div>
                <div className="v num">{o.calls_success_total ?? "-"} / {o.calls_aborted_total ?? "-"}</div>
                <div className="s">中止 = Provider 失败未扣款</div>
              </div>
              <div className="stat-card">
                <div className="k">服务（在售/全部）</div>
                <div className="v num">{o.services_active} / {o.services_total}</div>
                <div className="s">paused 不参与调用</div>
              </div>
              <div className="stat-card">
                <div className="k">Provider（有收入/注册）</div>
                <div className="v num">{o.providers_with_revenue} / {o.providers_registered}</div>
                <div className="s num">已同步到区块 {o.synced_to_block}</div>
              </div>
            </div>
            {o.degraded.length > 0 && (
              <div className="alert warn">
                <b>部分数据降级</b>：{o.degraded.join("、")} —— 展示值可能滞后，以链上（scan.bohr.life）为准。
              </div>
            )}
          </>
        )}
      </AsyncSection>

      {/* Provider 收入榜 */}
      <div className="card">
        <h3>Provider 收入榜（链上 Charged，含 proof）</h3>
        <AsyncSection state={providers} empty="还没有 Provider 产生收入">
          {(p) => (
            <table className="list">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Provider</th>
                  <th>Agent</th>
                  <th>收入（USDT）</th>
                  <th>笔数</th>
                  <th>Proof</th>
                </tr>
              </thead>
              <tbody>
                {p.providers.map((row, i) => (
                  <ProviderRow key={row.wallet} idx={i + 1} wallet={row.wallet} name={row.display_name} agentId={row.agent_id} revenue={row.revenue} revenueRaw={row.revenue_raw} count={row.charged_count} />
                ))}
              </tbody>
            </table>
          )}
        </AsyncSection>
      </div>

      {/* 服务目录 */}
      <div className="card">
        <h3>服务目录</h3>
        <AsyncSection state={catalog} empty="目录为空——去 Provider 工作台发布第一个服务">
          {(c) => (
            <div className="svc-grid">
              {c.services.map((s) => (
                <div className="svc-card" key={s.service_id}>
                  <div className="flex" style={{ justifyContent: "space-between" }}>
                    <span className="svc-name">{s.manifest.name}</span>
                    <Badge kind={s.status === "active" ? "ok" : "warn"}>{s.status}</Badge>
                  </div>
                  <div className="mono dim">{s.service_id}</div>
                  <div style={{ margin: "8px 0" }}>
                    <span className="svc-price">
                      {s.manifest.pricing.amount} {s.manifest.pricing.token}
                    </span>
                    <span className="dim"> / 次 · raw={s.manifest.pricing.amount_raw}</span>
                  </div>
                  <div className="dim">
                    端点 {s.manifest.endpoint.type}
                    {s.manifest.endpoint.type === "http_json" ? ` · ${s.manifest.endpoint.method ?? "POST"}` : ""}
                    {s.manifest.endpoint.url ? ` · ${s.manifest.endpoint.url}` : "（平台内置）"} · Provider {s.manifest.provider.display_name}
                  </div>
                  {s.manifest.description && <div style={{ marginTop: 6, fontSize: 13, color: "var(--text-2)" }}>{s.manifest.description}</div>}
                </div>
              ))}
            </div>
          )}
        </AsyncSection>
      </div>

      {/* keeper 结算观测 */}
      <div className="card">
        <div className="flex" style={{ justifyContent: "space-between" }}>
          <h3 className="mb-0">Keeper 结算观测（settle 队列 → 链上 Charged）</h3>
          <label className="dim" style={{ cursor: "pointer" }}>
            <input type="checkbox" checked={poll} onChange={(e) => setPoll(e.target.checked)} /> 10s 自动轮询
          </label>
        </div>
        <AsyncSection state={keeper} empty="keeper 状态不可用">
          {(k) => (
            <div className="stat-grid" style={{ marginTop: 16 }}>
              <div className="stat-card">
                <div className="k">状态</div>
                <div className="v" style={{ fontSize: 18 }}>
                  {k.enabled && k.running ? <Badge kind="ok">运行中</Badge> : <Badge kind="err">停止</Badge>}
                </div>
                <div className="s num">批次 {k.batch_size} · 间隔 {k.flush_interval_s}s</div>
              </div>
              <div className="stat-card">
                <div className="k">待结算队列</div>
                <div className="v num">{k.queue.pending}</div>
                <div className="s num">done {k.queue.done} · failed {k.queue.failed} · expired {k.queue.expired}</div>
              </div>
              <div className="stat-card">
                <div className="k">累计链上入账</div>
                <div className="v num">{fromRaw(k.cumulative_charged_raw)} USDT</div>
                <div className="s num">{k.cumulative_charged_count} 笔 · raw={k.cumulative_charged_raw}</div>
              </div>
              <div className="stat-card">
                <div className="k">最近一批</div>
                <div style={{ fontSize: 13, marginTop: 6 }}>
                  {k.last_batch ? (
                    <div className="mono" style={{ overflowX: "auto" }}>{JSON.stringify(k.last_batch)}</div>
                  ) : (
                    <span className="dim">暂无（等待下一批 {k.flush_interval_s}s）</span>
                  )}
                </div>
              </div>
            </div>
          )}
        </AsyncSection>
      </div>
    </div>
  );
}

function ProviderRow(props: { idx: number; wallet: string; name: string | null; agentId: number | null; revenue: string; revenueRaw: number; count: number }) {
  const proof = useAsync<ProofResponse | null>(() => coreApi.providerProof(props.wallet).catch(() => null), [props.wallet]);
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr>
        <td className="num">{props.idx}</td>
        <td>
          <div>{props.name ?? <span className="dim">（未登记名称）</span>}</div>
          <div className="mono dim">{props.wallet.slice(0, 8)}…{props.wallet.slice(-6)}</div>
        </td>
        <td className="num">{props.agentId ?? "-"}</td>
        <td className="num">
          {props.revenue} <span className="dim">raw={props.revenueRaw}</span>
        </td>
        <td className="num">{props.count}</td>
        <td>
          <button className="btn small secondary" onClick={() => setOpen(!open)}>
            {open ? "收起 proof" : `看 proof`}
          </button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6} style={{ background: "var(--surface-2)" }}>
            {proof.loading ? (
              <Spinner label="拉取链上 proof…" />
            ) : proof.error ? (
              <ErrorBox error={proof.error} />
            ) : proof.data ? (
              <div>
                <div className="dim" style={{ marginBottom: 8 }}>
                  共 {proof.data.count} 笔 Charged 事件 · 每笔可点交易哈希到 scan.bohr.life 核对：
                </div>
                <table className="list">
                  <thead>
                    <tr>
                      <th>交易</th>
                      <th>区块</th>
                      <th>金额</th>
                      <th>nonce</th>
                    </tr>
                  </thead>
                  <tbody>
                    {proof.data.events.slice(0, 20).map((e, i) => (
                      <tr key={`${e.tx_hash}-${e.log_index}-${i}`}>
                        <td>
                          <TxLink hash={e.tx_hash} />
                        </td>
                        <td className="num">{e.block_number}</td>
                        <td className="num">
                          {e.value} <span className="dim">raw={e.value_raw}</span>
                        </td>
                        <td className="mono dim">{e.nonce.slice(0, 12)}…</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {proof.data.count > 20 && <div className="dim">仅展示最近 20 笔（共 {proof.data.count} 笔）</div>}
              </div>
            ) : (
              <div className="dim">该地址暂无链上 Charged 事件。</div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
