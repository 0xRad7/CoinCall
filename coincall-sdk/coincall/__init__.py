"""CoinCall 消费者 SDK：本地付费钱包 + EIP-712 签名支付 + MCP 工具。

快速接入见 README《5 分钟接入》；公开面：
    coincall.wallet  — LocalWallet（create/from_key/approve_vault/balance）
    coincall.Client  — catalog()/call()（X-PAYMENT 组装 + 402 转人话 + 本地预算）
    coincall.signing — EIP-712 digest 独立实现（T17 黄金向量锁死）
"""

from coincall.client import CallResult, Client
from coincall.errors import (
    BudgetExceededError,
    CoinCallError,
    GatewayError,
    PaymentRequiredError,
    WalletError,
)
from coincall.wallet import LocalWallet, WalletBalance

__version__ = "0.1.0"

__all__ = [
    "BudgetExceededError",
    "CallResult",
    "Client",
    "CoinCallError",
    "GatewayError",
    "LocalWallet",
    "PaymentRequiredError",
    "WalletBalance",
    "WalletError",
    "__version__",
]
