"""PayVault 行为测试（eth-tester 本地 EVM，对应 TEST 索引 T4~T7 及补充分支）。

- T4  operator 无法提取（I1：无把合约资金转给非 provider 的路径）
- T5  nonce 重放拒绝（I2）
- T6  批量部分失败跳过 + 事件
- T7  不变量 I4 + 提现恒开（P8）
"""

import pytest
from web3 import Web3

from payvault.chain import call_contract
from payvault.eip712 import domain_separator
from tests.conftest import MINT_AMOUNT, vault_balance, vault_state
from tests.helpers import (
    charge_call,
    default_auth,
    events_of,
    expect_revert_selector,
    failed_reasons,
    selector_of,
    sign_for_vault,
    submit_charge,
)

ONE_USDT = 10**6


def nonce_hex(n: int) -> str:
    """int → 0x 前缀 32 字节 hex（bytes32 nonce 口径）。"""
    return f"0x{n:064x}"


def charged_total(vault, receipt) -> int:
    return sum(int(ev["value"]) for ev in events_of(vault, receipt, "Charged"))


# ---- 基础：域分隔符与权限 ---------------------------------------------------


def test_domain_separator_matches_python(vault_env) -> None:
    """合约构造的 DOMAIN_SEPARATOR 与 Python 参考实现逐字节一致（跨仓接线基础）。"""
    expected = domain_separator(vault_env.chain_id, vault_env.vault.address)
    assert vault_env.vault.functions.DOMAIN_SEPARATOR().call() == expected


def test_only_operator_can_charge(vault_env) -> None:
    """非 operator 调 chargeWithSigBatch 直接 revert（NotOperator）。"""
    vault = vault_env.vault
    auth = default_auth(vault, vault_env.consumer.address, value=ONE_USDT, nonce=nonce_hex(101))
    signed = sign_for_vault(vault_env.consumer, vault, vault_env.chain_id, auth)
    expect_revert_selector(
        vault.functions.chargeWithSigBatch([charge_call(signed, vault_env.provider.address)]),
        selector_of("NotOperator()"),
        call_from=vault_env.attacker.address,
    )


def test_max_batch_enforced(vault_env) -> None:
    """单笔交易硬上限 50：51 笔 revert BatchTooLarge。"""
    vault = vault_env.vault
    auth = default_auth(vault, vault_env.consumer.address, value=ONE_USDT, nonce=nonce_hex(102))
    signed = sign_for_vault(vault_env.consumer, vault, vault_env.chain_id, auth)
    calls = [charge_call(signed, vault_env.provider.address)] * 51
    expect_revert_selector(
        vault.functions.chargeWithSigBatch(calls),
        selector_of("BatchTooLarge(uint256)"),
        call_from=vault_env.operator.address,
    )


# ---- 正常路径 ---------------------------------------------------------------


def test_single_charge_success(vault_env) -> None:
    """approve→单笔 charge：credits 增加、代币进合约、Charged 事件四元组正确。"""
    vault, mock = vault_env.vault, vault_env.mock_usdt
    auth = default_auth(vault, vault_env.consumer.address, value=3 * ONE_USDT, nonce=nonce_hex(1))
    signed = sign_for_vault(vault_env.consumer, vault, vault_env.chain_id, auth)
    receipt = submit_charge(
        vault_env.w3, vault_env.operator, vault, [charge_call(signed, vault_env.provider.address)]
    )

    assert charged_total(vault, receipt) == 3 * ONE_USDT
    assert vault.functions.credits(vault_env.provider.address).call() == 3 * ONE_USDT
    assert mock.functions.balanceOf(vault.address).call() == 3 * ONE_USDT
    assert vault.functions.usedNonces(nonce_hex(1)).call() is True
    charged = events_of(vault, receipt, "Charged")
    assert len(charged) == 1
    assert charged[0] == {
        "provider": vault_env.provider.address,
        "from": vault_env.consumer.address,
        "value": 3 * ONE_USDT,
        "nonce": nonce_hex(1),
    }
    balance, total, diff = vault_state(mock, vault)
    assert (balance, total, diff) == (3 * ONE_USDT, 3 * ONE_USDT, 0)


# ---- T4：operator 无法提取（I1） --------------------------------------------


