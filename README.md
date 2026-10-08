# CoinCall 控制台（coincall-console）

服务端配置与消费端配置的一站式 Web 控制台：把「登记 Provider / 注册身份 / 发布服务 / 绑定身份钱包 / 提现」与「本地钱包 / 资金 / API key / 付费调用」全部变成点击流，不再逐个 curl 接口。

- 技术栈：React 18 + Vite 5 + TypeScript（strict）+ ethers v6 + react-router（Hash 路由）
- 运行时**零 CDN**：所有依赖打进构建产物，现场断网（后端在本地时）可用
- 中文 UI；**控制台不接触任何私钥**——连接浏览器钱包（OKX / MetaMask，EIP-6963 多注入发现+选择器+legacy 回退）的入口有三处共用同一 `connect()` 代码路径：**顶栏（未连接时常驻）**、发布表单「服务收款钱包」旁的内联按钮、消费端工作台 ① 教学卡；只读地址，签名与交易全部由扩展弹窗在本地完成；链不对时自动引导切链/添加 BOT Chain 测试网（switch 失败 4902 → add）

## 安装

```bash
cd coincall-console
npm install          # 直连 registry；失败时走代理：HTTPS_PROXY=http://127.0.0.1:7890 npm install
```

要求 Node ≥ 18（开发机为 v24）。无需全局安装任何工具。

## 开发

```bash
npm run dev          # http://127.0.0.1  （host 0.0.0.0，端口固定 80——局域网免带端口）
```

dev server 内置**同源代理**（避免后端跨域问题）：

| 前缀 | 目标 | 服务 |
|---|---|---|
| `/api/core/*` | `http://127.0.0.1:8020` | coincall-core（目录/登记/manifest/apikey/榜单） |
| `/api/gw/*` | `http://127.0.0.1:8030` | coincall-gateway（/call、keeper 观测）；跨机访问走同源代理，目录卡调用端点动态生成（可用 VITE_GATEWAY_PUBLIC_URL 覆盖，见 .env.example） |
| `/api/chain/*` | `http://127.0.0.1:8010` | coincall-bot-chain-api（身份/钱包绑定） |
| （浏览器直连） | `https://rpc.bohr.life/` | BOT Chain RPC（CORS=*，chainId 968） |

## 构建

```bash
npm run build        # tsc --noEmit + vite build → dist/
npm run preview      # 同样走 80 端口，带同一套代理，可直接验收构建产物
```

## 常用脚本与测试

```bash
npm run typecheck    # tsc --noEmit（零错误）
npm test             # vitest：黄金向量 + API 封装 + 表单/动线/视觉护栏（175 用例）
COINCALL_E2E=1 npx vitest run tests/e2e.live.test.ts
                     # 真实后端 E2E（需 dev server + 三个后端 + RPC 可达）：
                     # 代理读目录 → 链上余额 → 本地签名 → X-PAYMENT → /call 200+收据 → keeper 新批
```

黄金向量：`tests/golden.test.ts` 读取 `../coincall-contracts/vectors/eip712_golden.json`（缺失自动 skip），
用 anvil#0 私钥对每个 case 断言 domainSeparator / structHash / digest / v / r / s **逐字段一致**，
并验证 X-PAYMENT 组装函数走同一 digest 代码路径（`src/chain/signing.ts` 是全仓唯一签名出口）。

## 页面速览与动线

动线：**`/welcome`（登录+选身份单页，两态）→ `/provider` | `/consumer`（双工作台）**；`/` 总览为公共目录+决策视图（两端共用）。

