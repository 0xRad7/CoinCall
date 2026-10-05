# CoinCall Core（主仓库）

CoinCall —— BOT Chain 上的 x402 式按次付费 Agent 服务层。本仓承载**管理面服务**（manifest 目录 / api key / 钱包绑定记录 / 排行榜）与**项目级制品链**（intents/ specs/ plans/ tests/，无远端，评审以本地自查替代）。

## 仓库族

| 目录 | 角色 |
|---|---|
| coincall-core | 管理面（本仓，端口 8020） |
| coincall-gateway | 数据面 402 网关（端口 8030） |
| coincall-bot-chain-api | 链适配层（端口 8010） |
| coincall-contracts | PayVault 合约仓 |
| coincall-docs | 规范文档族（00~09，对本仓只读） |

## 制品链

当前活动链：`20261005202947`（CoinCall P0）。发起人 2026-10-05 授权：制品落盘即视为验收留痕，无需逐制品审核。

## 全量测试入口

见 `AGENTS.md`（W3 落地后生效；在此之前以各子仓 README 为准）；本仓用例绑定明细见 `tests/README.md`。

## Provider 接入

五步上架即售（注册身份→绑钱包→登记→发布→上榜）：见 `PROVIDER_ONBOARDING.md`。
