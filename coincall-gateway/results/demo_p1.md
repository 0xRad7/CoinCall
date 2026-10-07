# P1-4 三幕演示彩排实录（T24 绑定）

- 彩排时间：2026-10-05T15:40:31.070230+00:00；链：BOT Chain 测试网 968
- PayVault `0xFe91F55C0e7Ccbc4A6619C67544Ab453cf79C471` / MockUSDT `0x4F8f2eaAA3988E9f59B72C93262DDC1084E540fb`（6 位精度）
- 剧本：coincall-docs/07；脚本：scripts/demo_p1.py；consumer=anvil#1，provider 收款钱包=anvil#2（公开测试账户，私钥不上日志）

- `23:40:32` === 预检：三服务健康 + keeper 开关 + 链 RPC ===
- `23:40:32` 8010 healthz OK
- `23:40:32` 8020 healthz OK
- `23:40:32` 8030 healthz OK
- `23:40:32` keeper: batch_size=3 queue={'pending': 0, 'done': 16, 'failed': 0, 'expired': 0}
**开场大屏 /stats/overview（演示前）**

```
{
 "gmv_raw": 10280000,
 "gmv": "10.28",
 "charged_count": 23,
 "calls_success_total": 16,
 "calls_aborted_total": 1,
 "services_total": 4,
 "services_active": 4,
 "providers_registered": 1,
 "providers_with_revenue": 3,
 "synced_to_block": 25807132,
 "sources": {
  "revenue": "onchain Charged(PayVault) via bot-chain-api /contracts/logs",
  "activity": "gateway /internal/stats/calls"
 },
 "degraded": []
}
```

