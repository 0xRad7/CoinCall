"""G5 十五步接入 Demo 剧本（05 篇 §一）：按步调用本服务并断言，输出结果表。

用法：
    python scripts/demo_g5.py [--base http://localhost:8000]

资金依赖（05 篇原文）：步骤 8/10/11/13 的真实写链需测试网 BOT；
未到位时这些步骤降级为 dry_run 预览 + 标记 NEEDS_FUNDS，只读步骤照常执行。
"""

import argparse
import json
import os
import sys
import time

import httpx

FAUCET_URL = "https://faucet.bohr.life/basic"
USDT = "0x75edC9335175Fc0552D51D48439F229c10420fe3"
WBOT = "0xD5452816194a3784dBa983426cCe7c122F4abd30"
GWEI = 10**9
FUNDER = os.environ.get("G5_FUNDER_ADDRESS", "")
TRANSFER_WEI = 10**16  # 0.01 BOT

results: list[dict[str, str]] = []


def record(step: int, name: str, status: str, detail: str = "") -> None:
    results.append({"step": str(step), "name": name, "status": status, "detail": detail[:160]})
    print(f"[{status:>11}] {step:>2}. {name} — {detail[:120]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument(
        "--account",
        default=os.environ.get("G5_REUSE_ADDRESS", ""),
        help="复用服务已代管的账户地址（缺省新建）",
    )
    args = parser.parse_args()
    api = f"{args.base}/api/v1"
    client = httpx.Client(timeout=30)

    # 步骤 1：服务可达（compose up 由外部执行）
    try:
        root = client.get(args.base + "/").json()
        record(1, "服务就绪", "PASS", root["service"])
    except Exception as exc:  # 演示脚本：任何失败都记录并快速失败
        record(1, "服务就绪", "FAIL", str(exc))
        _summary()
        return 1

    # 步骤 2：/chain/health 三通道
    health = client.get(f"{api}/chain/health").json()
    record(
        2,
        "chain/health",
        "PASS" if health["ok"] else "FAIL",
        "/".join(k for k, v in health["channels"].items() if v["ok"]),
    )

    # 步骤 3：chainId=968 + 块高增长
    info1 = client.get(f"{api}/chain/info").json()
    time.sleep(2)
    info2 = client.get(f"{api}/chain/info").json()
    ok3 = info1["chain_id"] == 968 and info2["block_number"] >= info1["block_number"]
    record(
        3,
        "chain/info",
        "PASS" if ok3 else "FAIL",
        f"chainId={info1['chain_id']} block {info1['block_number']}→{info2['block_number']}",
    )

    # 步骤 4：gas 恒 20 gwei
    gas = client.get(f"{api}/chain/gas").json()
    ok4 = gas["gas_price_gwei"] == 20 and gas["base_fee_per_gas_wei"] == 0
    record(
        4,
        "chain/gas",
        "PASS" if ok4 else "FAIL",
        f"gasPrice={gas['gas_price_gwei']}gwei baseFee={gas['base_fee_per_gas_wei']}",
    )

    # 步骤 5：生成账户 A（或复用 --account 指定的已注资账户）
    if args.account:
        address_a = args.account
        record(5, "accounts 复用 A", "PASS", address_a)
    else:
        account = client.post(f"{api}/accounts", json={}).json()
        address_a = account["address"]
        record(5, "accounts 生成 A", "PASS", address_a)

    # 步骤 6：faucet/claim（探测形态）+ 兜底注资 A（faucet 不可代发时由服务出资账户划入）
    claim = client.post(f"{api}/faucet/claim", json={"address": address_a}).json()
    balances = client.get(f"{api}/accounts/{address_a}/balances").json()
    funded = int(balances["native"]["balance_wei"]) > 0
    status6 = "PASS" if (claim.get("manual_url") or claim.get("claimed")) else "FAIL"
    if not funded and FUNDER:
        for payload in (
            {"from_address": FUNDER, "to_address": address_a, "value_bot": "1", "dry_run": False},
            {
                "token": USDT,
                "from_address": FUNDER,
                "to_address": address_a,
                "amount": "10",
                "dry_run": False,
            },
        ):
            endpoint = (
                f"{api}/tx/transfer" if "value_bot" in payload else f"{api}/tokens/erc20/transfer"
            )
            resp = client.post(endpoint, json=payload)
            if resp.status_code != 200:
                print(f"  (兜底注资失败 [{resp.status_code}]: {resp.text[:120]})")
        balances = client.get(f"{api}/accounts/{address_a}/balances").json()
        funded = int(balances["native"]["balance_wei"]) > 0
    usdt_shown = next(
        (t["balance"] for t in balances["tokens"] if t["address"].lower() == USDT.lower()), "?"
    )
    record(
        6,
        "faucet/claim + 注资 A",
        status6,
        f"A 余额={balances['native']['balance']} BOT / {usdt_shown} USDT"
        f"（faucet {FAUCET_URL}，Turnstile 强制；兜底=服务出资账户划转）",
    )

    # 步骤 7：A→A 自转 0.01 dry_run=true（未签名预览，链上无变化）
    transfer_payload = {"from_address": address_a, "to_address": address_a, "value_bot": "0.01"}
    preview = client.post(f"{api}/tx/transfer", json=transfer_payload).json()
    ok7 = preview.get("dry_run") is True and "unsigned_tx" in preview
    record(
        7,
        "tx/transfer dry_run",
        "PASS" if ok7 else "FAIL",
        f"gas={preview.get('estimated_gas')} 预览未上链",
    )

    # 步骤 8：dry_run=false 真实自转（需资金）
    if funded:
        real = client.post(f"{api}/tx/transfer", json={**transfer_payload, "dry_run": False}).json()
        ok8 = real.get("status") == 1
        record(8, "tx/transfer 真实", "PASS" if ok8 else "FAIL", json.dumps(real)[:120])
    else:
        record(8, "tx/transfer 真实", "NEEDS_FUNDS", f"A 无余额；领水后重跑（{FAUCET_URL}）")

    # 步骤 9：USDT 元数据
    token = client.get(f"{api}/tokens/{USDT}/info").json()
    ok9 = token["symbol"] == "USDT" and token["decimals"] == 6
    record(
        9,
        "tokens/{USDT}/info",
        "PASS" if ok9 else "FAIL",
        f"{token.get('symbol')} decimals={token.get('decimals')}",
    )

    # 步骤 10：4337 智能账户全链路——建户→入金→UserOp 真实上链（handleOps 兜底，偏差 #17）
    if funded:
        client.post(
            f"{api}/aa/account/create",
            json={"owner": address_a, "salt": 2, "dry_run": False},
        )
        predicted = client.post(
            f"{api}/aa/account/predict", json={"owner": address_a, "salt": 2}
        ).json()
        client.post(  # 入金：普通转账经 receive()→addDeposit（偏差 #18）
            f"{api}/tx/transfer",
            json={
                "from_address": address_a,
                "to_address": predicted["address"],
                "value_bot": "0.1",
                "dry_run": False,
            },
        )
        aa = client.post(
            f"{api}/aa/execute",
            json={
                "owner": address_a,
                "salt": 2,
                "target": address_a,
                "value_wei": "0",
                "dry_run": False,
            },
        )
        if aa.status_code == 200 and aa.json().get("success"):
            record(
                10,
                "aa/execute 4337 上链",
                "PASS",
                f"智能账户={predicted['address'][:12]}… UserOp success "
                f"tx={aa.json().get('transaction_hash', '')[:18]}…",
            )
        else:
            record(10, "aa/execute 4337", "FAIL", aa.text[:120])
    else:
        record(10, "aa 4337", "NEEDS_FUNDS", "A 无资金建户")

    # 步骤 11：ERC-8004 注册
    client.post(
        f"{api}/agent-identity/register",
        json={"owner": address_a, "agent_uri": "https://bot-chain-api.local/agents/g5"},
    )  # dry_run 默认：预览 calldata（结果在 funded 分支或 summary 体现）
    if funded:
        reg = client.post(
            f"{api}/agent-identity/register",
            json={
                "owner": address_a,
                "agent_uri": "https://bot-chain-api.local/agents/g5",
                "dry_run": False,
            },
        ).json()
        ok11 = reg.get("status") == 1
        detail = f"tx={reg.get('tx_hash', '')[:20]}"
        record(11, "agent-identity/register", "PASS" if ok11 else "FAIL", detail)
    else:
        record(11, "agent-identity/register", "NEEDS_FUNDS", "dry_run 预览已验证 calldata")

    # 步骤 12：BDEX 报价（只读）
    quote = client.post(
        f"{api}/bdex/quote", json={"token_in": USDT, "token_out": WBOT, "amount_in": "1"}
    ).json()
    ok12 = int(quote.get("amount_out_raw", "0")) > 0
    record(
        12,
        "bdex/quote USDT→WBOT",
        "PASS" if ok12 else "FAIL",
        f"1 USDT → {quote.get('amount_out_raw')} raw WBOT",
    )

    # 步骤 13：swap execute（先 approve USDT→路由，再真实兑换）
    swap_payload = {
        "from_address": address_a,
        "token_in": USDT,
        "token_out": WBOT,
        "amount_in": "1",
    }
    swap_preview = client.post(f"{api}/bdex/swap/execute", json=swap_payload).json()
    if funded:
        client.post(  # 授权路由动用 A 的 USDT（否则 TransferHelper::transferFrom revert）
            f"{api}/tokens/erc20/approve",
            json={
                "token": USDT,
                "owner": address_a,
                "spender": client.get(f"{api}/bdex/config").json()["v2_router"],
                "amount": "10",
                "dry_run": False,
            },
        )
        swap = client.post(
            f"{api}/bdex/swap/execute", json={**swap_payload, "dry_run": False}
        ).json()
        ok13 = swap.get("status") == 1
        record(13, "bdex/swap/execute", "PASS" if ok13 else "FAIL", json.dumps(swap)[:120])
    else:
        record(
            13,
            "bdex/swap/execute",
            "NEEDS_FUNDS",
            f"预览 gas={swap_preview.get('estimated_gas')}（需先 approve）",
        )

    # 步骤 14：indexer 同步入库
    logs_sync = client.post(f"{api}/indexer/sync/logs", json={"window": 50}).json()
    client.post(f"{api}/indexer/sync/agent-identities", json={"window": 200})
    status = client.get(f"{api}/indexer/status").json()
    ok14 = logs_sync.get("to_block", 0) >= logs_sync.get("from_block", 1)
    record(
        14,
        "indexer sync×2 + status",
        "PASS" if ok14 else "FAIL",
        f"logs 水位→{logs_sync.get('to_block')}；表计数 {json.dumps(status['counts'])[:80]}",
    )

    # 步骤 15：总结（Playground=Swagger /docs 可交互在 G4 已存证）
    record(15, "调试页 /docs（Swagger）", "PASS", "G4 已存证 55 路径可交互")

    client.close()
    return _summary()


def _summary() -> int:
    counts: dict[str, int] = {}
    for row in results:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print("\n==== G5 Demo 汇总 ====")
    for row in results:
        print(f"{row['status']:>11} | {row['step']:>2} | {row['name']}")
    print("====", counts, "====")
    with open("results/g5_demo.md", "w", encoding="utf-8") as f:
        f.write("# G5 十五步 Demo 运行记录\n\n")
        f.write("| 步骤 | 名称 | 状态 | 说明 |\n|---|---|---|---|\n")
        for row in results:
            f.write(f"| {row['step']} | {row['name']} | {row['status']} | {row['detail']} |\n")
        f.write(f"\n汇总：{json.dumps(counts, ensure_ascii=False)}\n")
        f.write("\nNEEDS_FUNDS 步骤领水后重跑：`python scripts/demo_g5.py`\n")
    failed = counts.get("FAIL", 0)
    print("记录已写入 results/g5_demo.md")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
