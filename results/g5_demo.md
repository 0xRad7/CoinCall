# G5 十五步 Demo 运行记录

| 步骤 | 名称 | 状态 | 说明 |
|---|---|---|---|
| 1 | 服务就绪 | PASS | bot_chain_api |
| 2 | chain/health | PASS | rpc/bundler/explorer |
| 3 | chain/info | PASS | chainId=968 block 25380508→25380512 |
| 4 | chain/gas | PASS | gasPrice=20gwei baseFee=0 |
| 5 | accounts 复用 A | PASS | 0x4411FE870FffD5378e53a72D2D18C53e12598c7b |
| 6 | faucet/claim + 注资 A | PASS | A 余额=0.783543500000000000 BOT / 9.000000 USDT（faucet https://faucet.bohr.life/basic，Turnstile 强制；兜底=服务出资账户划转） |
| 7 | tx/transfer dry_run | PASS | gas=25200 预览未上链 |
| 8 | tx/transfer 真实 | PASS | {"dry_run": false, "tx_hash": "0x8470f82f4f00fb51c4e8ccbc362eccb6bde7e3417788eab31dbaf6037abcfc49", "status": 1, "block_ |
| 9 | tokens/{USDT}/info | PASS | USDT decimals=6 |
| 10 | aa 4337 建户+入金+estimate | PASS | 智能账户=0x1D0E0732A62B… 模拟通过 vgl=74249（偏差 #17）上链受 bundler 限制 |
| 11 | agent-identity/register | PASS | tx=0x6ff88c782a14b4a46b |
| 12 | bdex/quote USDT→WBOT | PASS | 1 USDT → 61434355522183574 raw WBOT |
| 13 | bdex/swap/execute | PASS | {"dry_run": false, "tx_hash": "0xa8953f4ead051b69b6602541cdbfcaa7944360b8fb874a9c71239e4d9cb16fa1", "status": 1, "block_ |
| 14 | indexer sync×2 + status | PASS | logs 水位→25380534；表计数 {"blocks": 0, "transactions": 0, "logs": 5448, "token_transfers": 0, "agent_iden |
| 15 | 调试页 /docs（Swagger） | PASS | G4 已存证 55 路径可交互 |

汇总：{"PASS": 15}

NEEDS_FUNDS 步骤领水后重跑：`python scripts/demo_g5.py`