- `23:40:35` 钱包就绪：consumer=0x70997970C51812dc3A010C7d01b50e0d17dc79C8 provider=0xB4AD544f875b908110E9c8dF1C54F1b1b0BBd79F (BOT: #1=9731951684181293298 #2=99552600000000000 wei)
- `23:40:35` === 第一幕：挂服务（任何 Agent 能力，30 秒变成收费服务） ===
- `23:40:37` provider 登记：agent_id=162 链上 agentWallet=0xadee6d874cec4b8dba0c969585bd2e593ddfba3d
- `23:40:37` manifest 发布 svc_translate @ 0.01 USDT hash=sha256:5fa24a4ae2610d1a4…
- `23:40:37` manifest 发布 svc_contract_scan @ 0.02 USDT hash=sha256:80b905ffb5ef13797…
- `23:40:37` manifest 发布 svc_chain_report @ 0.05 USDT hash=sha256:b0f3c55249751af75…
- `23:40:37` catalog 可见 ['svc_chain_report', 'svc_contract_scan', 'svc_e2e_demo', 'svc_translate']；第一幕耗时 2.3s（≤30s 口径 PASS）
- `23:40:37` === 第二幕：付费调用（Agent 只需要会 HTTP） ===
- `23:40:37` SDK 导入消费者钱包 anvil#1：0x70997970C51812dc3A010C7d01b50e0d17dc79C8（私钥不出本进程）
- `23:40:41` core 签发 api key（key_id=key_64c2042d1ceb）；SDK 预算闸 budget_raw=2000000
- `23:40:41` --- 402 指引路径演示：未 approve 的新 key ---
- `23:40:41` SDK 本地新钱包（新 key：先 mint 出余额，保持未 approve）：0xB35C14bEC0F14690cFb0ea78Dd0Fd7395bdBC2e4
- `23:40:43` 新钱包 gas 预备（#1 → fresh 0.05 BOT）：15333f06a52202823c33ada73349b83a9a758751b980fd27024c443676106057
- `23:40:47` 新钱包 mint 0.1 USDT：tx=9a291f39c02ea5dd6653333a66ca6ef3da3780e073581c7c6cf102c4321cbce9
- `23:40:53` 402 质询命中：code=insufficient_allowance（SDK 已把质询转成人话指引）
**PaymentRequiredError（SDK 402 指引原文）**

```
402 insufficient_allowance: 对 PayVault 的授权额度不足
下一步: 对 PayVault 的授权额度不足（本笔 amount_raw=10000）。请执行 wallet.approve_vault('0.01') 向合约 0xFe91F55C0e7Ccbc4A6619C67544Ab453cf79C471 授权后重试。
```

- `23:40:56` 照指引执行 wallet.approve_vault('0.1')：tx=4d59fc652f741c7d91e7d73ebe368f1961c43f4f9904f0bc6cbbe1ee36ba56d5
- `23:40:56` 等网关链上约束短缓存过期（30s，approve 可见性）再重试
- `23:41:40` approve 后重试成功：receipt=rcp_a63afeb30d28 X-Charged-Raw=10000
- `23:41:40` --- anvil#1 经 SDK 对三个服务各发起真实付费调用 ---
**svc_translate 收据（X-Charged-Raw 上屏）**

```
{
 "receipt_id": "rcp_0821dca421f9",
 "X-Charged-Raw": "10000",
 "X-Receipt-Sig": "a551383e97c2acbc83d55d69…",
 "translated": "[coincall 内置翻译兜底] Agent economies need verifiable settlement.",
 "engine": "internal-fallback"
}
```

**svc_contract_scan 结构化摘要（付费结果）**

```
{
 "receipt_id": "rcp_ee61d30161d0",
 "X-Charged-Raw": "20000",
 "count": 15,
 "total_value": "0.210000",
 "window": {
  "from_block": 25802238,
  "to_block": 25807237,
  "blocks": 5000
 },
 "latest": {
  "tx_hash": "0x7526c705c77b0d69de6b74d480ad00839b901e5f029bba39734311dc1a82f5b4",
  "block_number": 25803147,
  "log_index": 1,
  "provider": "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
  "from": "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
  "value_raw": 10000,
  "value": "0.010000",
  "nonce": "0xad514911cc4b4ed11c4706f1e3f9122a0e7452983f7deee0f51b00573a628769"
 }
}
```

**svc_chain_report 报告全文（付费结果）**

```
CoinCall 链报告（BOT Chain 测试网 968）
链通道健康: ok
  rpc: ok (312ms)
  bundler: ok (381ms)
  explorer: ok (849ms)
平台总调用 19（成功 19 / 中止 1）
链上 GMV 10.28 USDT（Charged 23 笔）
服务 4/4 active，Providers 1（有收入 3）
同步至块: 25807244
```

- `23:41:59` 第二幕合计 4 笔付费调用、SDK 本地预算计数 90000 raw（= 定价总和 90000）
- `23:41:59` === 第三幕：结算与信誉（收入就是最硬的信誉，每一笔都在链上） ===
- `23:42:35` keeper 新批上链（2 个批次 tx）：0x509b67c1b9a43a65…, 0x33527cf3bea1d975…
**链上 Charged 明细（大屏）**

```
0x509b67c1b9a43a65300715e600f981be76eb39cd0b4ebb7bb2047d1831d9fcba  block=25807245  0.010000 USDT  provider=0xb4ad544f875b908110e9c8df1c54f1b1b0bbd79f from=0xb35c14bec0f14690cfb0ea78dd0fd7395bdbc2e4
0x509b67c1b9a43a65300715e600f981be76eb39cd0b4ebb7bb2047d1831d9fcba  block=25807245  0.010000 USDT  provider=0xb4ad544f875b908110e9c8df1c54f1b1b0bbd79f from=0x70997970c51812dc3a010c7d01b50e0d17dc79c8
0x509b67c1b9a43a65300715e600f981be76eb39cd0b4ebb7bb2047d1831d9fcba  block=25807245  0.020000 USDT  provider=0xb4ad544f875b908110e9c8df1c54f1b1b0bbd79f from=0x70997970c51812dc3a010c7d01b50e0d17dc79c8
0x33527cf3bea1d975563e05b9b3a702fc8f90fe7a375db8da4e5b8cb229cd4db9  block=25807292  0.050000 USDT  provider=0xb4ad544f875b908110e9c8df1c54f1b1b0bbd79f from=0x70997970c51812dc3a010c7d01b50e0d17dc79c8
```

- `23:42:35` --- providerWithdraw（anvil#2 本地直签，msg.sender=provider） ---
**providerWithdraw 到账对账**

```
{
 "tx": "e61f6b155e34cfa02b91f7d465051add04bedcac904e7266a1f343f8a5c76579",
 "explorer": "https://scan.bohr.life/tx/e61f6b155e34cfa02b91f7d465051add04bedcac904e7266a1f343f8a5c76579",
 "credits_before_raw": 220000,
 "usdt_balance_before_raw": 0,
 "usdt_balance_after_raw": 220000,
 "到账增量（raw）": 220000,
 "断言": "增量 == 提取额 == credits_before；提现后 credits == 0",
 "Withdrawn 事件数": 0
}
```

- `23:42:39` --- 排行榜 / proof / overview 刷新 ---
**排行榜（/leaderboard/providers，收入优先）**

```
{
 "order": "revenue",
 "providers": [
  {
   "wallet": "0xc37ffe97b4d2c3d0187b1ddedf273e52a461b63a",
   "display_name": null,
   "agent_id": null,
   "revenue_raw": 10000000,
   "revenue": "10",
   "charged_count": 1
  },
  {
   "wallet": "0xb4ad544f875b908110e9c8df1c54f1b1b0bbd79f",
   "display_name": null,
   "agent_id": null,
   "revenue_raw": 220000,
   "revenue": "0.22",
   "charged_count": 11
  },
  {
   "wallet": "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
   "display_name": null,
   "agent_id": null,
   "revenue_raw": 150000,
   "revenue": "0.15",
   "charged_count": 15
  },
  {
   "wallet": "0xadee6d874cec4b8dba0c969585bd2e593ddfba3d",
   "display_name": "CoinCall Demo Provider (booth)",
   "agent_id": 162,
   "revenue_raw": 0,
   "revenue": "0",
   "charged_count": 0
  }
 ]
}
```

**链上 proof（/leaderboard/providers/0xB4AD544f…/proof）**

```
{
 "revenue": "0.22",
 "revenue_raw": 220000,
 "count": 11,
 "events（前 3 笔）": [
  {
   "tx": "0x1a9efff6cb71ccdcde89d2b46b857ad208bc9a01faf1b82b2df9ad3329a088f7",
   "value": "0.01",
   "url": "https://scan.bohr.life/tx/0x1a9efff6cb71ccdcde89d2b46b857ad208bc9a01faf1b82b2df9ad3329a088f7"
  },
  {
   "tx": "0x1a9efff6cb71ccdcde89d2b46b857ad208bc9a01faf1b82b2df9ad3329a088f7",
   "value": "0.01",
   "url": "https://scan.bohr.life/tx/0x1a9efff6cb71ccdcde89d2b46b857ad208bc9a01faf1b82b2df9ad3329a088f7"
  },
  {
   "tx": "0x1a9efff6cb71ccdcde89d2b46b857ad208bc9a01faf1b82b2df9ad3329a088f7",
   "value": "0.02",
   "url": "https://scan.bohr.life/tx/0x1a9efff6cb71ccdcde89d2b46b857ad208bc9a01faf1b82b2df9ad3329a088f7"
  }
 ]
}
```

**收尾大屏 /stats/overview（演示后）**

```
{
 "gmv_raw": 10370000,
 "gmv": "10.37",
 "charged_count": 27,
 "calls_success_total": 20,
 "calls_aborted_total": 1,
 "services_total": 4,
 "services_active": 4,
 "providers_registered": 1,
 "providers_with_revenue": 3,
 "synced_to_block": 25807305,
 "sources": {
  "revenue": "onchain Charged(PayVault) via bot-chain-api /contracts/logs",
  "activity": "gateway /internal/stats/calls"
 },
 "degraded": []
}
```

- `23:42:45` GMV 10.28 → 10.37 USDT；provider 收入（链上 Charged 真相）revenue=0.22 count=11
- `23:42:45` === 兜底演练：svc_translate 端点热切换（07 §4 友队掉线风险行） ===
- `23:42:45` PATCH=upsert 切友队 http_json 端点 hash=sha256:b280673ca64b95a4c…
- `23:42:45` 等待网关 manifest 缓存 TTL 过期（60s 切换传播上限）→ 友队端点生效
- `23:43:57` http_json 形态真实外联成功：receipt=rcp_91718eaac2e6 body.provider_endpoint=友队翻译-team
- `23:44:07` 友队掉线 → 502 provider_failed（provider 超时/网络错误: ）
- `23:44:07` 失败=从未扣款：SDK spent_raw 不变（90000）、gateway aborted +1 → 2
- `23:44:07` PATCH=upsert 切我方备用端点 hash=sha256:5cc30f418cac53817…
- `23:44:07` 等待网关 manifest 缓存 TTL 过期（60s 切换传播上限）→ 备用端点生效
- `23:45:19` 备用端点承接成功：receipt=rcp_2bdecc6d7414（调用不中断，同一 SDK 形态）
- `23:45:19` PATCH=upsert 切回 internal://translate 兜底 hash=sha256:7d15279a6e6dca71f…
- `23:45:19` 等待网关 manifest 缓存 TTL 过期（60s 切换传播上限）→ internal 兜底生效
- `23:46:30` 切回 internal 兜底成功：receipt=rcp_533beaa1d8a5 engine=internal-fallback
**彩排结论**

```
三幕全程无人工干预；402 指引→approve→成功闭环；keeper 批量结算上链；
providerWithdraw 到账增量==提取额；友队掉线=零扣款，manifest 热切换三段式
（友队→备用→internal）全部恢复服务。切换传播时延=manifest 缓存 TTL 60s。
诚实边界：overview 数字即全部真实发生过的调用，无 GMV 修饰。
```

## 附注（彩排后人工核验与工程勘误，2026-10-05 子智能体 C）

1. **Withdrawn 事件解码勘误**：上表"Withdrawn 事件数: 0"是脚本比对缺陷（本仓 web3 版本
   `HexBytes.hex()` 无 `0x` 前缀），非链上缺失。人工核验 withdraw tx
   `0xe61f6b155e34cfa02b91f7d465051add04bedcac904e7266a1f343f8a5c76579`（block 25807299）：
   PayVault 日志 topic0=`0xd1c19fbc…e5fb`（Withdrawn 签名哈希命中），
   data 解码 amount=220000 == credits_before == USDT 到账增量。脚本已修复
   （补 `0x` 前缀 + Withdrawn 金额断言），下轮彩排起自动核验。

2. **彩排耗时拆分**（本实录 23:40:32 → 23:46:30，全程 5 分 58 秒，无人工干预）：
   - 第一幕 2.3s（≤30s 口径）
   - 第二幕 82s（含 402 演练的 approve 后 32s 链上约束短缓存等待）
   - 第三幕 96s（keeper 两批结算 36s + withdraw 4s + 榜单刷新）
   - 兜底演练 3 分 45s（三段热切换 × 60s manifest 缓存 TTL 传播等待，为诚实口径不缩短）

3. **彩排过程中踩平的坑（已固化进脚本，重演不复发）**：
   - raw tx 必须显式带 `to`：省略会被签名成"合约创建"上链（回执 status=1 但 value 沉入
     空创建、收款方余额不增；此前 3 笔 ≈0.156 BOT 测试网损失，即"53000 intrinsic"真相）；
   - SDK 默认幂等键=参数 hash：跨彩排重演同参数会被网关重放 200（不重复扣款，符合设计），
     演示逐笔计费需显式传 `idempotency_key=uuid4()`；
   - approve/mint 后网关链上约束有 30s 短缓存：402→approve→重试需等缓存过期（记录在案，
     属 02 §5c 设计行为，非遗漏）；
   - 8010 偶发瞬断会使 internal 型付费调用 502（aborted 零扣款）：脚本对 502 类错误内置
     5s×4 重试（新幂等键+新 nonce，不双扣）。

4. **诚实边界**：provider 收款钱包 0xB4AD…79F（任务给定的 #2 私钥本地派生地址；
   任务文本标注的 0x3C44…93BC 与该私钥不对应，链上 0x3C44 的历史 0.15 credits 无本方
   私钥、未动）。排行榜 display_name 为 null 的收入行=收款钱包未做 provider 登记
   （登记表 wallet 以 ERC-8004 链上 agentWallet 为准，agent 162=0xadee…ba3d），
   收入数字本身以链上 Charged 为唯一真相，未做任何修饰。
