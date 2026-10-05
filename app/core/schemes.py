"""冻结契约 #3：payment_scheme / ChainAdapter 两个 Protocol + v1 PayVaultScheme + registry。

> 契约冻结（08 §2 / 02 §7.5）：以 Base USDC x402 形态为设计标尺；
> V1 仅挂 "erc3009-vault"（BOT Chain 968），官方 AgentPay/Base x402 迁移时
> 新增 scheme 实现并注册，网关其他模块不得出现 x402 语义。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from app.core.payment import XPayment, recover_signer

if TYPE_CHECKING:
    from app.modules.calls import CallStore


@dataclass
class VerifyResult:
    """scheme.verify 的统一产出：ok=False 时 code 进 402 质询。"""

    ok: bool
    signer: str | None = None
    code: str | None = None
    detail: str = ""
    wallet_balance_raw: int | None = None
    wallet_allowance_raw: int | None = None


class ChainAdapter(Protocol):
    """链上约束只读接口（02 §5b：eth_call 查 token balanceOf/allowance，结果短缓存）。"""

    async def erc20_balance(self, wallet: str, token: str) -> int: ...

    async def erc20_allowance(self, owner: str, spender: str, token: str) -> int: ...


@dataclass
class _NonceMemory:
    """nonce 防重放记录（02 §5a；V1 内存实现，Redis 镜像为 P2）。"""

    seen: set[str] = field(default_factory=set)

    def consume(self, nonce: str) -> bool:
        """首次见到 → 记录并返回 True；重放 → False。"""
        if nonce in self.seen:
            return False
        self.seen.add(nonce)
        return True


class PaymentScheme(Protocol):
    """支付方案抽象（02 §7.5）：build_challenge / verify / enqueue_settle 三件套。"""

    name: str

    def build_challenge(
        self,
        *,
        code: str,
        detail: str,
        service_id: str,
        pricing: dict[str, Any],
        wallet_balance_raw: int,
        pay_to: str,
    ) -> dict[str, Any]: ...

    async def verify(
        self,
        *,
        x_payment: XPayment,
        expected_from: str,
        expected_value_raw: str,
        pay_to: str,
    ) -> VerifyResult: ...

    def enqueue_settle(
        self,
        *,
        call_id: str,
        provider_token_id: int,
        x_payment: XPayment,
    ) -> None: ...


class PayVaultScheme:
    """V1 scheme "erc3009-vault"：本地 ecrecover 验签 + 链上约束 + nonce 防重放。

    enqueue_settle 写 settle_queue（keeper/04 消费，02 只写）。
    """

    name = "erc3009-vault"

    def __init__(  # noqa: PLR0917 —— keyword-only 构造参数
        self,
        chain: ChainAdapter,
        store: CallStore | None = None,
        token_address: str = "0x75edC9335175Fc0552D51D48439F229c10420fe3",  # noqa: S107 —— token 合约地址，非凭据
        verifying_contract: str = "0x000000000000000000000000000000000000dEaD",
        chain_id: int = 968,
        nonce_memory: _NonceMemory | None = None,
    ) -> None:
        self.chain = chain
        self.store = store
        self.token_address = token_address
        self.verifying_contract = verifying_contract
        self.chain_id = chain_id
        self.nonces = nonce_memory or _NonceMemory()

    # -- 402 质询（字段名对齐 x402；02 §3） --

    def build_challenge(
        self,
        *,
        code: str,
        detail: str,
        service_id: str,
        pricing: dict[str, Any],
        wallet_balance_raw: int,
        pay_to: str,
    ) -> dict[str, Any]:
        return {
            "error": "payment_required",
            "detail": detail,
            "code": code,
            "service_id": service_id,
            "pricing": {
                "amount": str(pricing.get("amount", "")),
                "amount_raw": str(pricing.get("amount_raw", "")),
                "token": str(pricing.get("token", "")),
            },
            "wallet_balance_raw": str(wallet_balance_raw),
            "topup": {
                "endpoint": "POST /consumer/topup",
                "token": str(pricing.get("token", "")),
                "deposit_address": pay_to,  # 04 §2 平台收款地址（V1 以 vault 地址占位）
                "min_amount": "1",
            },
            "trace_id": "",
        }

    # -- 本地验签 + 链上约束（02 §5a/5b） --

    async def verify(  # noqa: PLR0911 —— 每个失败分支独立可测（02 §7-1）
        self,
        *,
        x_payment: XPayment,
        expected_from: str,
        expected_value_raw: str,
        pay_to: str,
    ) -> VerifyResult:
        now = int(time.time())
        signer = recover_signer(x_payment, self.verifying_contract, self.chain_id)
        if (
            signer.lower() != expected_from.lower()
            or x_payment.from_.lower() != expected_from.lower()
        ):
            return VerifyResult(
                ok=False,
                signer=signer,
                code="signature_mismatch",
                detail="签名者与 api key 绑定钱包不符",
            )
        if now < x_payment.valid_after:
            return VerifyResult(
                ok=False, signer=signer, code="payment_not_yet_valid", detail="授权尚未生效"
            )
        if now >= x_payment.valid_before:
            return VerifyResult(
                ok=False, signer=signer, code="payment_expired", detail="授权已过期"
            )
        if x_payment.value != expected_value_raw:
            return VerifyResult(
                ok=False,
                signer=signer,
                code="amount_mismatch",
                detail=f"授权金额 {x_payment.value} != 定价 {expected_value_raw}",
            )
        if x_payment.to.lower() != pay_to.lower():
            return VerifyResult(
                ok=False, signer=signer, code="payee_mismatch", detail="授权收款方不是 PayVault"
            )
        if not self.nonces.consume(x_payment.nonce):
            return VerifyResult(
                ok=False, signer=signer, code="nonce_replayed", detail="nonce 已被使用"
            )

        # 链上约束（fail-closed：适配器异常 → chain_unavailable）
        try:
            balance = await self.chain.erc20_balance(expected_from, self.token_address)
            allowance = await self.chain.erc20_allowance(expected_from, pay_to, self.token_address)
        except Exception as exc:
            return VerifyResult(
                ok=False, signer=signer, code="chain_unavailable", detail=f"链上约束检查失败: {exc}"
            )
        price = int(expected_value_raw)
        if balance < price:
            return VerifyResult(
                ok=False,
                signer=signer,
                code="insufficient_balance",
                detail="钱包余额不足",
                wallet_balance_raw=balance,
                wallet_allowance_raw=allowance,
            )
        if allowance < price:
            return VerifyResult(
                ok=False,
                signer=signer,
                code="insufficient_allowance",
                detail="对 PayVault 的授权额度不足",
                wallet_balance_raw=balance,
                wallet_allowance_raw=allowance,
            )
        return VerifyResult(
            ok=True, signer=signer, wallet_balance_raw=balance, wallet_allowance_raw=allowance
        )

    # -- settle 队列（02 只写，keeper/04 消费） --

    def enqueue_settle(
        self,
        *,
        call_id: str,
        provider_token_id: int,
        x_payment: XPayment,
    ) -> None:
        if self.store is None:
            raise RuntimeError("PayVaultScheme 未装配 store，无法写 settle 队列")
        self.store.insert_settle(
            call_id=call_id,
            provider_token_id=provider_token_id,
            auth=x_payment.model_dump(by_alias=True),
        )


SCHEME_REGISTRY: dict[str, PaymentScheme] = {}


def register_scheme(scheme: PaymentScheme) -> None:
    SCHEME_REGISTRY[scheme.name] = scheme


def get_scheme(name: str) -> PaymentScheme:
    """按名取 scheme；未知名抛 KeyError（调用方应视为配置错误）。"""
    return SCHEME_REGISTRY[name]