- **`/welcome` 落地页（登录 = 连接钱包，两态单页）**：未连接=深色叙事登录面——产品一句话标题+三个记忆点（30 秒上架 / 0.01 USDT 一次 / 失败不扣款）+ 居中窄卡「Connect Wallet」通栏大按钮（同一 EIP-6963 连接流）+ 次级入口「先逛逛目录 →」（`/?browse=1` 免连浏览）+ 底部实时链上数据自证（GMV/在售服务数，读 overview，附 scan 外链）；连接成功后**同屏切换**为亮色工具面的身份二选一大卡——**I'm a provider**（创建团队、上架服务、收入直进你的钱包）/ **I'm a consumer**（浏览服务、授权一次、按次付费调用），身份色（Provider 深蓝 / Consumer 青绿）首次出现；选择后进对应工作台并 localStorage 粘滞（`coincall.mode`）。已连接且已选过身份的用户再访 `/welcome` 自动进对应工作台；顶栏模式 pill（`?switch=1`）随时回来换身份。
- **`/` 总览（公共目录+决策视图）**：未连接默认落 `/welcome`，`?browse=1` 可免连浏览。三段式骨架：指标排四卡（GMV/笔数/服务/Provider，关键数字大号等宽 + 「数字+证据」成对——哈希缩写与 scan 外链小图标）+ degraded 提示、Provider 收入榜（proof 交易外链 scan.bohr.life）、同类比价决策视图（四分量条形配色纳入单色体系）、服务目录卡片（Teams/类目双轴）、keeper 结算观测（10s 轮询可开关）
- **Provider 工作台**：五步向导 ①登记（链上身份实时预检；无身份可展开「注册一个」——平台代发铸造→自动回填 agentId→引导绑定身份钱包）→ ②发布服务（服务收款钱包默认=连接地址（未连接时字段旁内联「连接钱包自动填」，已连接有「使用当前钱包」快填一键回填）、amount↔amount_raw 联动、endpoint 类型切换 + method GET/POST（GET 参数映射上游 query、嵌套即时预警）、schema 编辑器带「这是什么？」引导与模板、示例请求区（GET 键值行/POST 示例 JSON）、「探测接口」平台代发（护栏+临时头不落盘）并可用响应「生成 Schema」（递归推断+深度 4 层截断+「自动识别，请核对」）、422 逐字段定位；http_json 可配「上游认证头」——Fernet 加密存 core、网关转发时注入、消费者不可见，发布后链式保存失败不回滚 manifest）→ ③我的服务（所有权=服务收款钱包==连接钱包 ∪ 身份∈我认领集合（GET /providers 的 claim_wallet 权威+①步上下文加速），未连接只给内联引导、无全量兜底；行内归属徽标（服务收款钱包=你/身份#N=你认领）与冒用橙警示；改价/改状态基于 internal 通道全量 manifest（含真实上游 url，公开 API 恒脱敏；自己行内可见 url）；暂停/恢复/改价=重发 manifest，带二次确认；http_json 服务管理上游认证头：头名 chips 值不回显、更新=全量替换带确认、清除二次确认；服务行显示 method 徽标）→ ④收款钱包绑定（EIP-712 typed data 展示、注入钱包/粘贴签名双模式、5 分钟 deadline 倒计时）→ ⑤提现（PayVault credits eth_call + providerWithdraw）
- **消费端工作台**：①连接钱包（扩展弹窗 eth_requestAccounts 只拿地址；顶栏常驻地址 chip + 链徽标 + 断开；链≠968 一键引导 switch→add）→ ②资金状态面板（只读三数 + 「获取 USDT」水龙头指引 + 刷新——approve 操作已并入④）→ ③API key 三层换机体验（本机已保存：掩码+服务端实检+绑定钱包不符黄条；粘贴原机器 key：验证并启用；为当前钱包签发新 key：主推动作+一次性明文+自动实检；名下 N 个 key 辅助行说明明文不可找回）→ ④试用调用（卡内「① 授权额度 → ② 支付调用」两步动线：充足自动折叠直达支付、不足内联授权默认=单价×10、402 自动展开①预置金额；②为按 input_schema 动态表单 + 扩展弹窗 signTypedData 签 EIP-712（v 归一 0/1→27/28）+4001 友好取消；已授权会话 localStorage 记忆直达②，历史标「含授权」）→ ⑤调用历史（localStorage，可清空；每条成功记录可「评价」——星级+一句话，用该次调用的链上收据五元组+Ed25519 签名提交，一收据一评，409/401 人话提示，已评价徽章）→ ⑥预算护栏。**没有扩展的演示机**：第一步末尾有折叠的「没有浏览器钱包？」→「创建一次性演示钱包」兜底（随机生成、sessionStorage、关页即焚、仅测试网，与主路径强隔离；沿用黄金向量锁定的本地 digest 签名路径）
- **帮助**：三服务关系图（纯 CSS）、错误码人话表、私钥安全声明

## 视觉系统（AIsa 设计语言迁移）

- **三段式页面骨架**每页固定：H1+副语+右上主动作 → 指标统计卡排 → 内容网格；侧栏任务分组（公共/工作台/支持）+ 每项一句灰字副标注
- **纸片卡**：卡面/1px 细描边/8px 小圆角/无阴影；三层中性面（页面底/卡面/输入底）；间距 8 的倍数（4 半步）；卡内边距 20
- **字阶**：正文 14 / 辅助 12-13 / 卡题 16-18 / H1 24-28；**关键数字独立层级**（28px、600 字重、tabular-nums 等宽）
- **色彩**：一个主色（#2563eb，兼 Provider 身份）+ Consumer 青绿（#0d9488）+ 三档低饱和状态色；色彩只给状态与主按钮
- **「数字+证据」成对模式**（CoinCall 差异化）：收入/结算/GMV 等关键数字旁配哈希缩写与 scan 外链小图标（`.evidence` / `EvidencePair`）
- **暗色模式**为完整第二形态（`[data-theme=dark]`：卡面比背景亮一档、描边亮灰、图表色提亮），顶栏一键切换并 localStorage 持久化（`coincall.theme`，首帧防闪跳）
- **动效**只做状态反馈（loading/hover/展开轻过渡），无装饰动画、无玻璃拟态/渐变/大阴影

## 端口说明

| 端口 | 用途 |
|---|---|
| 80 | 本控制台（dev 与 preview 一致；局域网访问免端口号） |
| 8010 / 8020 / 8030 | 三个后端服务（只读依赖，由控制台代理访问） |

> 注意：若直接以 `file://` 或其它静态服务器打开 `dist/`（无同源代理），对 8010/8020/8030 的请求会因跨域失败——请始终通过 `npm run dev` / `npm run preview` 访问。
