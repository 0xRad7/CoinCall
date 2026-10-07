"""消费客户端：catalog + 付费调用（03 §5）。

call(service_id, params) 内部：
  ① 目录查价（ETag 缓存）→ ② 本地预算闸（超限拒调，不发请求）→
  ③ 组装 Authorization 六元组（nonce 随机 bytes32 / valid_before=now+600）→
  ④ 本地 EIP-712 签名（T17 锁死的独立 digest）→ X-PAYMENT 头 →
  ⑤ POST /call/{service_id} → 402 转 PaymentRequiredError（人话指引）→
  ⑥ 成功记预算、透传收据头。

httpx 一律 trust_env=False（C-07：系统代理不得劫持 localhost 服务）。
"""

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from coincall.errors import (
    BudgetExceededError,  # noqa: F401 —— 向后兼容 re-export（v1 budget_raw 用户 except 用）
    CoinCallError,
    GatewayError,
    PaymentRequiredError,
    WalletError,
)
from coincall.policy import PolicyConfig, PolicyEngine
from coincall.signing import Authorization, build_payment_header
from coincall.wallet import LocalWallet

DEFAULT_GATEWAY_URL = "http://127.0.0.1:8030"
DEFAULT_CORE_URL = "http://127.0.0.1:8020"
AUTH_WINDOW_S = 600  # 授权窗口（02 §5a：valid_before = now + 600s）
HTTP_TIMEOUT_S = 60  # 与 manifest timeout_ms 硬顶（60s，core 校验）对齐——AI 类上游冷启动可超 30s

RECEIPT_HEADERS = ("X-Receipt-Id", "X-Charged-Raw", "X-Receipt-Sig")
HTTP_NOT_MODIFIED = 304
HTTP_BAD_REQUEST = 400
HTTP_PAYMENT_REQUIRED = 402


@dataclass(frozen=True)
class CallResult:
    """付费调用结果：服务返回体 + 收据头透传（02 §4）。"""

    service_id: str
    status_code: int
    body: Any
    receipt_id: str | None = None
    charged_raw: str | None = None
    receipt_sig: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


def _default_idempotency_key(service_id: str, params: Any) -> str:  # noqa: ANN401 —— JSON 序列化边界
    """自动幂等键 = 参数 hash（03 §5 ③：同参数重试同键，网关重放防双扣）。"""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{service_id}:{canonical}".encode()).hexdigest()


