# D3 探测结论（2026-10-01，全部实测）

## 1. ERC-8004 三合约 ABI（关键成果）
- 三合约均为 ERC1967Proxy（UUPS），浏览器只验证了代理壳；**implementation 地址经 EIP-1967 slot 直读获得**：
  - IdentityRegistry → impl `0xdb74b629fe35cab514edc310dc9ad0ff68a1eb7c`（IdentityRegistryUpgradeable，源码已验证）
  - ReputationRegistry → impl `0x706634b5c419b5992fb8dc88da2ab36823d0fe90`（ReputationRegistryUpgradeable）
  - ValidationRegistry → impl `0xeed5f847b22074bb753ad0356072b15f73c28048`（ValidationRegistryUpgradeable）
- 关键方法（ABI 已存 `abi_IdentityRegistry.json` 等）：
  - 注册：`register()` / `register(string)` / `register(string,(string,bytes)[])`
  - 视图：`getAgentWallet(uint256)`（蓝图猜测的函数真实存在）、`getMetadata(uint256,string)`、`ownerOf`、`tokenURI`
  - 写：`setAgentWallet(uint256,address,uint256,bytes)`、`setMetadata(uint256,string,bytes)`、`setAgentURI(uint256,string)`
  - Reputation：`giveFeedback/readFeedback/readAllFeedback/getSummary/getResponseCount/appendResponse`
  - Validation：`validationRequest/validationResponse/getAgentValidations/getValidationStatus/getSummary`
- IdentityRegistry 实测 `name()`="AgentIdentity"、`symbol()`="AGENT"（蓝图写 name=AGENT，偏差已记录）

## 2. 4337 Bundler
- `POST https://bundler.bohr.life/rpc/` `eth_supportedEntryPoints` → `["0x0000000071727de22e5e9d8baf0edac6f37da032"]`
  （EntryPoint v0.7 与 chains.py 一致，UserOp 通道前提成立）

## 3. SimpleAccountFactory / V2Router ABI
- SimpleAccountFactory（verified）：`createAccount(address,uint256)` / `getAddress(address,uint256)` / `accountImplementation()`
- V2Router：标准 Uniswap V2 Router02 24 函数全集（swapExactTokensForTokens 等）

## 4. Faucet：**自动领水不可行**（定案）
- 真实 API（前端 chunk 逆向）：`https://api-faucet.bohr.life/botchain/api/v1/faucet`
  - `GET /info` 免验证开放：BOT 10 枚/次、冷却 0.1667h（≈10 分钟）；**另有 tUSDT 1000 枚/次（erc20）**
  - `POST /claim` payload `{address, turnstileToken, asset}`：**Cloudflare Turnstile 人机验证强制**
    （假 token 实测返回 `400 {"code":10003,"message":"Turnstile verification failed"}`）
- 错误码表（chunk 逆向）：10001/10006/10008 send_failed、10002 invalid_address、10003 turnstile_failed、10004/10005 rate_limited、10007 service_unavailable
- **结论**：M10 做成 status 探测（/info 透传）+ claim 可带用户从浏览器取的 turnstileToken 代发；
  无 token 时返回 `manual_required` + 手动指引（https://faucet.bohr.life/basic）。
  needs_funds 用例无 BOT_CHAIN_TEST_PRIVATE_KEY 时显式 skip（铁律 A5）。
  手动领水同时可得 10 BOT + 1000 USDT（BDEX swap 测试资金来源，README 注明）。

## 对蓝图的偏差/补充（并入 RESULTS.md 偏差清单）
- 02 篇"fixture 自动领水"不可实现（Turnstile），按其自身的兜底路径走手动+skip
- 03 篇 M10 "探测端点是否可自动化" → 探测完成，答案是否（半自动：token 代发）
- IdentityRegistry name=AgentIdentity（非 AGENT）
