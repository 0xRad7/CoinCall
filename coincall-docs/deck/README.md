# CoinCall 演示片（deck/）

本目录有两个版本的单文件演示片，**讲法不同、各有用途**：

| 文件 | 叙事 | 用途 |
|---|---|---|
| `deck.html` | **产品叙事（当前主版本，11 页）**：用户问题 → 解决什么 → 产品能力 → 用户故事 → 机制 → 信任 → 时机 → 进度与演示。全程用户视角，只讲用户可感知的东西（30 秒上架 / 0.01 USDT 一次 / 失败不扣款）。 | 讲给组员、黑客松赛题教练；对外展示的默认材料 |
| `deck_v1_internal.html` | **内部立项叙事（存档，13 页）**：官方能力矩阵与侦察结论、方向演化史（三次否定）、六轮设计裁决、仓库族与端口、路线图任务书。 | 讲给内部工程师、复盘立项推理；**不含对外演示用途** |

两版均为单文件 HTML：零外部依赖（无 CDN / 网络字体 / 外链图片），现场断网可用，操作方式相同。

## 怎么打开

- 浏览器双击 `deck.html`（或 `deck_v1_internal.html`），Chrome / Safari / Edge 均可。
- 或命令行：`open "/Users/rad/Documents/S1&ETHwuhan/coincall-docs/deck/deck.html"`（路径含 `&`，必须加引号）。

## 翻页操作（两版相同）

| 操作 | 效果 |
|---|---|
| 键盘 `←` / `→`（或空格、PageUp/PageDown、Home/End） | 上一页 / 下一页 |
| 点击屏幕左侧 25% 区域 / 右侧区域 | 上一页 / 下一页 |
| 屏幕左右边缘 `‹` `›` 按钮 | 上一页 / 下一页 |
| 封面底部「本片脉络 / 论证线」导航条 | 点击条目跳转对应章节起始页 |

右下角页码指示（`NN / 11`），底部金色进度条随翻页推进。

## 页面清单（deck.html · 产品叙事 · 11 页）

| 页 | 标题 | 论点 |
|---|---|---|
| 01 | 封面 | 让 Agent 能力按次收费，让 Agent 调用按次付费——Agent 经济缺一个支付层，我们把它做出来了 |
| 02 | 用户问题 | 机器对机器的交易今天没有支付方式：供给侧卖不了按次、消费侧买不成按次（订阅对机器不成立 / 没处买 / 不敢预充值）；人有支付宝，Agent 什么都没有 |
| 03 | 我们解决什么 | Agent 世界的按次付费层（对齐 x402 方向）：按次明码标价 / 钱不经平台 / 失败不扣款 |
| 04 | 产品能力总览 | 六件事：30 秒上架、机读目录+商店页、api key 凭证、一次调用=一笔签名支付、链上结算随时提现、SDK+两个 MCP 工具 |
| 05 | 用户故事·供给侧 | 从能力到收入：上架 → 被发现 → 收入实时进账 → 收入即信誉 |
| 06 | 用户故事·消费侧 | 从想用到用上：挑服务 → 备好小钱包 → 自动付费调用 → 花得放心（失败不扣款、额度自封顶） |
| 07 | 怎么解决的 | 一次付费调用六步旅程（凭证→挑服务→签名授权→校验转发→成功才扣款→收入上链）+ 三个信任设计：钱只走链上合约 / 成功才扣 / 浪费有上限 |
| 08 | 为什么可信 | 不靠承诺靠结构：不托管资金 / 结算规则开源可查 / 每笔收入链上公开；不做托管、不做裁判、不抽佣 |
| 09 | 为什么是现在 | 方向已是共识（x402、官方路线图点名）→ 链上还没人跑通 → 我们率先做出闭环；官方未来上线=多一条通道，兼容共存 |
| 10 | 进度与演示（实录） | 五件套（管理面 / 网关 / 链适配 / 合约 / SDK）全部交付，端到端闭环真链跑通；三幕彩排全程 5 分 58 秒零人工干预，挂服务实测 2.3 秒（30 秒口径），Provider 提现到账 0.22 USDT 链上可核 |
| 11 | 收尾记忆点 | 人有支付宝和 Stripe，Agent 现在有了 CoinCall——30 秒上架 / 0.01 USDT 一次 / 失败不扣款（承诺口径锚点不变；角落小字：累计链上结算 30+ 笔） |

