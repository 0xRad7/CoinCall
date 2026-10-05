"""T16：本地付费钱包生成/导入，私钥不出机器（03 §3；绑定 TEST-20261005221052 T16）。

覆盖：create / from_key（直钥 / env / 0600 文件 / 权限与内容拒绝）/ approve_vault 本地直签
（chainId==968 断言先于签名、20 gwei、approve calldata 逐字节）/ balance 可用额口径 /
mint（测试网 MockUSDT 公开 mint）/ 私钥不出现在任何输出与网络载荷。
"""

import re
import stat
from decimal import Decimal

import pytest
from fake_chain import FakeChain

from coincall.chain import TOKEN_ADDRESS, TOKEN_DECIMALS
from coincall.errors import WalletError
from coincall.signing import PAY_VAULT_ADDRESS, Authorization, eip712_digest, recover_signer
from coincall.wallet import ENV_WALLET_KEY, LocalWallet

# anvil 账户 #1（公开助记词派生，测试网无价值）
ANVIL1_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
ANVIL1_ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _wallet(chain: FakeChain | None = None, key: str = ANVIL1_KEY) -> LocalWallet:
    return LocalWallet.from_key(key, chain=chain)


@pytest.mark.unit
def test_create_generates_fresh_eoa() -> None:
    w1 = LocalWallet.create(chain=FakeChain())
    w2 = LocalWallet.create(chain=FakeChain())
    assert ADDR_RE.match(w1.address)
    assert w1.address != w2.address
    assert w1.address == w1._account.address  # 生成路径对 account 的一致性锚点


@pytest.mark.unit
def test_from_key_direct_hex() -> None:
    w = _wallet()
    assert w.address == ANVIL1_ADDR


@pytest.mark.unit
def test_from_key_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_WALLET_KEY, ANVIL1_KEY)
    w = LocalWallet.from_key(chain=FakeChain())
    assert w.address == ANVIL1_ADDR


@pytest.mark.unit
def test_from_key_via_env_file_path(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    key_file = tmp_path / "wallet.key"
    key_file.write_text(ANVIL1_KEY + "\n")
    key_file.chmod(0o600)
    monkeypatch.setenv(ENV_WALLET_KEY, str(key_file))
    w = LocalWallet.from_key(chain=FakeChain())
    assert w.address == ANVIL1_ADDR


@pytest.mark.unit
def test_from_key_via_0600_file(tmp_path) -> None:
    key_file = tmp_path / "wallet.key"
    key_file.write_text(ANVIL1_KEY)
    key_file.chmod(0o600)
    w = LocalWallet.from_key(str(key_file), chain=FakeChain())
    assert w.address == ANVIL1_ADDR
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600


@pytest.mark.unit
def test_from_key_rejects_loose_file_permissions(tmp_path) -> None:
    key_file = tmp_path / "wallet.key"
    key_file.write_text(ANVIL1_KEY)
    key_file.chmod(0o644)
    with pytest.raises(WalletError, match="0600"):
        LocalWallet.from_key(str(key_file), chain=FakeChain())


@pytest.mark.unit
def test_from_key_rejects_bad_content(tmp_path) -> None:
    key_file = tmp_path / "wallet.key"
    key_file.write_text("not-a-key")
    key_file.chmod(0o600)
    with pytest.raises(WalletError, match="私钥"):
        LocalWallet.from_key(str(key_file), chain=FakeChain())


@pytest.mark.unit
def test_from_key_rejects_missing_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_WALLET_KEY, raising=False)
    with pytest.raises(WalletError, match=ENV_WALLET_KEY):
        LocalWallet.from_key(chain=FakeChain())
    with pytest.raises(WalletError):
        LocalWallet.from_key("/nonexistent/wallet.key", chain=FakeChain())


@pytest.mark.unit
def test_private_key_never_leaks_in_outputs() -> None:
    chain = FakeChain(balance_raw=10, allowance_raw=5)
    w = _wallet(chain=chain)
    key_hex = ANVIL1_KEY.lower()
    blob = " ".join(
        [
            repr(w),
            str(w),
            repr(w.balance()),
            str(w.balance()),
            repr(w.approve_vault("5")),
            str(w.approve_vault("5")),
        ]
    )
    assert key_hex not in blob.lower()
    assert key_hex[2:] not in blob.lower()  # 裸 hex 也不得出现
    # 网络载荷：build_tx 入参与 send_raw 载荷均为推导数据（地址/calldata/签名后的 raw tx）
    for tx in chain.built_txs:
        assert key_hex not in str(tx).lower()
    for raw in chain.sent_raw:
        assert isinstance(raw, bytes) and raw
        assert bytes.fromhex(key_hex[2:]) not in raw  # 未签名私钥绝不进 raw tx