def test_t4_operator_cannot_withdraw_others_credits(vault_env) -> None:
    """合约有真实资金后，operator（0 credits）providerWithdraw 必须 revert。"""
    vault, mock = vault_env.vault, vault_env.mock_usdt
    auth = default_auth(vault, vault_env.consumer.address, value=5 * ONE_USDT, nonce=nonce_hex(2))
    signed = sign_for_vault(vault_env.consumer, vault, vault_env.chain_id, auth)
    submit_charge(
        vault_env.w3, vault_env.operator, vault, [charge_call(signed, vault_env.provider.address)]
    )
    assert vault_balance(mock, vault) == 5 * ONE_USDT  # 合约确实持有资金

    expect_revert_selector(
        vault.functions.providerWithdraw(vault_env.operator.address, ONE_USDT),
        selector_of("InsufficientCredits(address,uint256,uint256)"),
        call_from=vault_env.operator.address,
    )
    # 资金原封不动（I1：operator 无任何提钱路径）
    assert vault_balance(mock, vault) == 5 * ONE_USDT


def test_t4_operator_self_signed_charge_cannot_move_consumer_funds(vault_env) -> None:
    """operator 自签（无消费者签名）提交 charge：必须 bad_signature 且资金零变动。"""
    vault, mock = vault_env.vault, vault_env.mock_usdt
    # operator 用自己的钥匙签一个 from=consumer 的授权 → 验签必败
    auth = default_auth(vault, vault_env.consumer.address, value=9 * ONE_USDT, nonce=nonce_hex(3))
    forged = sign_for_vault(vault_env.operator, vault, vault_env.chain_id, auth)
    receipt = submit_charge(
        vault_env.w3, vault_env.operator, vault, [charge_call(forged, vault_env.operator.address)]
    )

    assert failed_reasons(vault, receipt) == ["bad_signature"]
    assert vault.functions.credits(vault_env.operator.address).call() == 0
    assert vault_balance(mock, vault) == 0
    assert vault.functions.totalCredits().call() == 0


# ---- T5：nonce 重放拒绝（I2） ------------------------------------------------


def test_t5_nonce_replay_rejected(vault_env) -> None:
    """同 nonce 二次提交：第二笔 ChargeFailed(nonce_used)，记账不增。"""
    vault, mock = vault_env.vault, vault_env.mock_usdt
    auth = default_auth(vault, vault_env.consumer.address, value=2 * ONE_USDT, nonce=nonce_hex(42))
    signed = sign_for_vault(vault_env.consumer, vault, vault_env.chain_id, auth)
    call = charge_call(signed, vault_env.provider.address)

    first = submit_charge(vault_env.w3, vault_env.operator, vault, [call])
    assert charged_total(vault, first) == 2 * ONE_USDT

    replay = submit_charge(vault_env.w3, vault_env.operator, vault, [call])
    assert failed_reasons(vault, replay) == ["nonce_used"]
    assert vault.functions.credits(vault_env.provider.address).call() == 2 * ONE_USDT
    assert vault_balance(mock, vault) == 2 * ONE_USDT


def test_duplicate_nonce_within_same_batch(vault_env) -> None:
    """同批内重复 nonce：第一笔 Charged，第二笔 nonce_used。"""
    vault = vault_env.vault
    auth = default_auth(vault, vault_env.consumer.address, value=ONE_USDT, nonce=nonce_hex(77))
    signed = sign_for_vault(vault_env.consumer, vault, vault_env.chain_id, auth)
    call = charge_call(signed, vault_env.provider.address)
    receipt = submit_charge(vault_env.w3, vault_env.operator, vault, [call, call])
    assert charged_total(vault, receipt) == ONE_USDT
    assert failed_reasons(vault, receipt) == ["nonce_used"]


# ---- T6：批量部分失败跳过 + 事件 ---------------------------------------------


def test_t6_batch_partial_failure_skips_and_emits(vault_env) -> None:
    """批内 [好笔, 坏签名, 余额不足]：整批不 revert，仅好笔入账，两笔 ChargeFailed。"""
    vault, mock = vault_env.vault, vault_env.mock_usdt
    good = sign_for_vault(
        vault_env.consumer,
        vault,
        vault_env.chain_id,
        default_auth(vault, vault_env.consumer.address, value=ONE_USDT, nonce=nonce_hex(201)),
    )
    # 坏签名：provider 钥匙签 from=consumer 的授权
    bad_sig = sign_for_vault(
        vault_env.attacker,
        vault,
        vault_env.chain_id,
        default_auth(vault, vault_env.consumer.address, value=ONE_USDT, nonce=nonce_hex(202)),
    )
    # 余额不足： outsider 签自己的授权但没有 MockUSDT 余额（也未 approve）
    broke = sign_for_vault(
        vault_env.outsider,
        vault,
        vault_env.chain_id,
        default_auth(vault, vault_env.outsider.address, value=ONE_USDT, nonce=nonce_hex(203)),
    )

    receipt = submit_charge(
        vault_env.w3,
        vault_env.operator,
        vault,
        [
            charge_call(good, vault_env.provider.address),
            charge_call(bad_sig, vault_env.provider.address),
            charge_call(broke, vault_env.provider.address),
        ],
    )
    assert charged_total(vault, receipt) == ONE_USDT
    assert failed_reasons(vault, receipt) == ["bad_signature", "transfer_failed"]
    assert vault.functions.credits(vault_env.provider.address).call() == ONE_USDT
    balance, total, diff = vault_state(mock, vault)
    assert (balance, total, diff) == (ONE_USDT, ONE_USDT, 0)
    # 消费者余额只被扣好笔
    assert mock.functions.balanceOf(vault_env.consumer.address).call() == MINT_AMOUNT - ONE_USDT


