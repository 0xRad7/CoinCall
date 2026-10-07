"""L0 本地策略引擎（agent-wallet-trust.md §3，2026-10-07）。

Agent 持钥的五条资金损失向量中，本引擎根治/缓解 T1（失控循环）、T2（金额冗
余层）、T3（目标限面）、T5（不可追溯）；T4（密钥外泄）的物理上限靠"专用小额
钱包"约束爆炸半径——诚实边界见报告 §3-L0。

设计原则（借自 Turnkey/Privy/x402/Safe，见报告 §2）：默认拒绝、三重硬预算落
盘持久（重启不清零）、目标白名单、签名前评估、只追加审计账本、触底熔断只读。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_STATE_DIR = Path.home() / ".coincall"


class PolicyViolationError(Exception):
    """策略拒绝（人话 message 直接可给 Agent/用户看）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class PolicyConfig:
    """三重预算 + 白名单 + 速率。None=该项不设限（默认拒绝仅对白名单生效）。"""

    total_budget_raw: int | None = None
    daily_budget_raw: int | None = None
    max_per_call_raw: int | None = None
    allowed_service_ids: list[str] | None = None  # None=不限制（引擎外仍有结构防御）
    min_interval_s: float = 0.0
    max_calls_per_hour: int | None = None
    state_path: Path | None = None  # 缺省 ~/.coincall/spend_state.json（0600 原子写）
    ledger_path: Path | None = None  # 缺省 ~/.coincall/ledger.jsonl（只追加）

    @classmethod
    def from_env(cls) -> PolicyConfig:
        """MCP/env 装配口：COINCALL_{TOTAL,DAILY,PER_CALL}_BUDGET_RAW 等。"""

        def _int(name: str) -> int | None:
            v = os.environ.get(name, "")
            return int(v) if v else None

        def _float(name: str) -> float:
            v = os.environ.get(name, "")
            return float(v) if v else 0.0

        services = os.environ.get("COINCALL_ALLOWED_SERVICES", "")
        return cls(
            total_budget_raw=_int("COINCALL_TOTAL_BUDGET_RAW"),
            daily_budget_raw=_int("COINCALL_DAILY_BUDGET_RAW"),
            max_per_call_raw=_int("COINCALL_PER_CALL_BUDGET_RAW"),
            allowed_service_ids=[s.strip() for s in services.split(",") if s.strip()] or None,
            min_interval_s=_float("COINCALL_MIN_INTERVAL_S"),
            max_calls_per_hour=_int("COINCALL_MAX_CALLS_PER_HOUR"),
        )


