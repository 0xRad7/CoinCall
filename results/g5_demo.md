# G5 十五步 Demo 运行记录

| 步骤 | 名称 | 状态 | 说明 |
|---|---|---|---|
| 1 | 服务就绪 | PASS | bot_chain_api |
| 2 | chain/health | PASS | rpc/bundler/explorer |
| 3 | chain/info | PASS | chainId=968 block 25287604→25287610 |
| 4 | chain/gas | PASS | gasPrice=20gwei baseFee=0 |
| 5 | accounts 生成 A | PASS | 0x7236d0bd6E32d14eE946ff5156b552B3202A6aE2 |
| 6 | faucet/claim | PASS | 余额=0.000000000000000000 BOT（领水 https://faucet.bohr.life/basic，Turnstile 强制） |
| 7 | tx/transfer dry_run | PASS | gas=25200 预览未上链 |
| 8 | tx/transfer 真实 | NEEDS_FUNDS | A 无余额；领水后重跑（https://faucet.bohr.life/basic） |
| 9 | tokens/{USDT}/info | PASS | USDT decimals=6 |
| 10 | aa/execute 4337 | NEEDS_FUNDS | 预览失败 |
| 11 | agent-identity/register | NEEDS_FUNDS | dry_run 预览已验证 calldata |
| 12 | bdex/quote USDT→WBOT | PASS | 1 USDT → 61435122547562298 raw WBOT |
| 13 | bdex/swap/execute | NEEDS_FUNDS | 预览 gas=25200（需先 approve） |
| 14 | indexer sync×2 + status | PASS | logs 水位→25287636；表计数 {"blocks": 0, "transactions": 0, "logs": 0, "token_transfers": 0, "agent_identit |
| 15 | 调试页 /docs（Swagger） | PASS | G4 已存证 55 路径可交互 |

汇总：{"PASS": 11, "NEEDS_FUNDS": 4}

NEEDS_FUNDS 步骤领水后重跑：`python scripts/demo_g5.py`
