# tests/ — 绑定明细（TEST-20261005221052 CoinCall P1）

> 绑定索引：`TEST-20261005221052-coincall-p1.md`（T16~T24）；本文件落本仓用例明细。
> 跑法：`uv run pytest -q -m unit --cov=app --cov-fail-under=80`（门禁）；
> live 级 `uv run pytest -m live`（需 8010/8030 在跑，真链只读）。

| # | 规范条目 | 可执行用例（标识） | 级别 | 状态 |
|---|---|---|---|---|
| T9 | 01 §3 manifest 校验（精度/endpoint/schema/chain） | `test_manifest.py` | unit | 绿（P0） |
| T10 | 03 §2 签发明文只回显一次 + validate 往返 | `test_apikey.py` | unit | 绿（P0） |
| T20 | 03 §2 apikey 吊销立即生效/列表/换绑 | `test_apikey_lifecycle.py`（9 例） | unit | 绿（P1-2） |
| T21 | 01 Provider 登记校验链上 8004 身份存在 | `test_providers.py`（unit 9 例：404/409 契约、短缓存 MockTransport、upsert、manifests 关联；live 2 例：真身份 162 登记、未知 id 422） | unit+live | 绿（P1-2） |
| T22 | 06 收入优先排序/空库优雅/proof 哈希清单 | `test_leaderboard.py::TestLeaderboardUnit`（11 例：排序/水位增量分窗/空库/proof/双源降级） | unit | 绿（P1-3） |
| T23 | 06 overview GMV == 链上 Charged 总额 | `test_leaderboard.py::TestGmvMatchesOnchainLive::test_gmv_matches_onchain`（overview 库存 vs 直连 8010 /contracts/logs 逐笔直和，I4 口径，不用流水表） | live | 绿（P1-3） |

## 实测契约注记（写进用例的链上事实）

- 8010 identity 视图对未注册 tokenId 返回 **409 tx_reverted**（"不存在或未注册"），非 404——两种形态都映射 422 `identity_not_found`（`test_identity_409_revert_treated_as_not_found`）。
- PayVault 部署块 25795948（eth_getCode 实证）；历史 Charged 8 笔共 10_070_000 raw（provider `0xc37f…b63a` 1 笔 10_000_000 + `0x3c44…93bc` 7 笔 ×10_000）——T23 以此为历史下限。
