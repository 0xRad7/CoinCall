/** 全站共享「连接钱包」按钮（同一 w.connect() 代码路径：EIP-6963 发现 + 选择器 + legacy 回退）。 */
import { useWallet } from "../state/WalletContext";
import { Spinner } from "./ui";

export function ConnectWalletButton({
  size = "small",
  label = "连接钱包",
  title,
}: {
  size?: "small" | "normal";
  label?: string;
  title?: string;
}) {
  const w = useWallet();
  return (
    <button
      className={size === "small" ? "btn small" : "btn"}
      onClick={() => void w.connect()}
      disabled={w.connecting}
      title={title ?? "只读取钱包地址（eth_requestAccounts）；签名与交易都在钱包扩展弹窗里确认"}
    >
      {w.connecting ? <Spinner label="等待钱包确认…" /> : label}
    </button>
  );
}
