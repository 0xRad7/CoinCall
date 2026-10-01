"""live：测试网 968 连通性与基础读（02 篇 test_01 矩阵，8 用例）。

证据出处：BOT_CHAIN_REPORT.md（2026-09-29 实测）与 bot_chain_scripts/results/02_chain_vitals.json。
"""

import time

import pytest
from web3.exceptions import Web3RPCError

pytestmark = pytest.mark.live

GWEI = 10**9
CHAIN_ID_TESTNET = 968
GAS_PRICE_GWEI = 20
CLIENT_VERSION_FRAGMENT = "Geth/v1.5.13"
SAMPLE_BLOCK_COUNT = 10
INTERVAL_MIN_S = 0.3
INTERVAL_MAX_S = 2.0
WAIT_FOR_BLOCK_S = 2.0


def test_chain_id_is_968(w3) -> None:
    """协议行为：eth_chainId 恒返回 0x3c8（968）。出处：02_chain_vitals.json。"""
    assert w3.eth.chain_id == CHAIN_ID_TESTNET


def test_net_version_is_968(w3) -> None:
    """协议行为：net_version 返回十进制字符串 "968"。出处：02_chain_vitals.json。"""
    assert int(w3.net.version) == CHAIN_ID_TESTNET


def test_client_version_geth(w3) -> None:
    """协议行为：Geth/v1.5.13 系客户端（EVM 兼容前提）。出处：02_chain_vitals.json。"""
    assert CLIENT_VERSION_FRAGMENT in w3.client_version


def test_block_number_alive(w3) -> None:
    """活链：两次采样块高不减。"""
    first = w3.eth.block_number
    time.sleep(WAIT_FOR_BLOCK_S)
    second = w3.eth.block_number
    assert second >= first


def test_gas_price_constant_20_gwei(w3) -> None:
    """协议行为：eth_gasPrice 恒 20 gwei（60 块采样不变）。出处：02_chain_vitals.json。"""
    assert w3.eth.gas_price == GAS_PRICE_GWEI * GWEI


def test_base_fee_per_gas_zero(w3) -> None:
    """协议行为：1559 字段存在但 baseFeePerGas=0（不做动态费用预算的依据）。"""
    block = w3.eth.get_block("latest")
    assert block["baseFeePerGas"] == 0


def test_block_interval_sub_second_chain(w3) -> None:
    """协议行为：出块间隔 ~1s（实测均值 680ms/662ms）；用近 10 块 timestamp 差均值验证。"""
    tip = w3.eth.block_number
    blocks = [w3.eth.get_block(tip - i) for i in range(SAMPLE_BLOCK_COUNT)]
    timestamps = [b["timestamp"] for b in reversed(blocks)]
    diffs = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
    assert diffs, "采样块数不足"
    mean = sum(diffs) / len(diffs)
    assert INTERVAL_MIN_S <= mean <= INTERVAL_MAX_S


def test_debug_trace_method_not_available(w3) -> None:
    """协议行为：公共 RPC 无 debug/trace 方法，应立刻报错而非挂起（-32601）。"""
    try:
        resp = w3.provider.make_request("debug_traceTransaction", ["0x" + "00" * 32])
    except Web3RPCError as exc:
        assert "32601" in str(exc)
        return
    assert "error" in resp, f"debug 方法意外可用: {resp}"
    assert "32601" in str(resp["error"]), resp["error"]


def test_get_block_receipts_available(w3) -> None:
    """协议行为：eth_getBlockReceipts 可用（indexer 增量采集通道）。出处：04_evm_compat.json。"""
    resp = w3.provider.make_request("eth_getBlockReceipts", ["latest"])
    assert "error" not in resp, resp
    assert resp.get("result") is not None
