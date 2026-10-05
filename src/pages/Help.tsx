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
        <h3>私钥与密钥安全声明</h3>
        <ul style={{ paddingLeft: 18, lineHeight: 2 }}>
          <li>
            <b>本控制台只面向测试网（BOT Chain 968）</b>：MockUSDT 无真实价值，公开 mint 仅为演示。请勿导入任何持有真实资产的私钥。
          </li>
          <li>
            <b>消费端钱包私钥</b>：仅存浏览器 sessionStorage，关闭标签页即清除；不上传、不写 localStorage、不出现在任何网络请求体中。签名（EIP-712 / 交易）
            全部在本页面进程内完成，唯一外发对象是标准交易/签名产物。
          </li>
          <li>
            <b>API key</b>：服务端只存 hash，明文仅签发时回显一次。你在「我已保存」确认后，本机 localStorage 只保留它以便试用调用自动携带——
            可随时通过清浏览器数据移除。
          </li>
          <li>
            <b>付费授权签名</b>：每笔授权限定金额 = 服务定价、时间窗 10 分钟、nonce 一次性；即便签名泄露，损失上限为单笔价格。keeper
            划款依赖你对 PayVault 的 approve 额度——想随时收紧就把滑条调低重授权。
          </li>
          <li>
            <b>Provider 提现</b>：providerWithdraw 只能由收款钱包本人发起，路径恒开、无平台托管（铁律 P8）。
          </li>
        </ul>
        <WarnBox>演示建议：使用专项测试账户（如 anvil 公开测试账户）。演示结束点「销毁会话密钥」清理本机敏感态。</WarnBox>
      </div>
    </div>
  );
}