class Client:
    """CoinCall 消费客户端（同步阻塞式，Agent 进程内使用简单优先）。"""

    def __init__(
        self,
        api_key: str,
        wallet: LocalWallet | None = None,
        gateway_url: str = DEFAULT_GATEWAY_URL,
        core_url: str = DEFAULT_CORE_URL,
        budget_raw: int | None = None,
        *,
        policy: PolicyConfig | PolicyEngine | None = None,
        policy_state_dir: str | None = None,  # 隔离用：预算状态/账本目录（缺省 ~/.coincall）
        http: httpx.Client | None = None,
        auth_window_s: int = AUTH_WINDOW_S,
    ) -> None:
        self.api_key = api_key
        self.wallet = wallet
        self.gateway_url = gateway_url.rstrip("/")
        self.core_url = core_url.rstrip("/")
        self.budget_raw = budget_raw
        self.policy: PolicyEngine | None
        if isinstance(policy, PolicyEngine):
            self.policy = policy
        elif policy is not None:
            self.policy = PolicyEngine(policy)
        elif budget_raw is not None:
            cfg0 = PolicyConfig(total_budget_raw=budget_raw)
            if policy_state_dir:
                base = Path(policy_state_dir)
                cfg0.state_path = base / "spend_state.json"
                cfg0.ledger_path = base / "ledger.jsonl"
            self.policy = PolicyEngine(cfg0)
        else:
            self.policy = None
        self.auth_window_s = auth_window_s
        self._http = http or httpx.Client(trust_env=False, timeout=HTTP_TIMEOUT_S)
        self._catalog: dict[str, Any] | None = None
        self._catalog_etag: str | None = None
        self._spent_raw = 0

    # -- 目录 --

    def catalog(self, *, force: bool = False) -> dict[str, Any]:
        """机读目录（ETag 协商缓存；core GET /catalog，无需鉴权）。

        有缓存即发条件请求（If-None-Match）：304 → 复用缓存；force=True 时
        丢弃 ETag 强制全量拉取。
        """
        if force:
            self._catalog_etag = None
        headers = {"If-None-Match": self._catalog_etag} if self._catalog_etag else {}
        resp = self._http.get(f"{self.core_url}/catalog", headers=headers)
        if resp.status_code == HTTP_NOT_MODIFIED and self._catalog is not None:
            return self._catalog
        if resp.status_code >= HTTP_BAD_REQUEST:
            raise self._gateway_error(resp)
        self._catalog_etag = resp.headers.get("ETag")
        self._catalog = resp.json()
        return self._catalog

    def service_price_raw(self, service_id: str) -> int:
        """从目录取服务定价（amount_raw 最小单位）。"""
        for svc in self.catalog().get("services", []):
            if svc.get("service_id") == service_id:
                return int(svc["manifest"]["pricing"]["amount_raw"])
        raise CoinCallError(f"服务不在目录: {service_id}（先 catalog() 查可用服务）")

    # -- 决策建议 --

    def advice(
        self,
        category: str | None = None,
        current: str | None = None,
        daily_budget_raw: int | None = None,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """core GET /advice——决策引擎的判定式投影（verb + reason 人话 + ≤2 备选）。

        verb：recommend（纯推荐）/ keep（current 即榜首）/ switch（有更优且分差≥0.05）/
        indifferent（分差死区）/ insufficient_data（分区无数据）。
        category 缺省=全量分区；current=Agent 本来想调的服务；daily_budget_raw
        提供时响应含 budget_impact 占比；timeout_s 覆写本次超时（service_quote
        内嵌走 3s 短超时，防 advice 拖垮报价）。
        """
        params: dict[str, str | int] = {}
        if category:
            params["category"] = category
        if current:
            params["current"] = current
        if daily_budget_raw is not None:
            params["daily_budget_raw"] = daily_budget_raw
        request_kwargs: dict[str, Any] = {"params": params}
        if timeout_s is not None:
            request_kwargs["timeout"] = timeout_s
        resp = self._http.get(f"{self.core_url}/advice", **request_kwargs)
        if resp.status_code >= HTTP_BAD_REQUEST:
            raise self._gateway_error(resp)
        return resp.json()

    # -- 付费调用 --

    @property
    def spent_raw(self) -> int:
        """本地预算计数器：仅成功调用累计（402/aborted 从不扣减）。"""
        return self._spent_raw

    def call(
        self,
        service_id: str,
        params: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> CallResult:
        """调用付费服务：签名支付授权 → X-PAYMENT → 网关 →（402→人话指引）。"""
        price_raw = self.service_price_raw(service_id)
        if self.policy is not None:
            self.policy.check(service_id, price_raw)  # L0：签名前评估（人话拒绝）
        if self.wallet is None:
            raise WalletError(
                "未装配本地付费钱包：LocalWallet.from_key()/create() 后传入 Client(wallet=…)"
            )

        now = int(time.time())
        auth = Authorization(
            from_=self.wallet.address,
            to=self.wallet.pay_vault,
            value=price_raw,
            valid_after=now,
            valid_before=now + self.auth_window_s,
            nonce=secrets.token_bytes(32),
        )
        signature = self.wallet.sign_payment(
            auth, verifying_contract=self.wallet.pay_vault, chain_id=self.wallet.chain_id
        )
        headers = {
            "X-Api-Key": self.api_key,
            "X-PAYMENT": build_payment_header(auth, signature),
            "X-Idempotency-Key": idempotency_key
            if idempotency_key is not None
            else _default_idempotency_key(service_id, params),
        }
        resp = self._http.post(
            f"{self.gateway_url}/call/{service_id}", json=params, headers=headers
        )

        if resp.status_code == HTTP_PAYMENT_REQUIRED:
            raise PaymentRequiredError(self._json_or_empty(resp))
        if resp.status_code >= HTTP_BAD_REQUEST:
            raise self._gateway_error(resp)

        self._spent_raw += price_raw
        if self.policy is not None:
            self.policy.record_receipt(
                self.policy.record_intent(service_id, price_raw, auth.nonce.hex()),
                resp.headers.get("X-Receipt-Id", ""),
                price_raw,
            )
        passthrough = {k: resp.headers[k] for k in RECEIPT_HEADERS if k in resp.headers}
        return CallResult(
            service_id=service_id,
            status_code=resp.status_code,
            body=resp.json() if resp.content else None,
            receipt_id=passthrough.get("X-Receipt-Id"),
            charged_raw=passthrough.get("X-Charged-Raw"),
            receipt_sig=passthrough.get("X-Receipt-Sig"),
            headers=passthrough,
        )

    # -- 内部 --

    @staticmethod
    def _json_or_empty(resp: httpx.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    @classmethod
    def _gateway_error(cls, resp: httpx.Response) -> GatewayError:
        body = cls._json_or_empty(resp)
        return GatewayError(
            status_code=resp.status_code,
            error=str(body.get("error", "http_error")),
            detail=str(body.get("detail", resp.text[:200])),
            code=str(body.get("code", "")),
        )
