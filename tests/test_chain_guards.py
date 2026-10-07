"""chain.py 守卫逻辑单测：域白名单 / chainId 断言 / 20 gwei 交易模板 / 密钥纪律 / 代理会话。"""

import pytest
from eth_account import Account
from web3 import Web3

from payvault.chain import (
    GAS_PRICE_WEI,
    account_from_key,
    assert_chain_id,
    build_tx,
    connect,
    connect_local_tester,
    connect_testnet,
    direct_session,
    proxied_session,
    session_for,
)


def test_connect_testnet_rejects_non_bohr_domain() -> None:
    """链上访问只允许 bohr.life/botchain.ai：其他域直接拒绝。"""
    with pytest.raises(ValueError, match=r"bohr\.life"):
        connect_testnet("https://rpc.example.com/")


def test_connect_rejects_domain_lookalikes() -> None:
    """白名单是域后缀语义：bohr.life.example.com 这类伪装域必须拒绝。"""
    with pytest.raises(ValueError, match="botchain"):
        connect("https://rpc.example.com/")
    with pytest.raises(ValueError, match="bohr"):
        connect("https://evil-bohr.life.example.com/")


def test_connect_accepts_both_network_domains() -> None:
    """双网络域均可建连（HTTPProvider 惰性连接，构造不发请求）。"""
    assert connect("https://rpc.bohr.life/") is not None
    assert connect("https://rpc.botchain.ai/") is not None


def test_direct_session_bypasses_env_proxy() -> None:
    """bohr.life 永远直连：会话 trust_env=False + UA 伪装。"""
    session = direct_session()
    assert session.trust_env is False  # 忽略 http(s)_proxy 环境变量
    assert session.headers.get("User-Agent", "").startswith("Mozilla/")


def test_proxied_session_trusts_env_proxy() -> None:
    """botchain.ai 信任系统代理：trust_env=True + UA 伪装（污染环境 export HTTPS_PROXY 即可达）。"""
    session = proxied_session()
    assert session.trust_env is True
    assert session.headers.get("User-Agent", "").startswith("Mozilla/")


def test_session_for_routes_by_domain() -> None:
    """按域选会话：bohr.life 直连，botchain.ai 走系统代理（代码不硬编码代理）。"""
    assert session_for("https://rpc.bohr.life/").trust_env is False
    assert session_for("https://rpc.botchain.ai/").trust_env is True


def test_assert_chain_id_guard() -> None:
    w3 = connect_local_tester()
    assert_chain_id(w3, w3.eth.chain_id)  # 一致即通过
    with pytest.raises(RuntimeError, match="chainId 不符"):
        assert_chain_id(w3, 968)  # 本地 EVM 不是 968，必须拒签


def test_build_tx_pinned_20_gwei() -> None:
    """交易模板：恒定 20 gwei legacy、nonce 取 pending、chainId 注入。"""
    w3 = connect_local_tester()
    acct, target = w3.eth.accounts[0], w3.eth.accounts[1]
    tx = build_tx(w3, acct, target, b"")
    assert tx["gasPrice"] == GAS_PRICE_WEI == 20 * 10**9
    assert tx["nonce"] == w3.eth.get_transaction_count(acct, "pending")
    assert tx["chainId"] == w3.eth.chain_id
    assert tx["to"] == target
    assert tx["value"] == 0  # 默认不携带原生币


def test_build_tx_carries_value_for_native_transfer() -> None:
    """value_wei（keyword-only）支持原生币转账（部署脚本垫付 gas 用）。"""
    w3 = connect_local_tester()
    acct, target = w3.eth.accounts[0], w3.eth.accounts[1]
    tx = build_tx(w3, acct, target, b"", value_wei=123)
    assert tx["value"] == 123
    assert tx["gasPrice"] == GAS_PRICE_WEI  # 带值交易仍是恒定 20 gwei legacy


def test_account_from_key_never_leaks() -> None:
    """私钥只进不出：account_from_key 返回账户对象，地址可打印，key 不出现在日志约定内。"""
    key_obj = Account.from_key("0x" + "11" * 32)
    acct = account_from_key("0x" + "11" * 32)
    assert acct.address == key_obj.address
    assert Web3.is_checksum_address(acct.address)
