/** 帮助页：三服务与本页面的关系图（纯 CSS）、常见错误码人话表、私钥安全声明。 */
import { InfoBox, WarnBox } from "../components/ui";
import { PageHeader } from "../components/shell";

const ERRORS: Array<{ code: string; http: string; who: string; human: string; next: string }> = [
  { code: "missing_api_key", http: "402", who: "网关", human: "调用没带 X-Api-Key", next: "到「消费端工作台 → API key」签发并保存，再调用。" },
  { code: "insufficient_balance", http: "402", who: "网关(链上)", human: "钱包 USDT 余额不够本次价格", next: "资金面板「获取 USDT」——从测试网水龙头领取后重试。" },
  { code: "insufficient_allowance", http: "402", who: "网关(链上)", human: "对 PayVault 的授权额不够", next: "试用调用里的「① 授权额度」会内联展开并预置金额（默认=单价×10，一次授权可供多次调用），完成钱包授权后回来支付。" },
  { code: "payment_missing / 验签失败", http: "402", who: "网关", human: "X-PAYMENT 缺失或签名与 key 绑定钱包不符", next: "确认用「签发 key 的同一钱包」本地签名；本页试用调用自动保证一致。" },
  { code: "identity_not_found", http: "422", who: "core", human: "登记 Provider 时链上没有该 agent_id 身份", next: "核对 ERC-8004 tokenId；身份需先在 bot-chain-api 注册（POST /api/v1/agent-identity/register）。" },
  { code: "agent_wallet 未绑定", http: "422", who: "core", human: "身份钱包还是平台代管账户（未绑定为你自己的地址）", next: "Provider 工作台第 ① 步注册后点「把身份钱包绑定为当前连接的钱包」，或第 ④ 步完成绑定。" },
  { code: "schema 校验失败", http: "422", who: "网关", human: "请求体不符合服务 input_schema（不扣费）", next: "按参数表单的红字提示修正后重试。" },
  { code: "service_not_found / paused", http: "404", who: "网关", human: "服务不存在或已暂停", next: "刷新目录；Provider 在「我的服务」里恢复 active。" },
  { code: "idempotency_conflict", http: "409", who: "网关", human: "同一幂等键换了参数", next: "换参数后重试（本页自动生成新幂等键）。" },
  { code: "provider_failed", http: "502", who: "网关", human: "Provider 端点失败（未扣费，无退款问题）", next: "直接重试；持续失败联系 Provider。" },
];

