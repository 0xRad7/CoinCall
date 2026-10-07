"""本地付费钱包（03 §3 v2）：生成/导入、本地直签资金操作、签名支付授权。

铁律 A1：私钥只进本地存储（env / 0600 文件）与内存。网络层只见：
  - 已签名的 raw tx（approve/mint）
  - EIP-712 支付授权的签名产物（r/s/v）
任何 HTTP 头 / JSON body / 链上 calldata 都不携带私钥。
"""

import os
import re
import stat
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from eth_account import Account

from coincall.chain import (
    APPROVE_GAS,
    MINT_GAS,
    TOKEN_ADDRESS,
    TOKEN_DECIMALS,
    ChainConnection,
    ChainGateway,
    erc20_approve_data,
    erc20_mint_data,
)
from coincall.errors import WalletError
from coincall.signing import (
    CHAIN_ID,
    PAY_VAULT_ADDRESS,
    Authorization,
    PaymentSignature,
    sign_authorization,
)

if TYPE_CHECKING:
    from eth_account.signers.local import LocalAccount

ENV_WALLET_KEY = "COINCALL_WALLET_KEY"
_PRIVATE_KEY_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
REQUIRED_KEY_FILE_MODE = 0o600  # 私钥文件唯一合法权限


@dataclass(frozen=True)
class WalletBalance:
    """钱包资金视图（03 §3：可用额度 = min(余额, 授权额)；在途扣减在网关影子闸门侧）。"""

    wallet: str
    usdt_balance_raw: int
    vault_allowance_raw: int
    available_raw: int


def _to_raw(amount: str | int | Decimal, decimals: int = TOKEN_DECIMALS) -> int:
    """人类单位 → 最小单位（精确 Decimal 换算，拒绝超精度与负数）。"""
    try:
        value = Decimal(str(amount)) * (Decimal(10) ** decimals)
    except (InvalidOperation, ValueError) as exc:
        raise WalletError(f"金额不合法: {amount!r}") from exc
    if value < 0:
        raise WalletError(f"金额不能为负数: {amount!r}")
    if value != value.to_integral_value():
        raise WalletError(f"金额精度超过 {decimals} 位小数: {amount!r}")
    return int(value)


def _require_key_file_mode(path: str) -> None:
    """私钥文件权限必须恰好 0600（组/全局可读 = 拒绝装载）。"""
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode != REQUIRED_KEY_FILE_MODE:
        raise WalletError(f"私钥文件权限必须为 0600（当前 {oct(mode)}）: {path}")


def resolve_private_key(source: str | None = None, *, env_var: str = ENV_WALLET_KEY) -> str:
    """私钥来源解析：None→env；0x hex 直钥；本地 0600 文件；env 值亦可为文件路径。

    解析只发生在消费者本机；返回值仅在进程内使用，绝不进入任何网络载荷。
    """
    if source is None:
        source = os.environ.get(env_var, "")
        if not source:
            raise WalletError(
                f"未提供钱包私钥：设置环境变量 {env_var}（0x 私钥或 0600 key 文件路径），"
                "或用 LocalWallet.create() 在本机生成新钱包"
            )
    source = source.strip()
    if _PRIVATE_KEY_RE.match(source):
        return source.lower()
    if os.path.isfile(source):
        _require_key_file_mode(source)
        content = open(source, encoding="utf-8").read().strip()  # noqa: SIM115 —— 小文件即读即关
        if not _PRIVATE_KEY_RE.match(content):
            raise WalletError(f"私钥文件内容不是 0x+64hex 私钥: {source}")
        return content.lower()
    env_value = os.environ.get(source, "")
    if env_value and ("/" in source or source.isupper()):
        # 形如环境变量名（含路径分隔符或全大写）且已设置：其值为私钥或文件路径
        return resolve_private_key(env_value, env_var=env_var)
    raise WalletError(f"私钥来源不合法（既非 0x 私钥、亦非存在的文件或环境变量名）: {source!r}")


