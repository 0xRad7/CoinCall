# G5 十五步 Demo 运行记录

| 步骤 | 名称 | 状态 | 说明 |
|---|---|---|---|
| 1 | 服务就绪 | PASS | bot_chain_api |
| 2 | chain/health | PASS | rpc/bundler/explorer |
| 3 | chain/info | PASS | chainId=968 block 25385464→25385468 |
| 4 | chain/gas | PASS | gasPrice=20gwei baseFee=0 |
| 5 | accounts 复用 A | PASS | 0x4411FE870FffD5378e53a72D2D18C53e12598c7b |
| 6 | faucet/claim + 注资 A | PASS | A 余额=0.676167360000000000 BOT / 8.000000 USDT（faucet https://faucet.bohr.life/basic，Turnstile 强制；兜底=服务出资账户划转） |
| 7 | tx/transfer dry_run | PASS | gas=25200 预览未上链 |
| 8 | tx/transfer 真实 | PASS | {"dry_run": false, "tx_hash": "0x68387286b8de21275b759fdb4999b551a057e9a7e1e5f76ab56c6e37a994be5a", "status": 1, "block_ |
| 9 | tokens/{USDT}/info | PASS | USDT decimals=6 |
| 10 | aa/execute 4337 上链 | PASS | 智能账户=0x396aDE5137… UserOp success tx=0xb895c1466abd38a9… |
| 11 | agent-identity/register | PASS | tx=0x1b79e11d0dabde5f67 |
| 12 | bdex/quote USDT→WBOT | PASS | 1 USDT → 61362400499936254 raw WBOT |
| 13 | bdex/swap/execute | PASS | {"dry_run": false, "tx_hash": "0x965863a550bbec8d866bdc4f0d24cec6e6c10aed3ccbcaa28c926aa8e2336da8", "status": 1, "block_ |
| 14 | indexer sync×2 + status | PASS | logs 水位→25385507；表计数 {"blocks": 0, "transactions": 0, "logs": 5977, "token_transfers": 0, "agent_iden |
| 15 | 调试页 /docs（Swagger） | PASS | G4 已存证 55 路径可交互 |

汇总：{"PASS": 15}

NEEDS_FUNDS 步骤领水后重跑：`python scripts/demo_g5.py`