export default function Help() {
  return (
    <div>
      <PageHeader
        title="帮助"
        sub="这套系统怎么串起来、报错了怎么办、私钥安全边界在哪里。"
        actions={
          <a className="btn small secondary" href="https://scan.bohr.life" target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
            区块浏览器 ↗
          </a>
        }
      />

      <div className="card">
        <h3>三个服务与控制台的关系</h3>
        <div className="diagram">
          <div className="node">
            <h4>coincall-core</h4>
            <p>注册面 · 8020<br />Provider 登记 / manifest 目录 / API key 签发 / 收入榜</p>
          </div>
          <div className="arrow">⇄</div>
          <div className="node">
            <h4>coincall-gateway</h4>
            <p>数据面 · 8030<br />POST /call：验签 → 影子闸门 → 代理转发 → 收据 → settle 队列</p>
          </div>
          <div className="arrow">⇄</div>
          <div className="node">
            <h4>coincall-bot-chain-api</h4>
            <p>链 API · 8010<br />ERC-8004 身份 / PayVault 事件读取 / 钱包绑定上链</p>
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          <div className="diagram">
            <div className="node" style={{ borderColor: "var(--success)" }}>
              <h4>本控制台 · Provider 工作台</h4>
              <p>写 core（登记/manifest）· 写 8010（身份注册/钱包绑定）· 读 PayVault credits · providerWithdraw</p>
            </div>
            <div className="arrow">+</div>
            <div className="node" style={{ borderColor: "var(--success)" }}>
              <h4>本控制台 · 消费端工作台</h4>
              <p>资金状态面板（三数+水龙头）；试用调用内「① 授权额度 → ② 支付调用」一条动线</p>
            </div>
            <div className="arrow">→</div>
            <div className="node">
              <h4>keeper（网关内置）</h4>
              <p>settle 队列 → PayVault.chargeWithSigBatch 批量上链 → 总览页 GMV/proof</p>
            </div>
          </div>
          <div style={{ marginTop: 16 }}>
            <div className="dim" style={{ marginBottom: 6 }}>Provider 接入向导（认证先行，四步；第 0 步注册内置于第 ① 步）：</div>
            <div className="diagram">
              <div className="node" style={{ borderColor: "var(--text-3)" }}>
                <h4>⓪ 注册身份（可选）</h4>
                <p>没有 ERC-8004 身份？第 ① 步展开「注册一个」<br />平台代发铸造 → 自动绑定为你的钱包</p>
              </div>
              <div className="arrow">→</div>
              <div className="node" style={{ borderColor: "var(--success)" }}><h4>① 认领身份</h4><p>连接钱包 → 预检认领状态<br />绑定（AgentWalletSet 签名）+登记<b>一步完成</b><br />四态引导：不存在/可认领/需先绑定/已被他人认领</p></div>
              <div className="arrow">→</div>
              <div className="node"><h4>② 发布服务</h4><p>manifest 表单<br />定价/端点/schema/上游认证头<br />服务收款钱包<b>默认=认领钱包</b></p></div>
              <div className="arrow">→</div>
              <div className="node"><h4>③ 管理</h4><p>暂停/恢复/改价<br />= 重发 manifest<br />上游凭证管理</p></div>
              <div className="arrow">→</div>
              <div className="node"><h4>④ 提现</h4><p>PayVault credits<br />providerWithdraw<br />（服务收款钱包本人）</p></div>
            </div>
          </div>
          <div style={{ marginTop: 16 }}>
            <div className="dim" style={{ marginBottom: 6, marginTop: 8 }}>产品层级：钱包 → Teams → 服务（创建团队 = 一次钱包签名，链上身份由平台自动管理）：</div>
            <div className="diagram" style={{ marginBottom: 8 }}>
              <div className="node" style={{ borderColor: "var(--success)" }}><h4>你的钱包（1）</h4><p>连接一次即可<br />签团队身份/付费授权/提现</p></div>
              <div className="arrow">→</div>
              <div className="node"><h4>Teams（N）</h4><p>一个钱包可建多个团队<br />每个团队=一个链上身份<br />创建只需一次签名</p></div>
              <div className="arrow">→</div>
              <div className="node"><h4>服务（N）</h4><p>每个团队发布多个服务<br />收入进团队认领钱包<br />链上身份自动管理</p></div>
            </div>
            <div className="dim" style={{ marginBottom: 6 }}>三个钱包角色（别混成一个词）：</div>
            <div className="diagram">
              <div className="node" style={{ borderColor: "var(--text-3)" }}>
                <h4>平台代管账户（0xc37f…）</h4>
                <p>代发注册/结算交易 · PayVault operator<br /><b>不是收款地址</b>，不存放你的收入</p>
              </div>
              <div className="arrow">≠</div>
              <div className="node">
                <h4>身份钱包（agentWallet）</h4>
                <p>ERC-8004 身份上的对外钱包<br />注册时默认 = 平台代管，<b>建议绑成你自己的</b><br />（第 ①/④ 步，EIP-712 签名）</p>
              </div>
              <div className="arrow">≠</div>
              <div className="node" style={{ borderColor: "var(--success)" }}>
                <h4>服务收款钱包（manifest.provider.wallet）</h4>
                <p><b>收入实际到账地址</b>（Charged 记账键）<br />发布服务时指定，默认=你连接的钱包<br />提现（providerWithdraw）用它本人发起</p>
              </div>
            </div>
            <InfoBox>
              一句话资金流：付费调用的 USDT 由 keeper 划入 PayVault，按<b>服务收款钱包</b>记账；Provider 用该地址本人发起 providerWithdraw 提现。身份钱包只是链上身份的对外属性，平台代管账户只是交易代发方。
            </InfoBox>
          </div>
          <div className="card" style={{ marginTop: 16, marginBottom: 0 }}>
            <h3>上游凭证去哪了（http_json 服务）</h3>
            <div className="diagram">
              <div className="node">
                <h4>你填的键值</h4>
                <p>发布/管理页填 X-API-KEY 等<br />值只进本页内存与加密请求</p>
              </div>
              <div className="arrow">→</div>
              <div className="node">
                <h4>core 加密落盘</h4>
                <p>Fernet 加密存储；GET 只回头名<br />公开 manifest / 目录零泄露</p>
              </div>
              <div className="arrow">→</div>
              <div className="node">
                <h4>网关转发时注入</h4>
                <p>调你的上游 URL 时自动带上<br />（60s 缓存；消费者请求头不透传）</p>
              </div>
            </div>
            <InfoBox>
              信任模型一句话：上游凭证走的是 <b>API 网关标准模型</b>（平台可解密、代为注入），与消费者的付费钱包私钥分属两个信任域——后者自始至终只在消费者浏览器钱包里。不想交给平台？自包一层薄适配服务再上架即可。
            </InfoBox>
            <InfoBox>
              <b>公开面零上游地址</b>：公开目录与 manifest 查询里的 endpoint.url 恒为 null（type/method 保留）——消费者只见「CoinCall 调用端点」POST 网关/call/&#123;service_id&#125;，无法绕过付费直连你的上游。完整 url 只在本机管理面内部通道（Provider 自己的「我的服务」）与网关内部取数时可见。
            </InfoBox>
            <InfoBox>
              <b>调用端点与 SDK 接入</b>：目录卡的「CoinCall 调用端点」随你访问本控制台的地址动态生成（同源代理 <span className="mono">/api/gw</span>）——
              消费端 SDK 的 gateway_url 就填该端点的基址（局域网演示 = 访问 origin + /api/gw；对外部署可用环境变量 VITE_GATEWAY_PUBLIC_URL 指到真实网关域名）。
              本机部署也可直连网关 8030，跨机一律走同源代理。
            </InfoBox>
          </div>
          <div className="card" style={{ marginTop: 16, marginBottom: 0 }}>
            <h3>决策层怎么算的</h3>
            <p className="card-desc">总览页「同类比价」的四信号排名——帮你回答「这钱花给谁」：</p>
            <ul style={{ paddingLeft: 18, lineHeight: 1.9 }}>
              <li><b>score = 0.4×收入 + 0.25×履约 + 0.2×反馈 + 0.15×新鲜度</b>（窗口默认 7 天/168h）。</li>
              <li><b>收入</b>（0.4）= 0.5×norm(ln(总额+1)) + 0.5×norm(去重支付者数)——链上 Charged 事件，不是自报。</li>
              <li><b>履约</b>（0.25）= 成功率 × 延迟分（p95≤2s 满分，≥10s 零分，其间线性）——来自网关调用流水。</li>
              <li><b>反馈</b>（0.2）= norm(贝叶斯均值)，先验=全局均值 m=10（防小样本刷分）；<b>收据=付费才有发言权</b>（Ed25519 验签）。</li>
              <li><b>新鲜度</b>（0.15）= exp(-ln2×Δh/48)——<b>48 小时半衰期</b>：不活跃自动掉分，Providers 必须持续履约。</li>
              <li><b>时间拨针</b>（as_of）：决策页的日期时间控件——传 ISO 时间给服务端重算，直观演示 freshness 衰减如何重排（6 天后全部衰减到 ~12.5%）。</li>
            </ul>
          </div>
          <div className="card" style={{ marginTop: 16, marginBottom: 0 }}>
            <h3>探测与 Schema 识别</h3>
            <p className="card-desc">发布 http_json 服务前的「先试试上游通不通」：</p>
            <ul style={{ paddingLeft: 18, lineHeight: 1.9 }}>
              <li><b>为什么要平台代发</b>：浏览器直连你的上游会被 CORS 拦截，所以探测与转发都由服务端发出（护栏：仅 http/https、私网/回环地址拒绝、20 秒硬顶、不跟随重定向）。</li>
              <li><b>method=GET 的语义</b>：消费者仍然 POST JSON 给网关；网关把参数映射成上游 query——标量直传、数组同 key 重复（?tag=a&amp;tag=b）。嵌套对象参数在<b>发布时</b>被 422 拒绝（前端也会即时预警）。</li>
              <li><b>探测头不落盘</b>：探测弹层里临时填的认证头只随那一次请求发出；要长期保存请用发布表单的「上游认证头」。</li>
              <li><b>用结果生成 Schema</b>：从探测响应体递归推断 output_schema（数组元素类型合并、null 并入类型并集、嵌套限 4 层）；input_schema 从示例参数/示例 JSON 生成。生成结果标注「自动识别，请核对」，可继续手改。</li>
            </ul>
          </div>
        </div>
        <InfoBox>
          一次付费调用的资金流：你的钱包 —（EIP-712 授权签名）→ 网关（只验签不过手资金）—（keeper 批量结算）→
          PayVault 划扣 USDT 并给 Provider 记 credits → Provider 随时 providerWithdraw 提现。资金永不过平台的手（铁律 P7）。
        </InfoBox>
      </div>

      <div className="card">
        <h3>常见错误码人话表</h3>
        <table className="list">
          <thead>
            <tr>
              <th>错误码</th>
              <th>HTTP</th>
              <th>来自</th>
              <th>人话</th>
              <th>下一步</th>
            </tr>
          </thead>
          <tbody>
            {ERRORS.map((e) => (
              <tr key={e.code}>
                <td className="err-code">{e.code}</td>
                <td className="num">{e.http}</td>
                <td>{e.who}</td>
                <td>{e.human}</td>
                <td>{e.next}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>钱包连接流（新）</h3>
        <div className="diagram">
          <div className="node">
            <h4>点「连接钱包」</h4>
            <p>入口：顶栏（未连接时常驻）或任一工作台内联按钮<br />只调 eth_requestAccounts 读地址（不请求任何私钥/权限）</p>
          </div>
          <div className="arrow">→</div>
          <div className="node">
            <h4>扩展弹窗确认</h4>
            <p>OKX / MetaMask 弹窗里你自己确认<br />链 ≠ 968 时引导切链/添加网络</p>
          </div>
          <div className="arrow">→</div>
          <div className="node">
            <h4>顶栏常驻地址 chip</h4>
            <p>sessionStorage 记地址（仅地址）<br />链徽标 ✗ 可点重试切链 · 断开即清</p>
          </div>
          <div className="arrow">→</div>
          <div className="node" style={{ borderColor: "var(--success)" }}>
            <h4>每次动作都弹窗</h4>
            <p>签名（EIP-712）/交易（mint·授权·提现）<br />全部在扩展内完成，拒绝即取消（4001 友好态）</p>
          </div>
        </div>
      </div>

      <div className="card">
        <h3>私钥与密钥安全声明</h3>
        <p className="card-desc">
          <b>控制台不接触任何私钥</b>——地址只读获取，签名与交易全部由你的浏览器钱包（OKX / MetaMask）在本地完成。
        </p>
        <ul style={{ paddingLeft: 18, lineHeight: 2 }}>
          <li>
            <b>地址只读</b>：连接仅调用 eth_requestAccounts / eth_accounts 获取地址（存 sessionStorage，断开即清）；控制台代码里没有任何私钥输入框，
            网络请求中也不存在私钥字段。
          </li>
          <li>
            <b>签名在扩展内</b>：付费授权（EIP-712 typed data）与交易（mint / 授权 / 提现）都由钱包扩展计算 digest 并弹出确认；你在弹窗里能看到
            金额与合约地址，拒绝（4001）即取消，不产生任何上链效果。
          </li>
          <li>
            <b>付费授权签名</b>：每笔限定金额 = 服务定价、时间窗 10 分钟、nonce 一次性；即便签名泄露，损失上限为单笔价格。keeper 划款另需你对
            PayVault 的 approve 额度——想收紧就调低滑条重新授权。
          </li>
          <li>
            <b>API key</b>：服务端只存 hash，明文仅签发时回显一次；「我已保存」后本机 localStorage 保留它供试用调用自动携带，可清浏览器数据移除。
          </li>
          <li>
            <b>一次性演示钱包（兜底）</b>：给没装扩展的演示机用——随机生成、仅测试网、关页即焚（sessionStorage）、勿存资金；它在本页进程内本地签名，
            与「连接浏览器钱包」主路径完全隔离。
          </li>
          <li>
            <b>Provider 提现</b>：providerWithdraw 只能由服务收款钱包本人发起，路径恒开、无平台托管（铁律 P8）。
          </li>
        </ul>
        <WarnBox>
          本控制台面向测试网（BOT Chain 968）：计价 token 是测试网真 USDT（水龙头领取，无公开 mint）。请勿在演示钱包以外的场合使用主网私钥习惯（本页也根本没有输入私钥的地方）。
        </WarnBox>
      </div>
    </div>
  );
}