@dataclass
class _SpendState:
    """落盘持久（0600 原子写）——重启不清零是 T1 防御的关键。"""

    total_spent_raw: int = 0
    day_key: str = ""
    daily_spent_raw: int = 0
    last_call_ts: float = 0.0
    hour_key: str = ""
    hourly_calls: int = 0
    disabled_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _utc_day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _utc_hour(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H", time.gmtime(ts))


class Ledger:
    """只追加审计账本（intent → receipt → onchain 三段，JSONL）。

    与平台 calls 流水、链上 Charged 事件三方可对账（T5）。
    """

    def __init__(self, path: Path | None) -> None:
        self._path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def _append(self, row: dict[str, Any]) -> None:
        if self._path is None:
            return
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def intent(self, receipt_id: str, service_id: str, amount_raw: int, auth_nonce: str) -> None:
        self._append(
            {
                "stage": "intent",
                "receipt_id": receipt_id,
                "ts": time.time(),
                "service_id": service_id,
                "amount_raw": amount_raw,
                "auth_nonce": auth_nonce,
            }
        )

    def receipt(self, receipt_id: str, gateway_receipt: str, charged_raw: int) -> None:
        self._append(
            {
                "stage": "receipt",
                "receipt_id": receipt_id,
                "ts": time.time(),
                "gateway_receipt": gateway_receipt,
                "charged_raw": charged_raw,
            }
        )

    def onchain(self, receipt_id: str, tx_hash: str, charged_raw: int) -> None:
        self._append(
            {
                "stage": "onchain",
                "receipt_id": receipt_id,
                "ts": time.time(),
                "tx_hash": tx_hash,
                "charged_raw": charged_raw,
            }
        )


class PolicyEngine:
    """签名前评估（check）→ 三段记账。check 不通过绝不进签名路径。"""

    def __init__(self, config: PolicyConfig) -> None:
        self.cfg = config
        state_path = config.state_path or (DEFAULT_STATE_DIR / "spend_state.json")
        self._state_path = state_path
        state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()
        self.ledger = Ledger(config.ledger_path or (DEFAULT_STATE_DIR / "ledger.jsonl"))

    # ---- 加载/落盘（0600 原子写，借 wallet.py 纪律） ----
    def _load_state(self) -> _SpendState:
        if self._state_path.exists():
            data = json.loads(self._state_path.read_text())
            return _SpendState(**data)
        return _SpendState()

    def _save_state(self) -> None:
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state.__dict__, ensure_ascii=False))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._state_path)

    # ---- 熔断 ----
    def disable(self, reason: str) -> None:
        self.state.disabled_reason = reason
        self._save_state()

    def enable(self) -> None:
        self.state.disabled_reason = None
        self._save_state()

    # ---- 签名前检查（默认拒绝原则） ----
    def check(self, service_id: str, amount_raw: int) -> None:
        """不通过抛 PolicyViolation（人话），通过即记账并落盘。"""
        if self.state.disabled_reason:
            raise PolicyViolationError(f"策略引擎已停机：{self.state.disabled_reason}")
        allowed = self.cfg.allowed_service_ids
        if allowed is not None and service_id not in allowed:
            raise PolicyViolationError(
                f"服务 {service_id} 不在白名单（默认拒绝；COINCALL_ALLOWED_SERVICES 可调整）"
            )
        if self.cfg.max_per_call_raw is not None and amount_raw > self.cfg.max_per_call_raw:
            raise PolicyViolationError(
                f"单笔上限 {self.cfg.max_per_call_raw}，本笔 {amount_raw}（max_per_call_raw）"
            )
        now = time.time()
        day = _utc_day(now)
        if self.state.day_key != day:
            self.state.day_key, self.state.daily_spent_raw = day, 0
        hour = _utc_hour(now)
        if self.state.hour_key != hour:
            self.state.hour_key, self.state.hourly_calls = hour, 0
        if self.cfg.daily_budget_raw is not None and (
            self.state.daily_spent_raw + amount_raw > self.cfg.daily_budget_raw
        ):
            raise PolicyViolationError(
                f"日额 {self.cfg.daily_budget_raw}（已花 {self.state.daily_spent_raw}），"
                f"本笔 {amount_raw} 被拒——触底自动只读"
            )
        if self.cfg.total_budget_raw is not None and (
            self.state.total_spent_raw + amount_raw > self.cfg.total_budget_raw
        ):
            raise PolicyViolationError(
                f"总额预算 {self.cfg.total_budget_raw}（已花 {self.state.total_spent_raw}），"
                f"本笔 {amount_raw} 被拒——触底自动只读"
            )
        if self.cfg.min_interval_s > 0 and (
            now - self.state.last_call_ts < self.cfg.min_interval_s
        ):
            raise PolicyViolationError(
                f"调用间隔不足 {self.cfg.min_interval_s}s"
                f"（上一笔 {now - self.state.last_call_ts:.1f}s 前）"
            )
        if self.cfg.max_calls_per_hour is not None and (
            self.state.hourly_calls + 1 > self.cfg.max_calls_per_hour
        ):
            raise PolicyViolationError(
                f"小时速率上限 {self.cfg.max_calls_per_hour}（已 {self.state.hourly_calls} 笔）"
            )
        # 通过 → 记账
        self.state.total_spent_raw += amount_raw
        self.state.daily_spent_raw += amount_raw
        self.state.hourly_calls += 1
        self.state.last_call_ts = now
        self._save_state()

    # ---- 三段账本 ----
    def record_intent(self, service_id: str, amount_raw: int, auth_nonce: str) -> str:
        receipt_id = f"pl_{uuid.uuid4().hex[:12]}"
        self.ledger.intent(receipt_id, service_id, amount_raw, auth_nonce)
        return receipt_id

    def record_receipt(self, receipt_id: str, gateway_receipt: str, charged_raw: int) -> None:
        self.ledger.receipt(receipt_id, gateway_receipt, charged_raw)

    def record_onchain(self, receipt_id: str, tx_hash: str, charged_raw: int) -> None:
        self.ledger.onchain(receipt_id, tx_hash, charged_raw)
