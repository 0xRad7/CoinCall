/**
 * 「把身份钱包绑定为当前连接的钱包」内联绑定（注册闭环默认链路）：
 * setAgentWallet(agentId, newWallet=连接地址本人, deadline, signature)——
 * 连接钱包扩展弹窗 signTypedData（AgentWalletSet），deadline 5 分钟窗口倒计时，
 * 拒绝（4001）=友好取消态；成功回调 onBound 让父级刷新绿条。
 * 术语注意：这里绑定的是「身份钱包（agentWallet）」，不是服务收款钱包（manifest.provider.wallet）。
 */
import { useEffect, useRef, useState } from "react";
import { botChainApi, agentWalletSetTypedData } from "../api/gateway";
import { browserProvider, isUserRejected } from "../chain/injected";
import { CHAIN_ID, IDENTITY_REGISTRY } from "../chain/constants";
import { useWallet } from "../state/WalletContext";
import { ErrorBox, WarnBox } from "./ui";

export interface BindIdentityWalletProps {
  agentId: number;
  owner: string | null;
  onBound: (newWallet: string, txHash: string) => void;
}

type Phase = "idle" | "signing" | "submitting" | "done" | "cancelled" | "error";

export function BindIdentityWallet({ agentId, owner, onBound }: BindIdentityWalletProps) {
  const w = useWallet();
  const [phase, setPhase] = useState<Phase>("idle");
  const [deadline, setDeadline] = useState<number | null>(null);
  const [countdown, setCountdown] = useState(0);
  const [txHash, setTxHash] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  // 签名窗口倒计时（<60s 转警示）
  useEffect(() => {
    if (deadline == null) return;
    const t = setInterval(() => {
      if (aliveRef.current) setCountdown(Math.max(0, deadline - Math.floor(Date.now() / 1000)));
    }, 1000);
    return () => clearInterval(t);
  }, [deadline]);

  const expired = deadline != null && countdown <= 0 && (phase === "signing" || phase === "submitting");

  const run = async () => {
    if (!w.address) return;
    setPhase("signing");
    setError(null);
    setTxHash(null);
    try {
      const sel = await w.requireProvider();
      if (!sel) throw new Error("未选择浏览器钱包。");
      const dl = Math.floor(Date.now() / 1000) + 300; // 链上窗口 [now, now+300s]
      setDeadline(dl);
      setCountdown(300);
      const td = agentWalletSetTypedData({
        agentId,
        newWallet: w.address,
        owner: owner ?? "",
        deadline: dl,
        verifyingContract: IDENTITY_REGISTRY,
        chainId: CHAIN_ID,
      });
      // 签名者 = 新钱包本人（当前连接地址），digest 由扩展计算
      const signer = await browserProvider(sel.provider).getSigner(w.address);
      const sig = await signer.signTypedData(td.domain, td.types, td.message);
      if (!aliveRef.current) return;
      setPhase("submitting");
      const r = await botChainApi.bindWallet(agentId, w.address, sig, dl);
      if (!aliveRef.current) return;
      setTxHash(r.tx_hash);
      setPhase("done");
      onBound(w.address, r.tx_hash);
    } catch (e) {
      if (!aliveRef.current) return;
      if (isUserRejected(e)) setPhase("cancelled");
      else {
        setError(e);
        setPhase("error");
      }
    }
  };

  if (phase === "done") {
    return (
      <div className="alert ok" style={{ marginTop: 8 }}>
        ✓ 身份钱包已绑定为当前连接的钱包 <span className="mono">{w.address?.slice(0, 10)}…</span>
        {txHash && (
          <>
            （绑定交易 <a className="mono" href={`https://scan.bohr.life/tx/${txHash}`} target="_blank" rel="noreferrer">
              {txHash.slice(0, 10)}… ↗
            </a>）
          </>
        )}
      </div>
    );
  }

  return (
    <div style={{ marginTop: 8 }}>
      {!w.address ? (
        <WarnBox>
          要把身份钱包绑成你自己的地址，需要浏览器钱包：先到<b>消费端工作台 ①</b>（或本页刷新后的顶栏）连接钱包，再回到这里点绑定。
        </WarnBox>
      ) : (
        <div className="btn-row">
          <button className="btn" onClick={run} disabled={phase === "signing" || phase === "submitting" || expired}>
            {phase === "signing" ? "等待钱包签名确认…" : phase === "submitting" ? "提交上链…" : `把身份钱包绑定为当前连接的钱包（${w.address.slice(0, 8)}…）`}
          </button>
          {deadline != null && (phase === "signing" || phase === "submitting") && (
            <span className={`badge ${expired ? "err" : countdown < 60 ? "warn" : "muted"}`}>
              {expired ? "签名窗口已过，重试重签" : `签名窗口 ${Math.floor(countdown / 60)}:${String(countdown % 60).padStart(2, "0")}`}
            </span>
          )}
        </div>
      )}
      {phase === "cancelled" && <WarnBox>你取消了签名（钱包弹窗里拒绝）。身份钱包保持原状，可重新点绑定。</WarnBox>}
      {phase === "error" && error != null && <ErrorBox error={error} />}
    </div>
  );
}
