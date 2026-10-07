"""P0 端到端存证脚本（W6）：一次真实付费调用的全链路。

前置：8010(coincall-bot-chain-api)/8020(coincall-core) 在跑；8030 网关需以
COINCALL_KEEPER_ENABLED=true 启动。消费者=anvil#1、Provider 钱包=anvil#2
（公开测试账户，仅测试网演示）。产出 results/e2e_p0.json 存证。

用法：uv run python scripts/e2e_p0.py
"""

from __future__ import annotations

import json
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from eth_account import Account
from eth_keys import keys as eth_keys
from web3 import Web3

from app.core.payment import Authorization, XPayment, build_x_payment_header, eip712_digest

CORE = "http://127.0.0.1:8020"
GATEWAY = "http://127.0.0.1:8030"
RPC = "https://rpc.bohr.life/"
CHAIN_ID = 968
PAY_VAULT = "0xFe91F55C0e7Ccbc4A6619C67544Ab453cf79C471"
MOCK_USDT = "0x4F8f2eaAA3988E9f59B72C93262DDC1084E540fb"
SERVICE_ID = "svc_e2e_demo"
PRICE_RAW = 10000  # 0.01 USDT

# anvil 公开助记词派生账户（无资金价值，仅测试网演示）
CONSUMER = Account.from_key("0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d")
PROVIDER_WALLET = "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"

TOKEN_ABI = [
    {
        "name": "mint",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "t", "type": "address"}, {"name": "a", "type": "uint256"}],
        "outputs": [],
    },
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "s", "type": "address"}, {"name": "a", "type": "uint256"}],
        "outputs": [],
    },
]
CHARGED_ABI = [
    {
        "name": "Charged",
        "type": "event",
        "anonymous": False,
        "inputs": [
            {"name": "provider", "type": "address", "indexed": True},
            {"name": "from", "type": "address", "indexed": True},
            {"name": "value", "type": "uint256", "indexed": False},
            {"name": "nonce", "type": "bytes32", "indexed": True},
        ],
    }
]


def log(step: str, **kw: Any) -> None:
    print(
        f"[{time.strftime('%H:%M:%S')}] {step} " + json.dumps(kw, default=str, ensure_ascii=False)
    )


def send(w3: Web3, call: Any, nonce: int) -> str:
    tx = call.build_transaction(
        {
            "from": CONSUMER.address,
            "nonce": nonce,
            "gas": 150_000,
            "maxFeePerGas": 20 * 10**9,
            "maxPriorityFeePerGas": 20 * 10**9,
            "chainId": CHAIN_ID,
            "type": 2,
        }
    )
    signed = CONSUMER.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed["raw_transaction"])
    receipt = w3.eth.wait_for_transaction_receipt(h, timeout=90)
    assert receipt["status"] == 1, f"tx {h.hex()} 失败"
    return h.hex()