def test_expired_window_skipped(vault_env, now: int) -> None:
    """过期授权 = 从未扣款：validBefore 已过 → not_in_window。"""
    vault = vault_env.vault
    expired = sign_for_vault(
        vault_env.consumer,
        vault,
        vault_env.chain_id,
        default_auth(
            vault,
            vault_env.consumer.address,
            value=ONE_USDT,
            nonce=nonce_hex(210),
            valid_after=0,
            valid_before=now - 1,
        ),
    )
    future = sign_for_vault(
        vault_env.consumer,
        vault,
        vault_env.chain_id,
        default_auth(
            vault,
            vault_env.consumer.address,
            value=ONE_USDT,
            nonce=nonce_hex(211),
            valid_after=now + 3600,
        ),
    )
    receipt = submit_charge(
        vault_env.w3,
        vault_env.operator,
        vault,
        [
            charge_call(expired, vault_env.provider.address),
            charge_call(future, vault_env.provider.address),
        ],
    )
    assert failed_reasons(vault, receipt) == ["not_in_window", "not_in_window"]
    assert vault_state(vault_env.mock_usdt, vault)[2] == 0


def test_auth_to_must_be_vault(vault_env) -> None:
    """授权 to 非本合约 → auth_to_mismatch（签名虽有效也不执行）。"""
    vault = vault_env.vault
    hijack = sign_for_vault(
        vault_env.consumer,
        vault,
        vault_env.chain_id,
        default_auth(
            vault,
            vault_env.consumer.address,
            value=ONE_USDT,
            nonce=nonce_hex(220),
            to=vault_env.attacker.address,
        ),
    )
    receipt = submit_charge(
        vault_env.w3, vault_env.operator, vault, [charge_call(hijack, vault_env.provider.address)]
    )
    assert failed_reasons(vault, receipt) == ["auth_to_mismatch"]
    assert vault_state(vault_env.mock_usdt, vault)[2] == 0


# ---- T7：不变量 I4 + 提现恒开（P8） ------------------------------------------


def test_t7_withdraw_and_i4_invariant(vault_env) -> None:
    """混合操作全程断言 I4：余额==Σ未提现 credits；提现到账、可部分、可清零。"""

    vault, mock = vault_env.vault, vault_env.mock_usdt
    provider, consumer = vault_env.provider, vault_env.consumer

    a1 = sign_for_vault(
        consumer,
        vault,
        vault_env.chain_id,
        default_auth(vault, consumer.address, value=7 * ONE_USDT, nonce=nonce_hex(301)),
    )
    a2 = sign_for_vault(
        consumer,
        vault,
        vault_env.chain_id,
        default_auth(vault, consumer.address, value=3 * ONE_USDT, nonce=nonce_hex(302)),
    )
    receipt = submit_charge(
        vault_env.w3,
        vault_env.operator,
        vault,
        [charge_call(a1, provider.address), charge_call(a2, vault_env.operator.address)],
    )
    assert charged_total(vault, receipt) == 10 * ONE_USDT
    assert vault_state(mock, vault) == (10 * ONE_USDT, 10 * ONE_USDT, 0)

    # provider 部分提现到第三方地址
    summary = call_contract(
        vault_env.w3,
        provider,
        vault.functions.providerWithdraw(vault_env.outsider.address, 4 * ONE_USDT),
    )
    w_receipt = vault_env.w3.eth.get_transaction_receipt(summary["tx_hash"])
    withdrawn = events_of(vault, w_receipt, "Withdrawn")
    assert withdrawn == [
        {"provider": provider.address, "to": vault_env.outsider.address, "amount": 4 * ONE_USDT}
    ]
    assert mock.functions.balanceOf(vault_env.outsider.address).call() == 4 * ONE_USDT
    assert vault.functions.credits(provider.address).call() == 3 * ONE_USDT
    assert vault_state(mock, vault) == (6 * ONE_USDT, 6 * ONE_USDT, 0)

    # 提现清零
    call_contract(
        vault_env.w3, provider, vault.functions.providerWithdraw(provider.address, 3 * ONE_USDT)
    )
    assert vault.functions.credits(provider.address).call() == 0
    assert mock.functions.balanceOf(provider.address).call() == 3 * ONE_USDT
    assert vault_state(mock, vault) == (3 * ONE_USDT, 3 * ONE_USDT, 0)

    # 超额提现拒绝
    expect_revert_selector(
        vault.functions.providerWithdraw(provider.address, 1),
        selector_of("InsufficientCredits(address,uint256,uint256)"),
        call_from=provider.address,
    )
    # 零地址收款拒绝
    expect_revert_selector(
        vault.functions.providerWithdraw(Web3.to_checksum_address("0x" + "00" * 20), 1),
        selector_of("BadWithdrawTarget()"),
        call_from=provider.address,
    )


