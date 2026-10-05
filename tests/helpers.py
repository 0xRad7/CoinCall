"""测试助手：授权签名 → chargeWithSigBatch calldata 组装 → 回执事件解析 → revert 选择子断言。"""

import ast
import re
from typing import Any

from eth_account.signers.local import LocalAccount
from eth_tester.exceptions import TransactionFailed
from web3 import Web3
from web3._utils.events import EventLogErrorFlags
from web3.contract import Contract
from web3.exceptions import ContractLogicError

from payvault.chain import call_contract
from payvault.eip712 import Authorization, SignedAuthorization, nonce_from, sign_authorization


def selector_of(signature: str) -> str:
    return Web3.keccak(text=signature)[:4].hex()


def default_auth(
    vault: Contract,
    consumer: str,
    *,
    value: int,
    nonce: str | bytes,  # bytes32（EIP-3009 正典）：0x-hex 32 字节或 bytes
    to: str | None = None,
    valid_after: int = 0,
    valid_before: int = 4102444800,  # 2100-01-01 UTC
) -> Authorization:
    """以 vault 地址为 to、全时间窗构造 Authorization（各字段可覆盖）。"""
    return Authorization(
        from_addr=consumer,
        to=to or vault.address,
        value=value,
        valid_after=valid_after,
        valid_before=valid_before,
        nonce=nonce_from(nonce),
    )


def sign_for_vault(
    account: LocalAccount, vault: Contract, chain_id: int, auth: Authorization
) -> SignedAuthorization:
    return sign_authorization(account, auth, chain_id, vault.address)


def charge_call(signed: SignedAuthorization, provider: str) -> dict[str, Any]:
    """组装 chargeWithSigBatch 单元素 calldata 的 tuple。"""
    return {
        "provider": Web3.to_checksum_address(provider),
        "auth": signed.auth.as_typed_data(),
        "v": signed.v,
        "r": signed.r,
        "s": signed.s,
    }


def submit_charge(
    w3: Web3, operator: LocalAccount, vault: Contract, calls: list[dict[str, Any]]
) -> Any:
    """operator 签名提交一批 charge，返回回执。"""
    summary = call_contract(w3, operator, vault.functions.chargeWithSigBatch(calls))
    return w3.eth.get_transaction_receipt(summary["tx_hash"])


def events_of(vault: Contract, receipt: Any, name: str) -> list[dict[str, Any]]:
    """解码回执中指定事件名的事件列表（按 arg 名取值）。

    只喂 vault 自身地址的日志，避免其他合约事件触发 MismatchedABI 噪音；
    解码错误静默丢弃（discarded 日志必然不是本合约事件）。
    """
    event = getattr(vault.events, name)()
    only_vault = dict(receipt)
    only_vault["logs"] = [log for log in receipt["logs"] if log["address"] == vault.address]
    decoded = event.process_receipt(only_vault, errors=EventLogErrorFlags.Discard)
    return [
        {
            key: (Web3.to_hex(value) if isinstance(value, bytes) else value)
            for key, value in ev["args"].items()
        }
        for ev in decoded
        if not ev.get("removed")
    ]


def failed_reasons(vault: Contract, receipt: Any) -> list[str]:
    return [str(ev["reason"]) for ev in events_of(vault, receipt, "ChargeFailed")]


def expect_revert_selector(contract_function: Any, selector: str, *, call_from: str) -> None:
    """eth_call 断言回滚且 revert 数据以指定 4 字节选择子开头。

    live 链抛 web3 ContractLogicError（.data 为 hexstr）；eth-tester 抛
    TransactionFailed（args[0] 为裸选择子字节）——两条路径都归一到 hex 前缀比对。
    """
    try:
        contract_function.call({"from": Web3.to_checksum_address(call_from)})
    except ContractLogicError as exc:
        data = str(exc.data if exc.data else "")
        assert data.startswith(f"0x{selector}"), f"期望 {selector}，实际 revert 数据 {data}"
        return
    except TransactionFailed as exc:
        payload = exc.args[0] if exc.args else b""
        if isinstance(payload, bytes):
            data = "0x" + payload.hex()
        else:
            # web3 包装形态 "execution reverted: b'\xa6{...'"：还原 bytes 字面量
            match = re.search(r"b'(.*)'", str(payload), re.DOTALL)
            if match:
                try:
                    data = "0x" + ast.literal_eval("b'" + match.group(1) + "'").hex()
                except (ValueError, SyntaxError):
                    data = str(payload)
            else:
                data = str(payload)
        assert data.startswith(f"0x{selector}"), f"期望 {selector}，实际 revert 数据 {data}"
        return
    raise AssertionError("期望 revert 但调用成功")
