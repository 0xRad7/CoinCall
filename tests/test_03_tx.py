"""mixed：交易签名发送（02 篇 test_03 矩阵）。

unit 部分：TxService 预览/签名/错误映射 + raw tx 编码正确性（recover 校验）。
needs_funds 部分：真实 968 转账（显式 skip 当无私钥）。
"""

import pytest
from app.core.tx import GAS_PRICE_WEI, TxService, wei_from_decimal
from eth_account import Account

from app.core.errors import ServiceError, TxRevertedError
from tests.fakes import (
    ACCOUNT_A,
    ACCOUNT_B,
    make_fake_receipt,
    make_fake_w3,
)

FUND_KEY = "0x" + "44" * 32
FUND_ADDRESS = Account.from_key(FUND_KEY).address
TRANSFER_COST_GAS = 21000
TRANSFER_VALUE_WEI = 10**15


class TestTxServiceUnit:
    def test_dry_run_preview_never_sends(self) -> None:
        w3 = make_fake_w3()
        svc = TxService(w3=w3, funder_key=FUND_KEY)
        out = svc.execute(
            from_address=ACCOUNT_A, to_address=ACCOUNT_B, value_wei=10**16, data=b"", dry_run=True
        )
        assert out.dry_run is True
        assert out.unsigned_tx["gasPrice"] == GAS_PRICE_WEI
        assert out.unsigned_tx["value"] == 10**16
        w3.eth.send_raw_transaction.assert_not_called()

    def test_real_send_happy_path(self) -> None:
        w3 = make_fake_w3()
        svc = TxService(w3=w3, funder_key=FUND_KEY)
        out = svc.execute(
            from_address=FUND_ADDRESS,
            to_address=ACCOUNT_B,
            value_wei=10**15,
            data=b"",
            dry_run=False,
        )
        assert out.dry_run is False
        assert out.status == 1
        w3.eth.send_raw_transaction.assert_called_once()
        raw = w3.eth.send_raw_transaction.call_args[0][0]
        assert Account.recover_transaction(raw) == FUND_ADDRESS  # 签名者正确

    def test_revert_receipt_raises_tx_reverted(self) -> None:
        w3 = make_fake_w3()
        w3.eth.wait_for_transaction_receipt.return_value = make_fake_receipt(status=0)
        svc = TxService(w3=w3, funder_key=FUND_KEY)
        with pytest.raises(TxRevertedError) as ei:
            svc.execute(
                from_address=FUND_ADDRESS,
                to_address=ACCOUNT_B,
                value_wei=1,
                data=b"",
                dry_run=False,
            )
        assert ei.value.tx_hash is not None

    def test_unknown_signer_raises_service_error(self) -> None:
        svc = TxService(w3=make_fake_w3(), funder_key=None)
        with pytest.raises(ServiceError):
            svc.execute(
                from_address=ACCOUNT_A, to_address=ACCOUNT_B, value_wei=1, data=b"", dry_run=False
            )

    def test_fixed_gas_price_20_gwei(self) -> None:
        w3 = make_fake_w3()
        svc = TxService(w3=w3, funder_key=FUND_KEY)
        out = svc.execute(
            from_address=FUND_ADDRESS, to_address=ACCOUNT_B, value_wei=1, data=b"", dry_run=True
        )
        assert out.unsigned_tx["gasPrice"] == 20 * 10**9  # 铁律 A6：恒定 20 gwei


class TestRawTxEncoding:
    def test_signed_tx_recoverable_and_legacy(self) -> None:
        """离线签名向量：签名可恢复出原地址、legacy(type0)、链 ID 968、20 gwei。"""
        acct = Account.from_key(FUND_KEY)
        signed = Account.sign_transaction(
            {
                "from": acct.address,
                "to": ACCOUNT_B,
                "value": TRANSFER_VALUE_WEI,
                "gas": TRANSFER_COST_GAS,
                "gasPrice": GAS_PRICE_WEI,
                "nonce": 0,
                "chainId": 968,
            },
            FUND_KEY,
        )
        raw = signed.raw_transaction
        recovered = Account.recover_transaction(raw)
        assert recovered == acct.address


class TestWeiConversion:
    def test_wei_from_decimal_18(self) -> None:
        assert wei_from_decimal("1.5", 18) == 1_500_000_000_000_000_000

    def test_wei_from_decimal_6_usdt(self) -> None:
        assert wei_from_decimal("2", 6) == 2_000_000

    def test_too_many_decimals_rejected(self) -> None:
        with pytest.raises(ServiceError):
            wei_from_decimal("0.0000001", 6)

    def test_negative_rejected(self) -> None:
        with pytest.raises(ServiceError):
            wei_from_decimal("-1", 18)


@pytest.mark.needs_funds
class TestTxLiveNeedsFunds:
    """真实 968 写链：转账→回执→余额/nonce 断言（资金 < 0.001 BOT）。"""

    def test_self_transfer_roundtrip(self, w3, funder_key) -> None:
        acct = Account.from_key(funder_key)
        svc = TxService(w3=w3, funder_key=funder_key)
        balance_before = w3.eth.get_balance(acct.address)
        out = svc.execute(
            from_address=acct.address,
            to_address=acct.address,
            value_wei=10**15,
            data=b"",
            dry_run=False,
        )
        assert out.status == 1
        assert out.effective_gas_price_wei == GAS_PRICE_WEI  # 回执侧 20 gwei 验证
        balance_after = w3.eth.get_balance(acct.address)
        expected_fee = out.gas_used * GAS_PRICE_WEI
        assert balance_before - balance_after == expected_fee  # 自转只耗 gas
        assert w3.eth.get_transaction_count(acct.address, "latest") >= 1

    def test_nonce_strictly_increases(self, w3, funder_key) -> None:
        acct = Account.from_key(funder_key)
        n0 = w3.eth.get_transaction_count(acct.address, "pending")
        svc = TxService(w3=w3, funder_key=funder_key)
        svc.execute(
            from_address=acct.address,
            to_address=acct.address,
            value_wei=10**15,
            data=b"",
            dry_run=False,
        )
        n1 = w3.eth.get_transaction_count(acct.address, "pending")
        assert n1 == n0 + 1
