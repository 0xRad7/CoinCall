"""PayVault ABI 副本与切片装载（事实源：coincall-contracts/artifacts/PayVault.json）。

跨仓约定（P0-5）：合约 ABI 的唯一事实源在 coincall-contracts 编译产物；
本仓只持有一份只读副本 `payvault.json`，随合约重部署同步。
keeper 用三个切片：批量函数（calldata 组装在 bot-chain-api 侧 web3 完成）、
事件（本地解码 Charged/ChargeFailed）、视图（usedNonces 恢复探测 / credits 对账）。
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_ABI_PATH = Path(__file__).parent / "payvault.json"


@lru_cache(maxsize=1)
def payvault_abi() -> list[dict[str, Any]]:
    """完整 ABI（20 条：10 函数 + 4 事件 + 6 错误）。"""
    with _ABI_PATH.open(encoding="utf-8") as f:
        return list(json.load(f))


@lru_cache(maxsize=1)
def charge_batch_abi() -> list[dict[str, Any]]:
    """chargeWithSigBatch 函数片段（POST /contracts/send 只需要这一个）。"""
    return [e for e in payvault_abi() if e.get("name") == "chargeWithSigBatch"]


@lru_cache(maxsize=1)
def event_abi() -> list[dict[str, Any]]:
    """Charged / ChargeFailed 事件片段（回执日志本地解码）。"""
    names = {"Charged", "ChargeFailed"}
    return [e for e in payvault_abi() if e.get("type") == "event" and e.get("name") in names]


@lru_cache(maxsize=1)
def view_abi() -> list[dict[str, Any]]:
    """usedNonces / credits / totalCredits 视图片段（恢复探测与 I4 对账）。"""
    names = {"usedNonces", "credits", "totalCredits"}
    return [e for e in payvault_abi() if e.get("type") == "function" and e.get("name") in names]