def main() -> None:
    w3 = Web3(Web3.HTTPProvider(RPC))
    assert w3.eth.chain_id == CHAIN_ID, f"chainId={w3.eth.chain_id} 非 968，禁止签名"
    ev: dict[str, Any] = {"started": datetime.now(UTC).isoformat()}

    with httpx.Client(trust_env=False, timeout=10) as c:
        r = c.get(f"{CORE}/healthz")
        assert r.status_code == 200, "core 未启动"
        r = c.get(f"{GATEWAY}/healthz")
        assert r.status_code == 200, "gateway 未启动"

    # ① 上架服务（internal echo provider，"30 秒挂服务"口径）
    manifest = {
        "service_id": SERVICE_ID,
        "name": "E2E Echo Demo",
        "version": "1.0.0",
        "description": "P0 端到端存证用回显服务",
        "provider": {"agent_id": 162, "wallet": PROVIDER_WALLET, "display_name": "e2e-provider"},
        "endpoint": {"type": "internal"},
        "pricing": {
            "token": "USDT",
            "model": "per_call",
            "amount": "0.01",
            "amount_raw": str(PRICE_RAW),
        },
        "chain": {"network": CHAIN_ID},
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        "output_schema": {"type": "object"},
    }
    with httpx.Client(trust_env=False, timeout=10) as c:
        r = c.post(f"{CORE}/manifests", json=manifest)
        assert r.status_code in (200, 201), r.text
        ev["manifest_hash"] = r.json().get("manifest_hash")
        log("① manifest published", hash=ev["manifest_hash"])

        # ② 签发 api key（绑定消费者钱包，明文只此一次）
        r = c.post(f"{CORE}/apikeys", json={"consumer_wallet": CONSUMER.address})
        assert r.status_code in (200, 201), r.text
        key = r.json()
        api_key: str = key["api_key"]
        ev["key_id"] = key.get("key_id")
        log("② apikey issued", key_id=ev["key_id"])

    # ③ 消费者自铸 1 USDT + approve PayVault（公开 mint；anvil 测试账户直签，20 gwei）
    usdt = w3.eth.contract(address=MOCK_USDT, abi=TOKEN_ABI)
    nonce = w3.eth.get_transaction_count(CONSUMER.address)
    ev["tx_mint"] = send(w3, usdt.functions.mint(CONSUMER.address, 1_000_000), nonce)
    ev["tx_approve"] = send(w3, usdt.functions.approve(PAY_VAULT, 10**9), nonce + 1)
    log("③ funded+approved", mint=ev["tx_mint"], approve=ev["tx_approve"])

    # ④ 付费调用：本地 EIP-712 签 X-PAYMENT → POST /call
    auth = Authorization(
        from_=CONSUMER.address,
        to=PAY_VAULT,
        value=str(PRICE_RAW),
        valid_after=int(time.time()) - 60,
        valid_before=int(time.time()) + 600,
        nonce="0x" + secrets.token_bytes(32).hex(),
    )
    digest = eip712_digest(auth, PAY_VAULT, CHAIN_ID)  # type: ignore[arg-type]
    sig = eth_keys.PrivateKey(CONSUMER.key).sign_msg_hash(digest)
    xp = XPayment(
        **auth.model_dump(by_alias=True),
        v=sig.v,
        r="0x" + sig.r.to_bytes(32, "big").hex(),
        s="0x" + sig.s.to_bytes(32, "big").hex(),
    )
    header = build_x_payment_header(xp)
    with httpx.Client(trust_env=False, timeout=30) as c:
        r = c.post(
            f"{GATEWAY}/call/{SERVICE_ID}",
            json={"text": "hello coincall p0"},
            headers={"X-Api-Key": api_key, "X-PAYMENT": header},
        )
        assert r.status_code == 200, f"{r.status_code} {r.text[:500]}"
        ev["receipt_id"] = r.headers.get("X-Receipt-Id")
        ev["charged_raw"] = r.headers.get("X-Charged-Raw")
        ev["call_body"] = r.json()
        log("④ paid call 200", receipt=ev["receipt_id"], charged_raw=ev["charged_raw"])

    # ⑤ 等 keeper 自动结算（3 笔或 30s 触发）并取队列状态
    deadline = time.time() + 120
    while time.time() < deadline:
        with httpx.Client(trust_env=False, timeout=10) as c:
            st = c.get(f"{GATEWAY}/internal/keeper/status").json()
        last = st.get("last_batch") or {}
        if last.get("tx_hash"):
            ev["keeper_status"] = st
            log("⑤ keeper settled", tx=last["tx_hash"], charged=last.get("charged"))
            break
        time.sleep(3)
    else:
        raise AssertionError("keeper 120s 未结算")

    # ⑥ 链上 Charged 与本笔 nonce 对账（I4 口径）
    vault = w3.eth.contract(address=PAY_VAULT, abi=CHARGED_ABI)
    logs = vault.events.Charged.get_logs(from_block=w3.eth.block_number - 300, to_block="latest")
    hit = [e for e in logs if e["args"]["nonce"].hex() == auth.nonce[2:]]
    assert hit, "链上未找到本笔 Charged"
    a = hit[0]["args"]
    assert a["from"].lower() == CONSUMER.address.lower(), "Charged.from 与消费者不符"
    assert a["value"] == PRICE_RAW and a["provider"].lower() == PROVIDER_WALLET.lower()
    ev["charged_event"] = {
        "tx": hit[0]["transactionHash"].hex(),
        "block": hit[0]["blockNumber"],
        "value": a["value"],
        "provider": a["provider"],
    }
    log(
        "⑥ on-chain Charged verified",
        tx=ev["charged_event"]["tx"],
        block=ev["charged_event"]["block"],
    )

    out = Path("results/e2e_p0.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(ev, indent=2, ensure_ascii=False, default=str))
    print(f"E2E PASS — 存证 {out}")


if __name__ == "__main__":
    main()
