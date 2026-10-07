"""CoinCall SDK 异常族：一张表看清消费者会遇到什么。"""


class CoinCallError(Exception):
    """SDK 异常基类。"""


class WalletError(CoinCallError):
    """钱包生成/导入/资金准备失败（私钥来源非法、0600 校验不过、链断言失败等）。"""


class BudgetExceededError(CoinCallError):
    """本地预算计数器超限（03 §6：平台不替 consumer 管预算，SDK 侧执行）。"""

    def __init__(self, spent_raw: int, budget_raw: int, price_raw: int) -> None:
        self.spent_raw = spent_raw
        self.budget_raw = budget_raw
        self.price_raw = price_raw
        super().__init__(
            f"本地预算超限：已花 {spent_raw} + 本笔 {price_raw} > 上限 {budget_raw}"
            "（如需继续，调大 budget_raw / COINCALL_BUDGET_RAW）"
        )


class GatewayError(CoinCallError):
    """网关非 402 错误（401/404/409/422/5xx…）。"""

    def __init__(self, status_code: int, error: str, detail: str, code: str) -> None:
        self.status_code = status_code
        self.error = error
        self.detail = detail
        self.code = code
        super().__init__(f"[{status_code} {error}/{code}] {detail}")


class PaymentRequiredError(CoinCallError):
    """402 付费质询：质询体的 payment 块已转成人话指引（03 §5：含 approve 指引）。

    attributes:
        code: 质询码（insufficient_balance / insufficient_allowance / …）
        detail: 网关原文
        service_id / pricing / wallet_balance_raw: 质询上下文
        approve_to: 应 approve 的合约地址（质询 payment.approve_to）
        amount_raw / deficit_raw: 本笔定价与余额缺口（最小单位；数值可解析时）
        guidance: 人话指引（下一步做什么）
    """

    def __init__(self, challenge: dict[str, object]) -> None:
        payment = challenge.get("payment") or {}
        if not isinstance(payment, dict):  # 质询形态异常时兜底为空块
            payment = {}
        pricing = challenge.get("pricing") or {}
        if not isinstance(pricing, dict):
            pricing = {}
        self.challenge = challenge
        self.code = str(challenge.get("code", "payment_required"))
        self.detail = str(challenge.get("detail", ""))
        self.service_id = str(challenge.get("service_id", ""))
        self.pricing = pricing
        self.wallet_balance_raw = str(challenge.get("wallet_balance_raw", ""))
        self.approve_to = str(payment.get("approve_to", ""))
        self.amount_raw = str(pricing.get("amount_raw", ""))
        self.deficit_raw: str | None = self._deficit()
        self.guidance = self._guidance()
        super().__init__(f"402 {self.code}: {self.detail}\n下一步: {self.guidance}")

    def _deficit(self) -> str | None:
        try:
            deficit = int(self.amount_raw) - int(self.wallet_balance_raw)
        except (TypeError, ValueError):
            return None
        return str(deficit) if deficit > 0 else None

    def _guidance(self) -> str:
        """质询码 → 人话（A6：不透传裸 JSON，给可执行动作）。"""
        token = str(self.pricing.get("token", "USDT"))
        amount = str(self.pricing.get("amount", self.amount_raw))
        if self.code == "insufficient_balance":
            gap = f"，差 {self.deficit_raw}（最小单位）" if self.deficit_raw else ""
            return (
                f"钱包 {token} 余额不足：本笔定价 {amount} {token}"
                f"（amount_raw={self.amount_raw}），链上余额 raw={self.wallet_balance_raw}{gap}。"
                f"请向付费钱包转入测试网 {token}（无公开 mint，从持有资金的钱包转入）后重试。"
            )
        if self.code == "insufficient_allowance":
            return (
                f"对 PayVault 的授权额度不足（本笔 amount_raw={self.amount_raw}）。"
                f"请执行 wallet.approve_vault('{amount}') 向合约 {self.approve_to} 授权后重试。"
            )
        if self.code in ("missing_api_key", "apikey_unknown"):
            return "缺少/无效 X-Api-Key：先到 core（8020）POST /apikeys 签发并绑定消费者钱包。"
        if self.code == "quota_exceeded":
            return "超出该 api key 单笔授权上限：请到 core 重新签发更大 quota 的 key。"
        if self.code == "bad_debt":
            return "存在未结坏账被拦截：联系平台运营核对 settle 流水。"
        return f"付费质询（{self.code}）：{self.detail}。可先 catalog() 核对定价与钱包余额。"