def test_withdraw_zero_amount_with_zero_credits_allowed(vault_env) -> None:
    """0 credits + amount=0 的提现不 revert（无任何锁；等价于空操作）。"""

    vault = vault_env.vault
    call_contract(
        vault_env.w3,
        vault_env.outsider,
        vault.functions.providerWithdraw(vault_env.outsider.address, 0),
    )
    assert vault.functions.credits(vault_env.outsider.address).call() == 0


def test_failed_charges_do_not_break_i4(vault_env) -> None:
    """只失败不成功的批：合约余额与 totalCredits 保持零（I4 恒等不被坏账破坏）。"""
    vault = vault_env.vault
    forged = sign_for_vault(
        vault_env.attacker,
        vault,
        vault_env.chain_id,
        default_auth(vault, vault_env.consumer.address, value=50 * ONE_USDT, nonce=nonce_hex(401)),
    )
    receipt = submit_charge(
        vault_env.w3, vault_env.operator, vault, [charge_call(forged, vault_env.provider.address)]
    )
    assert failed_reasons(vault, receipt) == ["bad_signature"]
    assert vault_state(vault_env.mock_usdt, vault) == (0, 0, 0)


# ---- operator 轮换 ----------------------------------------------------------


def test_operator_update(vault_env) -> None:
    """operatorUpdate 单步轮换：旧 operator 失权，新 operator 可结算，事件正确。"""

    vault = vault_env.vault
    call_contract(
        vault_env.w3,
        vault_env.operator,
        vault.functions.operatorUpdate(vault_env.attacker.address),
    )
    assert vault.functions.operator().call() == vault_env.attacker.address

    auth = default_auth(vault, vault_env.consumer.address, value=ONE_USDT, nonce=nonce_hex(501))
    signed = sign_for_vault(vault_env.consumer, vault, vault_env.chain_id, auth)
    expect_revert_selector(
        vault.functions.chargeWithSigBatch([charge_call(signed, vault_env.provider.address)]),
        selector_of("NotOperator()"),
        call_from=vault_env.operator.address,
    )
    receipt = submit_charge(
        vault_env.w3, vault_env.attacker, vault, [charge_call(signed, vault_env.provider.address)]
    )
    assert charged_total(vault, receipt) == ONE_USDT


def test_operator_update_only_operator(vault_env) -> None:
    expect_revert_selector(
        vault_env.vault.functions.operatorUpdate(vault_env.attacker.address),
        selector_of("NotOperator()"),
        call_from=vault_env.attacker.address,
    )


# ---- P8 结构证明：ABI 面不允许任何管理锁 --------------------------------------


def test_p8_abi_surface_has_no_admin_locks(artifacts) -> None:
    """ABI 函数集合与冻结清单完全一致：不存在 pause/owner/timelock 类入口。"""
    expected = {
        "MAX_BATCH",
        "DOMAIN_SEPARATOR",
        "operator",
        "operatorUpdate",
        "token",
        "totalCredits",
        "credits",
        "usedNonces",
        "chargeWithSigBatch",
        "providerWithdraw",
    }
    functions = {
        entry["name"] for entry in artifacts["PayVault"]["abi"] if entry.get("type") == "function"
    }
    assert functions == expected
    forbidden = (
        "pause",
        "unpause",
        "owner",
        "admin",
        "renounce",
        "transferOwnership",
        "setCredits",
    )
    assert all(not any(word in fn.lower() for word in forbidden) for fn in functions)


if __name__ == "__main__":
    pytest.main([__file__])
