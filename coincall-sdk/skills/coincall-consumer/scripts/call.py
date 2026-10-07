#!/usr/bin/env python3
"""CoinCall Consumer CLI 薄壳——coincall-consumer skill 的执行面。

内部走 coincall SDK，天然带 L0 策略引擎（预算/白名单/速率在签名前拦截）。

命令：
  status                     钱包健康自查（地址/链/余额/授权/L0 现值）
  catalog                    列平台付费服务（ID/定价/schema）
  quote SERVICE_ID           单服务报价（定价/收款方/余额授权/预算余量）
  call SERVICE_ID PARAMS     付费调用（真实扣款；PARAMS 为 JSON 对象字符串）
  report [--recent N]        本地账本聚合 + 最近 N 笔明细（默认 10，最新在前）

环境变量（与 coincall-mcp 一致）：
  COINCALL_API_KEY                core 签发的 api key（POST /apikeys，仅回显一次）
  COINCALL_WALLET_KEY             付费钱包私钥：0x hex 或 0600 key 文件路径
  COINCALL_GATEWAY_URL / COINCALL_CORE_URL   默认 127.0.0.1:8030 / 127.0.0.1:8020
  L0 五变量：COINCALL_{TOTAL,DAILY,PER_CALL}_BUDGET_RAW / COINCALL_ALLOWED_SERVICES /
             COINCALL_MIN_INTERVAL_S / COINCALL_MAX_CALLS_PER_HOUR
             （旧 COINCALL_BUDGET_RAW 兼容映射总额）

输出契约：结果 JSON 走 stdout；错误人话走 stderr（退出码 1=平台/支付/策略拒绝，
2=配置/参数错误）。私钥与 api key 永不打印。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# SDK 仓内直跑支持（skills/coincall-consumer/scripts/ → 仓根在 parents[3]）；
# skill 艺术品被单独拷走时退化为依赖已安装的 coincall 包。
_REPO_ROOT = Path(__file__).resolve().parents[3]
if (_REPO_ROOT / "coincall" / "__init__.py").exists() and str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from coincall.client import DEFAULT_CORE_URL, DEFAULT_GATEWAY_URL, Client  # noqa: E402
from coincall.errors import CoinCallError  # noqa: E402
from coincall.policy import PolicyConfig  # noqa: E402
from coincall.signing import PAY_VAULT_ADDRESS  # noqa: E402
from coincall.wallet import LocalWallet  # noqa: E402

HINT_NO_WALLET = "未设置 COINCALL_WALLET_KEY（0x 私钥或 0600 key 文件）——只能浏览目录，无法付费调用"
TOKEN_DECIMALS_FOR_DISPLAY = 6  # MockUSDT 6 位小数（人话金额换算用）


def build_client() -> Client:
    """env 装配（与 tools/mcp_server.py 同口径；L0 缺省即全部不设限）。"""
    wallet: LocalWallet | None = None
    key = os.environ.get("COINCALL_WALLET_KEY", "")
    if key:
        wallet = LocalWallet.from_key(key)
    policy = PolicyConfig.from_env()
    compat = os.environ.get("COINCALL_BUDGET_RAW", "")
    if compat and policy.total_budget_raw is None:  # 旧变量兼容映射总额
        policy.total_budget_raw = int(compat)
    return Client(
        api_key=os.environ.get("COINCALL_API_KEY", ""),
        wallet=wallet,
        gateway_url=os.environ.get("COINCALL_GATEWAY_URL", DEFAULT_GATEWAY_URL),
        core_url=os.environ.get("COINCALL_CORE_URL", DEFAULT_CORE_URL),
        policy=policy,
    )


def cmd_status(client: Client) -> dict[str, Any]:
    """钱包健康自查：缺什么给 hint_* 人话（水龙头/approve）。"""
    out: dict[str, Any] = {
        "api_key_present": bool(client.api_key),
        "pay_vault": PAY_VAULT_ADDRESS,
        "policy": client.policy.summary() if client.policy is not None else None,
    }
    if client.wallet is None:
        out["hint_no_wallet"] = HINT_NO_WALLET
        return out
    bal = client.wallet.balance()
    out.update(
        {
            "address": client.wallet.address,
            "chain_id": client.wallet.chain_id,
            "usdt_balance_raw": bal.usdt_balance_raw,
            "vault_allowance_raw": bal.vault_allowance_raw,
            "available_raw": bal.available_raw,
        }
    )
    if bal.usdt_balance_raw == 0:
        out["hint_fund_wallet"] = (
            "钱包 USDT 余额为 0：测试网用 wallet.mint('10')（MockUSDT 公开水龙头）充值；"
            "主网转入 USDT"
        )
    if bal.vault_allowance_raw == 0:
        out["hint_approve_vault"] = (
            "对 PayVault 授权额度为 0：执行 wallet.approve_vault('5') 授权后才能付费扣款"
        )
    return out


def cmd_quote(client: Client, service_id: str) -> dict[str, Any]:
    """单服务报价：目录定价 + 收款方 + 余额/授权 + L0 预算余量（不花钱）。"""
    services = client.catalog().get("services", [])
    svc = next((s for s in services if s.get("service_id") == service_id), None)
    if svc is None:
        available = ", ".join(str(s.get("service_id")) for s in services) or "（目录为空）"
        raise CoinCallError(f"服务不在目录: {service_id}（当前目录: {available}）")
    manifest = svc.get("manifest", {})
    pricing = manifest.get("pricing", {})
    price_raw = int(pricing.get("amount_raw", 0))
    out: dict[str, Any] = {
        "service_id": service_id,
        "name": manifest.get("name"),
        "pricing": pricing,
        "price_raw": price_raw,
        "payee": client.wallet.pay_vault if client.wallet is not None else PAY_VAULT_ADDRESS,
    }
    if client.wallet is None:
        out["hint_no_wallet"] = HINT_NO_WALLET
    else:
        bal = client.wallet.balance()
        out["wallet"] = {
            "usdt_balance_raw": bal.usdt_balance_raw,
            "vault_allowance_raw": bal.vault_allowance_raw,
            "available_raw": bal.available_raw,
        }
        out["affordable_by_wallet"] = bal.available_raw >= price_raw
    if client.policy is not None:
        summary = client.policy.summary()
        out["budget"] = summary
        lefts = (summary.get("total_left_raw"), summary.get("daily_left_raw"))
        limits = [x for x in lefts if x is not None]
        if limits:
            out["affordable_by_budget"] = min(limits) >= price_raw
    return out


def cmd_call(client: Client, service_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """付费调用（真实扣款）。L0 在签名前拦截；失败人话交上层转 stderr（不自动重试）。"""
    result = client.call(service_id, params)
    charged = int(result.charged_raw or 0)
    return {
        "service_id": result.service_id,
        "status_code": result.status_code,
        "body": result.body,
        "receipt_id": result.receipt_id,
        "charged_raw": result.charged_raw,
        # 铁律③：每笔付费调用都要能一句话报出——买了什么/花了多少/收据号
        "summary": (
            f"已调用 {result.service_id}：花费 {charged / 10**TOKEN_DECIMALS_FOR_DISPLAY} USDT"
            f"（{charged} raw），收据 {result.receipt_id}"
        ),
    }


def cmd_report(client: Client, recent: int) -> dict[str, Any]:
    """本地账本聚合：L0 余量/已花 + 最近 N 笔（最新在前）。"""
    if client.policy is None:
        return {
            "policy": None,
            "hint_no_policy": (
                "L0 未启用：设置 COINCALL_TOTAL/DAILY/PER_CALL_BUDGET_RAW"
                "（或旧 COINCALL_BUDGET_RAW）后，付费调用自动经本地预算闸与审计账本"
            ),
            "recent": [],
        }
    return {
        "policy": client.policy.summary(),
        "spent_in_session_raw": client.spent_raw,
        "recent": client.policy.ledger.recent(max(1, min(recent, 100))),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="call.py",
        description="CoinCall 消费 CLI（skill 薄壳：内部走 SDK，天然带 L0 预算/账本）",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="钱包健康自查（挂载后第一件事）")
    sub.add_parser("catalog", help="列平台付费服务（ID/定价/schema）")
    quote_p = sub.add_parser("quote", help="单服务报价（花钱前先看价）")
    quote_p.add_argument("service_id", help="目录中的服务 ID")
    call_p = sub.add_parser("call", help="付费调用（真实扣款；失败不要自动重试）")
    call_p.add_argument("service_id", help="目录中的服务 ID")
    call_p.add_argument("params", help='JSON 对象字符串，如 \'{"query": "BTC 价格"}\'')
    report_p = sub.add_parser("report", help="本地账本聚合 + 最近明细")
    report_p.add_argument("--recent", type=int, default=10, help="最近 N 笔（默认 10）")
    args = parser.parse_args(argv)

    try:
        client = build_client()
        if args.command == "status":
            payload: dict[str, Any] = cmd_status(client)
        elif args.command == "catalog":
            payload = client.catalog()
        elif args.command == "quote":
            payload = cmd_quote(client, args.service_id)
        elif args.command == "call":
            try:
                params = json.loads(args.params)
            except ValueError as exc:
                print(f"params 不是合法 JSON: {exc}", file=sys.stderr)
                return 2
            if not isinstance(params, dict):
                print("params 必须是 JSON 对象（该服务 input_schema 的请求体）", file=sys.stderr)
                return 2
            payload = cmd_call(client, args.service_id, params)
        else:  # report
            payload = cmd_report(client, args.recent)
    except CoinCallError as exc:
        print(str(exc), file=sys.stderr)  # 人话（402 指引 / L0 拒绝理由 / 钱包配置）
        return 1

    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, default=str)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
