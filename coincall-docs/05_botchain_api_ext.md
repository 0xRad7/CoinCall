# 05 — coincall-bot-chain-api 端点扩展设计（W0 先行任务）

> 背景：铁律 P1/P2——付费服务层的一切链上操作经 coincall-bot-chain-api；本文件是其**缺口端点**的完整设计，开发仍落在 coincall-bot-chain-api 仓库（沿用其测试先行 + 静态门纪律），排 D7 阶段。

## 1. 既有能力映射（已验证可用，付费层直接消费）

| 付费层用途 | coincall-bot-chain-api 既有端点 | 状态 |
|---|---|---|
| 注册 8004 身份 | `POST /api/v1/agent-identity/register` | ✅ 已上线（needs_funds 实测过） |
| 身份/钱包聚合视图 | `GET /api/v1/agent-identity/{tokenId}` | ✅ 已上线 |
| USDT 转账（结算/提现/自助调拨） | `POST /api/v1/tokens/erc20/transfer` | ✅ 已上线 |
| USDT 授权 | `POST /api/v1/tokens/erc20/approve` | ✅ 已上线 |
| 交易回执/事件 | `GET /api/v1/tx/{hash}`、`/tx/{hash}/events` | ✅ 已上线 |
| 余额查询 | `GET /api/v1/accounts/{addr}/balances` | ✅ 已上线 |
| 代管账户 | `POST /api/v1/accounts` | ✅ 已上线 |
| 通用合约调用（兜底） | `POST /api/v1/contracts/call` / `send` | ✅ 已上线 |

## 2. 缺口清单（本文件新增设计）

| # | 缺口 | 消费方 | 优先级 |
|---|---|---|---|
| A | `setAgentWallet` 端点（绑定收款钱包） | 01 Provider 接入 ② | **P0** |
| B | 8004 metadata 读写端点（manifest hash 锚定） | 01 Provider 接入 ⑤ | P2 |
| C | 注册结果解析（register 回执 → agentId） | 01 Provider 接入 ① | **P0** |

## 3. 端点 A：绑定/更新 agentWallet（P0）

```
POST /api/v1/agent-identity/{token_id}/wallet
{
  "owner":        "0x…",          // 必须是 tokenId 的 owner（服务代管账户或出资账户）
  "wallet_address": "0x…",        // 新收款地址
  "dry_run": true                  // 默认预览（铁律 A4 同源）
}

200（dry_run=true）  → TxPreview（calldata 预览，方法 setAgentWallet(uint256,address,uint256,bytes)）
200（dry_run=false） → TxReceiptSummary
```

- 链上方法（已验证 ABI）：`IdentityRegistryUpgradeable.setAgentWallet(uint256 agentId, address newWallet, uint256 deadline, bytes signature)`；
- **签名参数（2026-10-05 源码定案，W1 实证上链——推翻本文档早期预判，详见 bot-chain-api 仓 CONSTRAINTS C-23 与 results/w1_setagentwallet_findings.md）**：
  - 签名为 **EIP-712 typed data v4**（非 EIP-191）：typehash `AgentWalletSet(uint256 agentId,address newWallet,address owner,uint256 deadline)`；
  - 域：name `ERC8004IdentityRegistry` / version `1` / chainId 968 / verifyingContract = 代理 `0xec8f…99c0`（OZ 5.4 升级版每次重算，另有 `eip712Domain()`（IERC5267）可链上对账）；
  - **签名者必须是 newWallet 本人**（ecrecover==newWallet，或其 ERC-1271）——身份 owner 不参与签名，owner/被授权者只作交易 msg.sender；空签名 `0x` 永远 revert；
  - 时间窗：`now ≤ deadline ≤ now+300s`（MAX_DEADLINE_DELAY=5 分钟）；无 nonce；NFT 转移时 agentWallet 自动清空。
- 校验：owner == `ownerOf(token_id)`（否则 409 tx_reverted/422 service_error）。

## 4. 端点 B：8004 元数据读写（P2）

```
PUT  /api/v1/agent-identity/{token_id}/metadata
     {owner, key: "service_manifest", value: "<manifest_json 或 hash>", dry_run}
GET  /api/v1/agent-identity/{token_id}/metadata/{key}   → {key, value}
```

- 链上方法：`setMetadata(uint256,string,bytes)` / `getMetadata(uint256,string)`（ABI 已取得）；
- 用途锚定：manifest 全文或 sha256 hash 写入，目录读者可链上核验；
- 大小限制：value ≤ 4KB（超限则只存 hash，全文走服务）。

## 5. 端点 C：注册结果解析（P0）

```
GET /api/v1/agent-identity/register-result/{tx_hash}
→ {tx_hash, status, agent_ids: [137], owner, wallet}   // 解析回执 Transfer mint 事件
   未上链 → {found: false}
```

- 实现：复用 `GET /tx/{hash}/events` 的 Transfer 解码逻辑，收敛为语义化端点（付费层 01 的接入流程强依赖"register 之后拿到 agentId"，现在要人工拼事件，体验断裂）。

## 6. 开发与验收（在 coincall-bot-chain-api 仓库执行）

- 模块归属：M6 `modules/erc8004.py` 增端点；ABI 补充进 `core/abis/erc8004.py`（setAgentWallet/setMetadata 已在已验证 ABI 中，缺则从 d3_probes 补录）；
- 测试先行：每端点 unit（dry_run 预览/权限错误映射）+ needs_funds（真实绑定与元数据写入、register-result 对真实注册回执的解析）；
- 质量门：`ruff check && ruff format --check && mypy app && pytest -m unit --cov=app --cov-fail-under=80`；
- 提交：`feat(m6): …`，tag `d7-api-ext`；
- **首日探测项（写进 08 的 W0 任务卡）**：
  1. ~~setAgentWallet 签名格式探测~~ 已定案（源码实证 EIP-712/newWallet 签名，见 §3）；
  2. `setMetadata` bytes 编码对 UTF-8 JSON 的兼容；
  3. register 回执 Transfer 事件稳定字段（from=0x0）。

## 6.5 官方 API 形态对齐（2026-10-06 实测官方文档后增补）

官方 Agent OS 已发布 Identity API 文档（服务暂不可达），其端点形态是现成坐标系：
- 官方 `POST /v1/agents/identities` ↔ 我们的 `POST /agent-identity/register`
- 官方 `POST /v1/agents/identities/{id}/wallet-binding` ↔ 我们的端点 A
- 官方 `PUT /v1/agents/identities/{id}/metadata` ↔ 我们的端点 B
- 官方 `GET /v1/agents/by-erc8004/{chain}/{registry}/{agentId}` ↔ 我们的聚合视图
**纪律**：本文件三端点的请求/响应字段命名向官方文档靠拢（如 wallet-binding 的
readiness/异步语义）；官方服务开放后，已接我们的调用方换 base_url 即可迁移。
coincall-bot-chain-api 由此获得第二身份：官方 Agent OS 的开源自托管实现。

## 7. 明确不做

- 不为付费层新增任何"账本/结算"类端点（那是新服务自己的域，链上只需既有 transfer 能力）；
- 不做多签/治理类端点（方向已被否决，见 07 附录）。