## 更新方式（改哪一节）

- 每页在对应 HTML 里都有注释路标：`<!-- PAGE 07 · 怎么解决的 -->`，搜索页号即可定位到 `<section class="slide">`，直接改文案。
- **写作纪律（deck.html 专属）**：这是产品叙事版——不出现工程词与内部术语（端口号、仓库名、合约名、并发参数、迁移分级、worker 编号等），数字只用用户可感知的三个（30 秒 / 0.01 USDT / 失败不扣款）。需要讲工程细节时切 `deck_v1_internal.html`。
- 新增页面：复制一个 `<section class="slide">…</section>` 插入，页码与进度条自动适配（JS 统计 slide 数）；同步更新封面导航条与本清单。
- 旧版 `deck_v1_internal.html` 已冻结存档，一般不再修改；若确要改，其自身的 PAGE 注释体系与 13 页结构仍然有效。

## 自查命令（改完建议重跑，以 deck.html 为例）

```bash
# 1. 零外部资源引用（应无输出）
grep -E 'https?://' "/Users/rad/Documents/S1&ETHwuhan/coincall-docs/deck/deck.html"

# 2. 结构校验：可解析、标签闭合、slide 数=页码总数、无工程禁词
python3 - <<'EOF'
from html.parser import HTMLParser
import re
VOID={'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
class P(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.stack=[]; self.errs=[]; self.slides=0
    def handle_starttag(self,t,a):
        if t not in VOID: self.stack.append(t)
        if t=='section' and 'slide' in dict(a).get('class','').split(): self.slides+=1
    def handle_endtag(self,t):
        if t in VOID: return
        if not self.stack or self.stack[-1]!=t: self.errs.append(f'mismatch </{t}>')
        else: self.stack.pop()
src=open("/Users/rad/Documents/S1&ETHwuhan/coincall-docs/deck/deck.html",encoding="utf-8").read()
p=P(); p.feed(src)
banned=re.compile(r'端口|8020|8030|8010|coincall-|PayVault|keeper|K=3|L0|L1|L2|L3|W1|W2|W3|gwei|黄金向量|影子闸门|死锁|三次否定|六轮|isort|needs_funds|4337|EIP-|nonce|eth_call|manifest|ERC-')
print("slides:",p.slides," unclosed:",p.stack," errors:",p.errs or "none")
print("banned terms:", banned.findall(src) or "none")
assert p.slides==11 and not p.stack and not p.errs and not banned.findall(src)
EOF

# 3. 文件大小应 < 500KB
ls -lh "/Users/rad/Documents/S1&ETHwuhan/coincall-docs/deck/"
```

## 事实口径

`deck.html` 的事实取自 `coincall-docs/` 01/02/03/04（定价模型 per_call 与 0.01 USDT 示例、机读目录与商店页收入排序、api key 无需注册/可吊销、本地钱包与额度授权、成功才扣款与随时提现、SDK 两个 MCP 工具），全部翻译为用户语言陈述；未发明新数字、未预测官方上线时间。进度页实录数字（5 分 58 秒 / 2.3 秒 / 0.22 USDT / 30+ 笔）以发起人提供的验收存证为准；收尾页三个锚点（30 秒 / 0.01 USDT / 失败不扣款）是产品承诺口径，不随实测值改动。内部工程事实（端口/仓库/参数）只存在于 `deck_v1_internal.html`。
