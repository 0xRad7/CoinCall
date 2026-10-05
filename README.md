# CoinCall 控制台（coincall-console）

服务端配置与消费端配置的一站式 Web 控制台：把「登记 Provider / 发布服务 / 绑定收款钱包 / 提现」与「本地钱包 / 资金 / API key / 付费调用」全部变成点击流，不再逐个 curl 接口。

- 技术栈：React 18 + Vite 5 + TypeScript（strict）+ ethers v6 + react-router（Hash 路由）
- 运行时**零 CDN**：所有依赖打进构建产物，现场断网（后端在本地时）可用
- 中文 UI；私钥只存 sessionStorage（关页即清），签名全部在浏览器进程内完成

## 安装

```bash
cd coincall-console
npm install          # 直连 registry；失败时走代理：HTTPS_PROXY=http://127.0.0.1:7890 npm install
```

要求 Node ≥ 18（开发机为 v24）。无需全局安装任何工具。

## 开发

```bash
npm run dev          # http://127.0.0.1:5173  （host 127.0.0.1，端口固定 5173）
```

dev server 内置**同源代理**（避免后端跨域问题）：

| 前缀 | 目标 | 服务 |
|---|---|---|
| `/api/core/*` | `http://127.0.0.1:8020` | coincall-core（目录/登记/manifest/apikey/榜单） |
| `/api/gw/*` | `http://127.0.0.1:8030` | coincall-gateway（/call、keeper 观测） |
| `/api/chain/*` | `http://127.0.0.1:8010` | coincall-bot-chain-api（身份/钱包绑定） |
| （浏览器直连） | `https://rpc.bohr.life/` | BOT Chain RPC（CORS=*，chainId 968） |

## 构建

```bash
npm run build        # tsc --noEmit + vite build → dist/
npm run preview      # 同样走 5173，带同一套代理，可直接验收构建产物
```

## 常用脚本与测试

```bash
npm run typecheck    # tsc --noEmit（零错误）
npm test             # vitest：黄金向量 + API 封装 + 表单校验（28 用例）
COINCALL_E2E=1 npx vitest run tests/e2e.live.test.ts
                     # 真实后端 E2E（需 dev server + 三个后端 + RPC 可达）：
                     # 代理读目录 → 链上余额 → 本地签名 → X-PAYMENT → /call 200+收据 → keeper 新批
```

黄金向量：`tests/golden.test.ts` 读取 `../coincall-contracts/vectors/eip712_golden.json`（缺失自动 skip），
用 anvil#0 私钥对每个 case 断言 domainSeparator / structHash / digest / v / r / s **逐字段一致**，
并验证 X-PAYMENT 组装函数走同一 digest 代码路径（`src/chain/signing.ts` 是全仓唯一签名出口）。

## 页面速览

- **总览**（默认）：GMV/笔数/服务/Provider 四卡 + degraded 提示、Provider 收入榜（proof 交易外链 scan.bohr.life）、服务目录卡片、keeper 结算观测（10s 轮询可开关）
- **Provider 工作台**：五步向导 ①登记（链上身份实时预检）→ ②发布服务（amount↔amount_raw 联动、endpoint 类型切换、schema JSON 校验、422 逐字段定位）→ ③我的服务（暂停/恢复/改价=重发 manifest，带二次确认）→ ④收款钱包绑定（EIP-712 typed data 展示、注入钱包/粘贴签名双模式、5 分钟 deadline 倒计时）→ ⑤提现（PayVault credits eth_call + providerWithdraw）
- **消费端工作台**：①钱包生成/导入（安全须知折叠、key 文件下载、销毁会话密钥）→ ②资金三数 + 铸造 + 授权滑条（pending→confirmed + 交易外链）→ ③API key 一次性明文展示 + 「已保存」确认 → ④试用调用（按 input_schema 动态表单；本地 EIP-712 签名组 X-PAYMENT；402 质询转成「去授权」动作按钮）→ ⑤调用历史（localStorage，可清空）→ ⑥预算护栏
- **帮助**：三服务关系图（纯 CSS）、错误码人话表、私钥安全声明

## 端口说明

| 端口 | 用途 |
|---|---|
| 5173 | 本控制台（dev 与 preview 一致） |
| 8010 / 8020 / 8030 | 三个后端服务（只读依赖，由控制台代理访问） |

> 注意：若直接以 `file://` 或其它静态服务器打开 `dist/`（无同源代理），对 8010/8020/8030 的请求会因跨域失败——请始终通过 `npm run dev` / `npm run preview` 访问。
