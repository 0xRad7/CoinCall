"""live：账户/余额/nonce（02 篇 test_02 矩阵）。

交叉验证：RPC eth_getBalance 与 Blockscout /api/v2/addresses/{addr}.coin_balance 一致。
"""

import pytest
from eth_account import Account
from web3 import Web3

pytestmark = pytest.mark.live

ZERO = 0


def test_new_eoa_shape_nonce_balance(w3) -> None:
    """新 EOA：checksum 地址、起始 nonce=0、balance=0。"""
    acct = Account.create()
    assert acct.address == Web3.to_checksum_address(acct.address)
    assert w3.eth.get_transaction_count(acct.address, "latest") == ZERO
    assert w3.eth.get_balance(acct.address) == ZERO


def test_get_code_empty_for_eoa(w3) -> None:
    """EOA 的 eth_getCode 返回空。"""
    acct = Account.create()
    assert w3.eth.get_code(acct.address) in (b"", "0x")


def test_nonce_latest_equals_pending_for_idle_account(w3) -> None:
    """静默地址 pending/latest nonce 一致且为 0。"""
    acct = Account.create()
    latest = w3.eth.get_transaction_count(acct.address, "latest")
    pending = w3.eth.get_transaction_count(acct.address, "pending")
    assert latest == pending == ZERO


def test_rpc_balance_matches_blockscout(w3, explorer) -> None:
    """交叉验证：最新块矿工地址的 RPC 余额 == Blockscout coin_balance（wei）。"""
    block = w3.eth.get_block("latest")
    miner = block["miner"]
    data = explorer.get(f"/addresses/{miner}")
    coin_balance = data.get("coin_balance") or 0
    assert w3.eth.get_balance(miner) == int(coin_balance)


def test_blockscout_stats_shape(explorer) -> None:
    """/api/v2/stats 免 key 可用且关键字段在位（M1 /chain/stats 的数据源前提）。"""
    stats = explorer.get("/stats")
    for field in ("total_transactions", "total_addresses", "transactions_today"):
        assert field in stats, f"stats 缺字段 {field}"