class LocalWallet:
    """消费者本地付费钱包：持钥、本地直签、资金视图；repr/str 永不泄露私钥。"""

    def __init__(
        self,
        account: "LocalAccount",
        *,
        chain: ChainGateway | None = None,
        rpc_url: str | None = None,
        token_address: str = TOKEN_ADDRESS,
        token_decimals: int = TOKEN_DECIMALS,
        pay_vault: str = PAY_VAULT_ADDRESS,
        chain_id: int = CHAIN_ID,
    ) -> None:
        self._account = account
        self.address = account.address
        self._chain = chain
        self._rpc_url = rpc_url
        self.token_address = token_address
        self.token_decimals = token_decimals
        self.pay_vault = pay_vault
        self.chain_id = chain_id

    # -- 生成与导入 --

    @classmethod
    def create(
        cls,
        *,
        chain: ChainGateway | None = None,
        rpc_url: str | None = None,
        token_address: str = TOKEN_ADDRESS,
        token_decimals: int = TOKEN_DECIMALS,
        pay_vault: str = PAY_VAULT_ADDRESS,
        chain_id: int = CHAIN_ID,
    ) -> "LocalWallet":
        """本地生成专用付费钱包（03 §3 ①：Account.create，私钥不落任何平台）。"""
        return cls(
            Account.create(),
            chain=chain,
            rpc_url=rpc_url,
            token_address=token_address,
            token_decimals=token_decimals,
            pay_vault=pay_vault,
            chain_id=chain_id,
        )

    @classmethod
    def from_key(
        cls,
        source: str | None = None,
        *,
        chain: ChainGateway | None = None,
        rpc_url: str | None = None,
        token_address: str = TOKEN_ADDRESS,
        token_decimals: int = TOKEN_DECIMALS,
        pay_vault: str = PAY_VAULT_ADDRESS,
        chain_id: int = CHAIN_ID,
    ) -> "LocalWallet":
        """导入已有钱包：0x 私钥 / env 变量 / 0600 本地 key 文件。"""
        key = resolve_private_key(source)
        return cls(
            Account.from_key(key),
            chain=chain,
            rpc_url=rpc_url,
            token_address=token_address,
            token_decimals=token_decimals,
            pay_vault=pay_vault,
            chain_id=chain_id,
        )

    def __repr__(self) -> str:
        return f"<LocalWallet {self.address} chain={self.chain_id}>"

    # -- 链网关（惰性直连 rpc.bohr.life） --

    @property
    def chain(self) -> ChainGateway:
        if self._chain is None:
            self._chain = ChainConnection(self._rpc_url) if self._rpc_url else ChainConnection()
        return self._chain

    # -- 资金准备（本地直签 raw tx） --

    def _send_signed(self, to: str, data: bytes, gas: int) -> dict[str, object]:
        """构建 → 断言链 ID → 本地签名 → 只发 raw tx → 等回执（A1/A4）。"""
        tx = self.chain.build_tx(self.address, to, data, gas)
        if tx.get("chainId") != self.chain_id:
            raise WalletError(
                f"交易链 ID {tx.get('chainId')} != {self.chain_id}，拒绝签名（防错链）"
            )
        signed = self._account.sign_transaction(tx)
        tx_hash = self.chain.send_raw(bytes(signed.raw_transaction))
        receipt = self.chain.wait_receipt(tx_hash)
        if receipt.get("status") != 1:
            raise WalletError(f"交易回执失败: {receipt}")
        return receipt

    def approve_vault(self, amount: str | int | Decimal) -> dict[str, object]:
        """向 PayVault 授权额度（03 §3 ③：本地直签 approve raw tx）。"""
        amount_raw = _to_raw(amount, self.token_decimals)
        receipt = self._send_signed(
            self.token_address,
            erc20_approve_data(self.pay_vault, amount_raw),
            APPROVE_GAS,
        )
        return {
            "tx_hash": receipt["transactionHash"],
            "status": receipt["status"],
            "spender": self.pay_vault,
            "token": self.token_address,
            "amount_raw": amount_raw,
            "gas_used": receipt["gasUsed"],
        }

    def mint(self, amount: str | int | Decimal) -> dict[str, object]:
        """测试网 USDT mint（仅限 MINTER_ROLE 账户；普通钱包会 revert，充值走转入）。"""
        amount_raw = _to_raw(amount, self.token_decimals)
        receipt = self._send_signed(
            self.token_address, erc20_mint_data(self.address, amount_raw), MINT_GAS
        )
        return {
            "tx_hash": receipt["transactionHash"],
            "status": receipt["status"],
            "amount_raw": amount_raw,
            "gas_used": receipt["gasUsed"],
        }

    def balance(self) -> WalletBalance:
        """钱包 USDT 余额 / 对 PayVault 授权额 / 可用额（03 §3）。"""
        token_balance = self.chain.erc20_balance(self.address, self.token_address)
        allowance = self.chain.erc20_allowance(self.address, self.pay_vault, self.token_address)
        return WalletBalance(
            wallet=self.address,
            usdt_balance_raw=token_balance,
            vault_allowance_raw=allowance,
            available_raw=min(token_balance, allowance),
        )

    # -- 每笔调用的签名职责（03 §3：消费者进程内 EIP-712 签名） --

    def sign_payment(
        self,
        auth: Authorization,
        *,
        verifying_contract: str | None = None,
        chain_id: int | None = None,
    ) -> PaymentSignature:
        """对 Authorization 六元组做本地 EIP-712 签名（T17 锁死的独立 digest）。"""
        return sign_authorization(
            auth,
            private_key=self._account.key,
            verifying_contract=verifying_contract or self.pay_vault,
            chain_id=chain_id or self.chain_id,
        )
