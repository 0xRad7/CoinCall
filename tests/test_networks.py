"""主网参数化（networks 表 / env 切换 / 主网参数正确性）：零网络（A2）。

覆盖：resolve_network 缺省与显式解析、非法值人话错误、字段级 env 覆写、
LocalWallet 缺省从网络解析（显式参数 > env > 网络表缺省）、主网 677 签名域、
ChainConnection 期望链断言与 RPC 端点解析（web3 provider 惰性构造，不发请求）。
"""

import pytest
from fake_chain import FakeChain

from coincall.chain import TOKEN_ADDRESS as CHAIN_TOKEN_SNAPSHOT
from coincall.chain import ChainConnection
from coincall.errors import CoinCallError, WalletError
from coincall.networks import (
    ENV_CHAIN_ID,
    ENV_NETWORK,
    ENV_RPC_URL,
    ENV_TOKEN_ADDRESS,
    MAINNET,
    TESTNET,
    resolve_network,
)
from coincall.signing import PAY_VAULT_ADDRESS, Authorization, eip712_digest, recover_signer
from coincall.wallet import LocalWallet

# anvil 账户 #1（公开助记词派生，测试网无价值）
ANVIL1_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"

NETWORK_ENVS = (ENV_NETWORK, ENV_CHAIN_ID, ENV_RPC_URL, ENV_TOKEN_ADDRESS)


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉宿主 shell 可能残留的 COINCALL_NETWORK*（保证缺省用例密闭）。"""
    for var in NETWORK_ENVS:
        monkeypatch.delenv(var, raising=False)


class _StubEth:
    def __init__(self, chain_id: int) -> None:
        self.chain_id = chain_id

    def get_transaction_count(self, addr: str, status: str) -> int:
        return 7


class _StubW3:
    """只够 build_tx 走通的 w3 桩（chainId 断言分支零网络可测）。"""

    def __init__(self, chain_id: int) -> None:
        self.eth = _StubEth(chain_id)


# -- 网络表与缺省解析 --


@pytest.mark.unit
def test_default_resolves_testnet(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    net = resolve_network()
    assert net.name == "testnet"
    assert net.chain_id == 968
    assert net.rpc_url == "https://rpc.bohr.life/"
    assert net.token_address.lower() == "0x75edc9335175fc0552d51d48439f229c10420fe3"


@pytest.mark.unit
def test_network_table_values() -> None:
    """主网参数锚点：BOT Chain 677 / rpc.botchain.ai / USDT 0xaBabc7…87a3C。"""
    assert TESTNET.chain_id == 968
    assert MAINNET.chain_id == 677
    assert MAINNET.rpc_url == "https://rpc.botchain.ai/"
    assert MAINNET.token_address == "0xaBabc7Ddc03e501d190C676BF3d92ef0e6e87a3C"
    assert MAINNET.token_address != TESTNET.token_address
    # 测试网 token 与 chain.py 历史快照一致（向后兼容锚）
    assert TESTNET.token_address == CHAIN_TOKEN_SNAPSHOT


@pytest.mark.unit
def test_env_switches_mainnet_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    for value in ("mainnet", "MAINNET", " MainNet "):
        _clean_env(monkeypatch)
        monkeypatch.setenv(ENV_NETWORK, value)
        assert resolve_network() == MAINNET


@pytest.mark.unit
def test_unknown_network_raises_human_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(ENV_NETWORK, "devnet")
    with pytest.raises(CoinCallError, match=ENV_NETWORK):
        resolve_network()


@pytest.mark.unit
def test_bad_chain_id_override_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(ENV_CHAIN_ID, "not-a-number")
    with pytest.raises(CoinCallError, match=ENV_CHAIN_ID):
        resolve_network()


@pytest.mark.unit
def test_field_overrides_win_over_table(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(ENV_CHAIN_ID, "42")
    monkeypatch.setenv(ENV_RPC_URL, "https://private-rpc.example/")
    monkeypatch.setenv(ENV_TOKEN_ADDRESS, "0x00000000000000000000000000000000000000ff")
    net = resolve_network()
    assert net.chain_id == 42
    assert net.rpc_url == "https://private-rpc.example/"
    assert net.token_address == "0x00000000000000000000000000000000000000ff"
    assert net.name == "testnet"  # 未覆写字段保持网络表缺省


@pytest.mark.unit
def test_field_override_on_mainnet_keeps_other_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(ENV_NETWORK, "mainnet")
    monkeypatch.setenv(ENV_RPC_URL, "https://mainnet-mirror.example/")
    net = resolve_network()
    assert net.chain_id == MAINNET.chain_id
    assert net.rpc_url == "https://mainnet-mirror.example/"
    assert net.token_address == MAINNET.token_address


@pytest.mark.unit
def test_resolve_network_accepts_explicit_env_mapping() -> None:
    net = resolve_network({ENV_NETWORK: "mainnet"})
    assert net == MAINNET


# -- LocalWallet 缺省从网络解析（显式参数 > env > 网络表） --


@pytest.mark.unit
def test_wallet_default_is_testnet_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    w = LocalWallet.from_key(ANVIL1_KEY, chain=FakeChain())
    assert w.chain_id == 968
    assert w.token_address == TESTNET.token_address
    assert w.pay_vault == PAY_VAULT_ADDRESS  # 金库缺省仍是测试网 0xa6E8…（需显式传参换主网金库）


@pytest.mark.unit
def test_wallet_env_switches_to_mainnet_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(ENV_NETWORK, "mainnet")
    w = LocalWallet.from_key(ANVIL1_KEY, chain=FakeChain())
    assert w.chain_id == 677
    assert w.token_address == MAINNET.token_address
    # 显式参数永远优先于 env
    w2 = LocalWallet.from_key(ANVIL1_KEY, chain=FakeChain(), chain_id=968)
    assert w2.chain_id == 968
    w3 = LocalWallet.from_key(ANVIL1_KEY, chain=FakeChain(), token_address="0xdeadbeef" + "0" * 32)
    assert w3.token_address.startswith("0xdeadbeef")


@pytest.mark.unit
def test_wallet_field_env_overrides_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(ENV_TOKEN_ADDRESS, "0x00000000000000000000000000000000000000ff")
    w = LocalWallet.from_key(ANVIL1_KEY, chain=FakeChain())
    assert w.chain_id == 968  # 未覆写字段不变
    assert w.token_address == "0x00000000000000000000000000000000000000ff"


@pytest.mark.unit
def test_wallet_create_follows_network_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(ENV_NETWORK, "mainnet")
    w = LocalWallet.create(chain=FakeChain())
    assert w.chain_id == 677
    assert w.token_address == MAINNET.token_address


# -- 主网签名域（EIP-712 chainId=677） --


@pytest.mark.unit
def test_sign_payment_on_mainnet_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(ENV_NETWORK, "mainnet")
    w = LocalWallet.from_key(ANVIL1_KEY, chain=FakeChain())
    auth = Authorization(
        from_=w.address,
        to=PAY_VAULT_ADDRESS,
        value=10_000,
        valid_after=1,
        valid_before=601,
        nonce=b"\x07" * 32,
    )
    sig = w.sign_payment(auth)  # chain_id 缺省=钱包 chain_id=677
    digest677 = eip712_digest(auth, PAY_VAULT_ADDRESS, 677)
    assert recover_signer(digest677, v=sig.v, r=sig.r, s=sig.s) == w.address
    # 域绑定：主网签名在测试网域上不可恢复（链 ID 进 domainSeparator）
    assert eip712_digest(auth, PAY_VAULT_ADDRESS, 968) != digest677


# -- ChainConnection：期望链断言 + RPC 端点解析（provider 惰性，零网络） --


@pytest.mark.unit
def test_connection_defaults_follow_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    conn = ChainConnection()
    assert conn.expected_chain_id == 968
    assert str(conn._w3.provider.endpoint_uri) == TESTNET.rpc_url

    monkeypatch.setenv(ENV_NETWORK, "mainnet")
    conn_m = ChainConnection()
    assert conn_m.expected_chain_id == 677
    assert str(conn_m._w3.provider.endpoint_uri) == MAINNET.rpc_url

    monkeypatch.setenv(ENV_RPC_URL, "https://private-rpc.example/")
    conn_r = ChainConnection()
    assert str(conn_r._w3.provider.endpoint_uri) == "https://private-rpc.example/"
    assert conn_r.expected_chain_id == 677  # RPC 覆写不改变期望链


@pytest.mark.unit
def test_wallet_chain_connection_carries_wallet_chain_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(ENV_NETWORK, "mainnet")
    w = LocalWallet.from_key(ANVIL1_KEY)  # 未注入 FakeChain：惰性建 ChainConnection（不发包）
    assert w.chain_id == 677
    assert w.chain.expected_chain_id == 677  # A4：链上 chainId == 钱包 chain_id


@pytest.mark.unit
def test_build_tx_asserts_onchain_matches_expected_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv(ENV_NETWORK, "mainnet")
    conn = ChainConnection(expected_chain_id=677)

    conn._w3 = _StubW3(968)  # RPC 实际在测试网 → 主网钱包必须拒构建
    with pytest.raises(WalletError, match="677"):
        conn.build_tx("0x70997970C51812dc3A010C7d01b50e0d17dc79C8", "0x" + "aa" * 20, b"", 100_000)

    conn._w3 = _StubW3(677)  # 链上与期望一致 → 正常组 tx
    tx = conn.build_tx("0x70997970C51812dc3A010C7d01b50e0d17dc79C8", "0x" + "aa" * 20, b"", 100_000)
    assert tx["chainId"] == 677
    assert tx["gasPrice"] == 20 * 10**9  # POA 恒 20 gwei 不随网络变
