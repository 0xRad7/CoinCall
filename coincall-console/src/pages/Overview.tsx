/** 总览页（公共目录+决策视图，两端共用）：三段式骨架——指标排（证据成对）/ 收入榜 / 比价 / 目录 / keeper 观测。 */
import { useState } from "react";
import { coreApi, decisionApi, type DecisionRow, type ProofResponse } from "../api/core";
import { gatewayApi } from "../api/gateway";
import { useAsync } from "../lib/useAsync";
import { AsyncSection, Badge, CopyButton, ErrorBox, Spinner, TxLink } from "../components/ui";
import { EvidencePair, PageHeader, StatCard } from "../components/shell";
import { gatewayCallUrl, fromRaw, EXPLORER_HOST, EXPLORER_TX, EXPLORER_URL } from "../chain/constants";

export default function Overview() {
  const overview = useAsync(() => coreApi.overview(), []);
  const providers = useAsync(() => coreApi.leaderboardProviders(), []);
  const [poll, setPoll] = useState(true);
  const keeper = useAsync(() => gatewayApi.keeperStatus(), [], { pollMs: poll ? 10_000 : 0 });
  // 决策徽章数据（一次拉取映射到目录卡）
  const decision = useAsync(() => decisionApi.services({ window_hours: 168, sort: "score" }), []);
  const decisionMap = new Map((decision.data?.services ?? []).map((r) => [r.service_id, r]));

  return (
    <div>
      <PageHeader
        title="总览"
        sub="平台全局状态：链上 GMV（唯一真相）、服务目录、keeper 结算观测。数据每 30s 内随访问刷新。"
        actions={
          <a className="btn small secondary" href={EXPLORER_URL} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
            区块浏览器 ↗
          </a>
        }
      />

      {/* 指标排（四卡，关键数字独立层级 + 证据成对） */}
      <AsyncSection state={overview}>
        {(o) => (
          <>
            <div className="stat-grid">
              <StatCard
                k="链上 GMV（Charged 总额）"
                value={
                  <>
                    {o.gmv}
                    <span className="unit">USDT</span>
                  </>
                }
                sub={`raw=${o.gmv_raw} · ${o.charged_count} 笔`}
                evidence={<EvidencePair hash={`charged_count=${o.charged_count} · synced_to_block=${o.synced_to_block}`} href={EXPLORER_URL} label={`去 ${EXPLORER_HOST} 核对 Charged 事件`} />}
              />
              <StatCard k="调用笔数（成功 / 中止）" value={`${o.calls_success_total ?? "-"} / ${o.calls_aborted_total ?? "-"}`} sub="中止 = Provider 失败未扣款" />
              <StatCard k="服务（在售/全部）" value={`${o.services_active} / ${o.services_total}`} sub="paused 不参与调用" />
              <StatCard k="Provider（有收入/注册）" value={`${o.providers_with_revenue} / ${o.providers_registered}`} sub={`已同步到区块 ${o.synced_to_block}`} />
            </div>
            {o.degraded.length > 0 && (
              <div className="alert warn">
                <b>部分数据降级</b>：{o.degraded.join("、")} —— 展示值可能滞后，以链上（{EXPLORER_HOST}）为准。
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

      {/* 同类比价（决策视图） */}
      <DecisionView />

      {/* 服务目录（左右结构：左=团队列表，右=选中团队的服务；管理员视图，团队主页数据去 Provider 工作台看） */}
      <div className="card">
        <h3>服务目录</h3>
        <CatalogTeamsSplit decisionMap={decisionMap} />
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
              <StatCard
                k="状态"
                value={k.enabled && k.running ? <Badge kind="ok">运行中</Badge> : <Badge kind="err">停止</Badge>}
                sub={`批次 ${k.batch_size} · 间隔 ${k.flush_interval_s}s`}
              />
              <StatCard k="待结算队列" value={k.queue.pending} sub={`done ${k.queue.done} · failed ${k.queue.failed} · expired ${k.queue.expired}`} />
              <StatCard
                k="累计链上入账"
                value={
                  <>
                    {fromRaw(k.cumulative_charged_raw)}
                    <span className="unit">USDT</span>
                  </>
                }
                sub={`${k.cumulative_charged_count} 笔 · raw=${k.cumulative_charged_raw}`}
              />
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
                  共 {proof.data.count} 笔 Charged 事件 · 每笔可点交易哈希到 {EXPLORER_HOST} 核对：
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

function pct(x: number | null | undefined): string {
  return x == null ? "-" : `${Math.round(x * 100)}%`;
}

/** 四分量迷你条形（rev/ful/fb/fresh 各自 score_component；配色纳入单色体系，随主题双态） */
function ScoreBars({ row }: { row: DecisionRow }) {
  const parts: Array<{ key: string; label: string; v: number | null; w: number; color: string }> = [
    { key: "rev", label: `收入 ${(row.components.revenue.score_component * 100).toFixed(0)}%`, v: row.components.revenue.score_component, w: 0.5, color: "var(--chart-1)" },
    { key: "ful", label: `履约 ${pct(row.components.fulfillment.score_component)}`, v: row.components.fulfillment.score_component, w: 0.3, color: "var(--chart-2)" },
    { key: "fresh", label: `新鲜 ${pct(row.components.freshness.score_component)}`, v: row.components.freshness.score_component, w: 0.2, color: "var(--chart-4)" },
  ];
  return (
    <div style={{ display: "flex", gap: 1, alignItems: "center", height: 8, width: 140, borderRadius: 4, overflow: "hidden", background: "var(--code-bg)" }} title={parts.map((p) => `${p.label}（权重 ${p.w}）`).join(" · ")}>
      {parts.map((p) => (
        <div key={p.key} style={{ width: `${p.w * 100}%`, height: "100%", display: "flex" }}>
          <div style={{ width: "100%", background: "var(--code-bg)" }}>
            <div style={{ width: `${Math.max(0, Math.min(1, p.v ?? 0)) * 100}%`, height: "100%", background: p.color, opacity: 0.75 }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function timeAgo(iso: string | null): string {
  if (!iso) return "从未";
  const h = (Date.now() - new Date(iso).getTime()) / 3_600_000;
  if (h < 1) return `${Math.round(h * 60)} 分钟前`;
  if (h < 48) return `${h.toFixed(1)} 小时前`;
  return `${(h / 24).toFixed(1)} 天前`;
}

/** 决策视图：类目 chips + score 排序 + 时间拨针 */
export function DecisionView() {
  const [cat, setCat] = useState<string | null>(null);
  const [asOf, setAsOf] = useState<string>(""); // 空=现在
  const cats = useAsync(() => decisionApi.categories(), []);
  const rows = useAsync(
    () => decisionApi.services({ category: cat ?? undefined, window_hours: 168, as_of: asOf || undefined, sort: "score" }),
    [cat, asOf]
  );

  return (
    <div className="card">
      <div className="flex" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <h3 className="mb-0">同类比价（决策视图）</h3>
        <div className="flex" style={{ gap: 6 }}>
          <label className="dim" style={{ fontSize: 12 }}>时间拨针：</label>
          <input
            type="datetime-local"
            value={asOf}
            onChange={(e) => setAsOf(e.target.value)}
            aria-label="时间拨针（as_of）"
            style={{ width: 200 }}
          />
          {asOf && (
            <button className="btn small secondary" onClick={() => setAsOf("")}>
              回到现在
            </button>
          )}
        </div>
      </div>
      <p className="card-desc" style={{ marginTop: 6 }}>
        按综合分排序：score = 0.5×收入 + 0.3×履约 + 0.2×新鲜度（详见帮助页「决策层怎么算的」）。拖动时间拨针看排序随时间衰减重排。
      </p>

      {cats.data && (
        <div className="flex" style={{ marginBottom: 10, gap: 6, flexWrap: "wrap" }} role="group" aria-label="类目筛选">
          <button className={`btn small ${cat === null ? "" : "secondary"}`} onClick={() => setCat(null)}>
            全部（{cats.data.total_active}）
          </button>
          {cats.data.categories.map((c) => (
            <button key={c} className={`btn small ${cat === c ? "" : "secondary"}`} onClick={() => setCat(c)}>
              {c}（{cats.data!.counts[c] ?? 0}）
            </button>
          ))}
        </div>
      )}

      <AsyncSection state={rows} empty="该类目暂无在售服务">
        {(d) => (
          <table className="list">
            <thead>
              <tr>
                <th>#</th>
                <th>服务</th>
                <th>价格</th>
                <th>综合分</th>
                <th>四分量</th>
                <th>关键徽章</th>
              </tr>
            </thead>
            <tbody>
              {d.services.map((r, i) => (
                <tr key={r.service_id}>
                  <td className="num">{i + 1}</td>
                  <td>
                    <b>{r.name}</b>
                    <div className="mono dim" style={{ fontSize: 11 }}>{r.service_id} · {r.category}</div>
                  </td>
                  <td className="num">{r.price}</td>
                  <td className="num">
                    <b>{r.score.toFixed(4)}</b>
                  </td>
                  <td>
                    <ScoreBars row={r} />
                  </td>
                  <td>
                    <span className="flex" style={{ gap: 4, flexWrap: "wrap" }}>
                      <span className="badge ok" title="网关侧履约成功率">
                        ✓ {pct(r.components.fulfillment.success_rate)}
                      </span>
                      {r.components.fulfillment.p95_ms != null && (
                        <span className="badge muted" title="p95 延迟">
                          p95 {r.components.fulfillment.p95_ms}ms
                        </span>
                      )}
                      <span className="badge muted" title={`最近活跃 ${timeAgo(r.components.freshness.last_activity_at)}（48h 半衰期）`}>
                        🕐 {timeAgo(r.components.freshness.last_activity_at)}（衰减 {pct(r.components.freshness.score_component)}）
                      </span>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </AsyncSection>
    </div>
  );
}


/** 目录左右结构：左=团队列表（收入聚合），右=选中团队的服务详情卡。 */
function CatalogTeamsSplit({ decisionMap }: { decisionMap: Map<string, DecisionRow> }) {
  const catalog = useAsync(() => coreApi.catalog(), []);
  const [selTeam, setSelTeam] = useState<string | null>(null);

  return (
    <AsyncSection state={catalog} empty="目录为空">
      {(c) => {
        const groups = new Map<string, { display_name: string; agent_id: number | null; services: typeof c.services }>();
        for (const s of c.services) {
          const key = s.manifest.provider.display_name || s.manifest.provider.wallet;
          const g = groups.get(key) ?? { display_name: s.manifest.provider.display_name, agent_id: s.manifest.provider.agent_id, services: [] as typeof c.services };
          g.services.push(s);
          groups.set(key, g);
        }
        const keys = [...groups.keys()];
        const active = selTeam ?? keys[0] ?? "";
        const g = groups.get(active);
        const rev = [...decisionMap.values()].filter((r) => g?.services.some((sv) => sv.service_id === r.service_id));
        const teamRev = rev.reduce((a, r) => a + BigInt(r.components.revenue.total_raw), BigInt(0));
        return (
          <div className="catalog-split">
            <aside className="catalog-team-list">
              {keys.map((k) => {
                const grp = groups.get(k)!;
                const r = [...decisionMap.values()].filter((d) => grp.services.some((sv) => sv.service_id === d.service_id));
                const rv = r.reduce((a, d) => a + BigInt(d.components.revenue.total_raw), BigInt(0));
                return (
                  <button key={k} type="button" className={`catalog-team-item${k === active ? " active" : ""}`} onClick={() => setSelTeam(k)}>
                    <span className="ct-name">{grp.display_name || "（未命名团队）"}</span>
                    <span className="ct-meta">{grp.services.length} 服务 · {fromRaw(rv)} USDT</span>
                  </button>
                );
              })}
            </aside>
            <div className="catalog-team-detail">
              {g && (
                <>
                  <div className="flex" style={{ justifyContent: "space-between", marginBottom: 10 }}>
                    <div>
                      <span className="svc-name">{g.display_name || "（未命名团队）"}</span>
                      <span className="dim" style={{ fontSize: 12, marginLeft: 8 }}>
                        {g.agent_id != null ? `team #${g.agent_id}` : "无链上身份"} · 团队主页数据请到 Provider 工作台查看
                      </span>
                    </div>
                    <span className="badge ok" title="团队聚合收入（链上 Charged）">{fromRaw(teamRev)} USDT</span>
                  </div>
                  <div className="svc-grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))" }}>
                    {g.services.map((s) => {
                      const dr = decisionMap.get(s.service_id);
                      return (
                        <div className="svc-card" key={s.service_id}>
                          <div className="flex" style={{ justifyContent: "space-between" }}>
                            <span className="svc-name">{s.manifest.name}</span>
                            <Badge kind={s.status === "active" ? "ok" : "warn"}>{s.status}</Badge>
                          </div>
                          <div className="mono dim">{s.service_id}</div>
                          <div style={{ margin: "8px 0" }}>
                            <span className="svc-price">{s.manifest.pricing.amount} {s.manifest.pricing.token}</span>
                            <span className="dim"> / 次</span>
                          </div>
                          {dr && (() => {
                            const anchor = typeof dr.components.fulfillment.proof === "object" ? dr.components.fulfillment.proof : null;
                            return (
                              <div className="flex" style={{ gap: 4, rowGap: 6, flexWrap: "wrap", marginBottom: 10 }} title="决策层履约信号">
                                <span className="badge ok">✓ {pct(dr.components.fulfillment.success_rate)}</span>
                                {dr.components.fulfillment.p95_ms != null && <span className="badge muted">p95 {dr.components.fulfillment.p95_ms}ms</span>}
                                <span className="badge muted">🕐 {timeAgo(dr.components.freshness.last_activity_at)}</span>
                                {anchor && (
                                  <a className="badge muted" style={{ textDecoration: "none" }} href={EXPLORER_TX(anchor.anchor_tx)} target="_blank" rel="noreferrer" title={`锚定 ${anchor.digest.slice(0, 16)}…`}>
                                    🔗 {anchor.digest.slice(7, 15)}
                                  </a>
                                )}
                              </div>
                            );
                          })()}
                          <div className="call-endpoint-box">
                            <div className="dim" style={{ fontSize: 11 }}>CoinCall 调用端点</div>
                            {/* URL 独占整行换行，复制按钮单独一行——长域名 break-all 与按钮同行会互相穿插重叠 */}
                            <div className="mono" style={{ fontSize: 11, wordBreak: "break-all", lineHeight: 1.6, margin: "4px 0" }}>
                              POST {gatewayCallUrl(s.service_id)}
                            </div>
                            <div style={{ marginTop: 2 }}>
                              <CopyButton text={`POST ${gatewayCallUrl(s.service_id)}`} label="复制调用端点" />
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
            </div>
          </div>
        );
      }}
    </AsyncSection>
  );
}