@pytest.mark.unit
def test_approve_vault_signs_locally_with_chain968_and_20gwei() -> None:
    chain = FakeChain()
    w = _wallet(chain=chain)
    result = w.approve_vault("5")
    # 唯一一笔 tx：发往 MockUSDT，approve(PayVault, 5 USDT=5_000_000 raw)
    assert len(chain.built_txs) == 1
    tx = chain.built_txs[0]
    assert tx["chainId"] == 968
    assert tx["gasPrice"] == 20 * 10**9
    assert tx["to"] == TOKEN_ADDRESS
    assert tx["from"] == ANVIL1_ADDR
    assert tx["data"] == bytes.fromhex("095ea7b3") + _pad_addr(PAY_VAULT_ADDRESS) + _pad_uint(
        5 * 10**6
    )
    assert len(chain.sent_raw) == 1  # 本地签名后只发 raw tx
    assert result["amount_raw"] == 5 * 10**6
    assert result["spender"] == PAY_VAULT_ADDRESS
    assert result["status"] == 1
    assert result["tx_hash"].startswith("0x")


@pytest.mark.unit
def test_approve_vault_amount_parsing() -> None:
    chain = FakeChain()
    w = _wallet(chain=chain)
    assert w.approve_vault(Decimal("0.01"))["amount_raw"] == 10**4
    assert w.approve_vault(1)["amount_raw"] == 10**6
    with pytest.raises(WalletError, match="精度"):
        w.approve_vault("0.0000001")  # 7 位小数，超过 MockUSDT 6 位精度
    with pytest.raises(WalletError, match="负数"):
        w.approve_vault("-1")


@pytest.mark.unit
def test_wrong_chain_refuses_before_signing() -> None:
    chain = FakeChain()
    chain.chain_id = 967  # 错链：build_tx 照常返回，钱包必须在签名前拒绝
    w = _wallet(chain=chain)
    with pytest.raises(WalletError, match="968"):
        w.approve_vault("1")
    assert chain.sent_raw == []  # A4：断言先于签名，未发任何交易


@pytest.mark.unit
def test_balance_available_is_min_of_balance_and_allowance() -> None:
    w = _wallet(chain=FakeChain(balance_raw=10_000_000, allowance_raw=3_000_000))
    bal = w.balance()
    assert bal.wallet == ANVIL1_ADDR
    assert bal.usdt_balance_raw == 10_000_000
    assert bal.vault_allowance_raw == 3_000_000
    assert bal.available_raw == 3_000_000
    # 授权高于余额时可用额受余额约束
    w2 = _wallet(chain=FakeChain(balance_raw=2, allowance_raw=999))
    assert w2.balance().available_raw == 2


@pytest.mark.unit
def test_mint_encodes_public_mint_calldata() -> None:
    chain = FakeChain()
    w = _wallet(chain=chain)
    result = w.mint("10")
    tx = chain.built_txs[0]
    assert tx["to"] == TOKEN_ADDRESS
    assert tx["data"] == bytes.fromhex("40c10f19") + _pad_addr(ANVIL1_ADDR) + _pad_uint(10 * 10**6)
    assert result["amount_raw"] == 10 * 10**6
    assert TOKEN_DECIMALS == 6


@pytest.mark.unit
def test_sign_payment_recovers_to_wallet_address() -> None:
    w = _wallet(chain=FakeChain())
    auth = Authorization(
        from_=w.address,
        to=PAY_VAULT_ADDRESS,
        value=10_000,
        valid_after=1,
        valid_before=601,
        nonce=b"\x07" * 32,
    )
    sig = w.sign_payment(auth)
    digest = eip712_digest(auth, PAY_VAULT_ADDRESS, 968)
    assert recover_signer(digest, v=sig.v, r=sig.r, s=sig.s) == w.address


def _pad_uint(n: int) -> bytes:
    return n.to_bytes(32, "big")


def _pad_addr(addr: str) -> bytes:
    return int(addr, 16).to_bytes(32, "big")
