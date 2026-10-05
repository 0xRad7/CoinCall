/** 帮助页：三服务与本页面的关系图（纯 CSS）、常见错误码人话表、私钥安全声明。 */
import { InfoBox, WarnBox } from "../components/ui";

const ERRORS: Array<{ code: string; http: string; who: string; human: string; next: string }> = [
  { code: "missing_api_key", http: "402", who: "网关", human: "调用没带 X-Api-Key", next: "到「消费端工作台 → API key」签发并保存，再调用。" },
  { code: "insufficient_balance", http: "402", who: "网关(链上)", human: "钱包 USDT 余额不够本次价格", next: "资金面板点「铸造 10 MockUSDT」（测试网）。" },
  { code: "insufficient_allowance", http: "402", who: "网关(链上)", human: "对 PayVault 的授权额不够", next: "资金面板用授权滑条（如 0.01/0.1/1 USDT）重新 approve。" },
  { code: "payment_missing / 验签失败", http: "402", who: "网关", human: "X-PAYMENT 缺失或签名与 key 绑定钱包不符", next: "确认用「签发 key 的同一钱包」本地签名；本页试用调用自动保证一致。" },
  { code: "identity_not_found", http: "422", who: "core", human: "登记 Provider 时链上没有该 agent_id 身份", next: "核对 ERC-8004 tokenId；身份需先在 bot-chain-api 注册（POST /api/v1/agent-identity/register）。" },
  { code: "agent_wallet 未绑定", http: "422", who: "core", human: "发布服务要求身份已绑定收款钱包", next: "Provider 工作台第 ④ 步完成收款钱包绑定后重发。" },
  { code: "schema 校验失败", http: "422", who: "网关", human: "请求体不符合服务 input_schema（不扣费）", next: "按参数表单的红字提示修正后重试。" },
  { code: "service_not_found / paused", http: "404", who: "网关", human: "服务不存在或已暂停", next: "刷新目录；Provider 在「我的服务」里恢复 active。" },
  { code: "idempotency_conflict", http: "409", who: "网关", human: "同一幂等键换了参数", next: "换参数后重试（本页自动生成新幂等键）。" },
  { code: "provider_failed", http: "502", who: "网关", human: "Provider 端点失败（未扣费，无退款问题）", next: "直接重试；持续失败联系 Provider。" },
];

export default function Help() {
  return (
    <div>
      <h1 className="page-title">帮助</h1>
      <p className="page-sub">这套系统怎么串起来、报错了怎么办、私钥安全边界在哪里。</p>

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
              <p>写 core（登记/manifest）· 写 8010（钱包绑定）· 读 PayVault credits · providerWithdraw</p>
            </div>
            <div className="arrow">+</div>
            <div className="node" style={{ borderColor: "var(--success)" }}>
              <h4>本控制台 · 消费端工作台</h4>
              <p>本地钱包 EIP-712 签名 → X-PAYMENT → 网关 /call；mint/approve 测试资金</p>
            </div>
            <div className="arrow">→</div>
            <div className="node">
              <h4>keeper（网关内置）</h4>
              <p>settle 队列 → PayVault.chargeWithSigBatch 批量上链 → 总览页 GMV/proof</p>
            </div>
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
            <h4>点击「连接钱包」</h4>
            <p>只调 eth_requestAccounts 读地址<br />（不请求任何私钥/权限）</p>
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
            <b>Provider 提现</b>：providerWithdraw 只能由收款钱包本人发起，路径恒开、无平台托管（铁律 P8）。
          </li>
        </ul>
        <WarnBox>
          本控制台面向测试网（BOT Chain 968）演示：MockUSDT 公开 mint、无真实价值。请勿在演示钱包以外的场合使用主网私钥习惯（本页也根本没有输入私钥的地方）。
        </WarnBox>
      </div>
    </div>
  );
}
