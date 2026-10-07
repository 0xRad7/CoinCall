# 08 — 并发构建计划（v2，对齐 09 任务书）

> v2 变更：任务卡与 09 的 P0-x/P1-x 一一对应；新增 PayVault 合约线与 Base Sepolia 可选线；契约冻结点更新（v1 的 ledger 契约作废）。

## 1. 依赖图

```
P0-1 身份端点(coincall-bot-chain-api 仓库) ──┐
P0-2 PayVault 合约 ──► P0-3 地基+契约冻结 ──► P0-4 402网关 ──┐
                                    ├──► P0-5 keeper ─────────┼──► 合流(p1门)
                                    └──► P1-1 SDK（fixture 先行）├──► P1-2 接入层 ──► P1-4 演示
                                         P1-3 数据(fixture) ────┘
（W9 Base Sepolia 可选线独立，随时插入）
```

三线起步（P0-1 ‖ P0-2 ‖ P0-3 的骨架部分）→ 契约冻结后四线并发（P0-4/P0-5/P1-1/P1-3）→ P1-2 → P1-4 收口。

## 2. 契约冻结点（并行的前提，P0-3 落盘后只读）

1. `contracts/payment.py`：X-PAYMENT 头格式 + Authorization 六元组 + **EIP-712 digest 构造（与 PayVault 合约 golden vector 双向锁定）** + 402 质询模型
2. `contracts/schemes.py`：payment_scheme / ChainAdapter 两个 Protocol
3. `contracts/calls.py`：calls + settle_queue 表模型（status 枚举含 settled/bad_debt）
4. `contracts/manifest.py`：ServiceManifest（含 chain 字段）

## 3. 任务卡（与 09 一一对应，此处只列执行要点）

| 卡 | 范围 | DoD 出处 | 提交/标记 |
|---|---|---|---|
| W1 | P0-1 三端点（coincall-bot-chain-api 仓库，先读源码存档定签名格式） | 09 P0-1 | 该仓 tag `d7-api-ext` |
| W2 | P0-2 PayVault 合约+编译部署+全链路测试 | 09 P0-2 | 新仓 `feat(payvault)` |
| W3 | P0-3 骨架+四契约+空跑绿 | 09 P0-3 | 新仓 tag `p0-base` |
| W4 | P0-4 网关（mock provider 开发） | 09 P0-4 | `feat(gateway)` |
| W5 | P0-5 keeper（四路用例先行） | 09 P0-5 | `feat(keeper)` |
| W6 | P1-1 SDK+MCP skill | 09 P1-1 | `feat(sdk)` |
| W7 | P1-2 接入层（官方形态对齐） | 09 P1-2 | `feat(registry)` |
| W8 | P1-3 数据排行 | 09 P1-3 | `feat(data)` |
| W9 | （可选 V1.5）Base Sepolia 最小闭环：eip3009-native scheme 适配器 + 一条真 3009 结算演示 | 00 §4/04 §6 | `feat(base-adapter)`，1d，绝不阻塞主线 |
| W10 | P1-4 演示集成与彩排 | 09 P1-4 | tag `p2-demo` |

## 4. 并行纪律（沿用已验证模式）

- 每卡一个 general-purpose 子智能体，prompt 附：09 对应节全文 + 契约文件路径 + DoD；
- 契约文件只读，发现契约问题上报而非绕过；
- 两仓对接唯一约定：BOT_CHAIN_API_BASE → 8010 常驻服务；W1 上线需重启该服务（DuckDB 单进程互斥，C-14 教训）；
- 每卡完成即静态三连+该卡测试绿+conventional commit（带 scope）；合流时全量门；
- 新仓阶段 tag：`p0-base` → `p1-merge`（四线合流全绿）→ `p2-demo`；
- 犯错沉淀 CONSTRAINTS §D 续编（C-NN）。

## 5. 时间线（满负荷 5 天；40h 赛时口径见 09 末节）

| 时段 | 内容 |
|---|---|
| D1 上午 | W1‖W2‖W3（三线起步）；W1 内先做源码定案与 solc 冒烟两个"悬念消除"动作 |
| D1 下午–D2 | W2 收尾；W4‖W5‖W6‖W8 四线并发 |
| D3 上午 | W7 接入层；`p1-merge` 全量门 |
| D3 下午 | W10 演示彩排；（可选 W9） |
