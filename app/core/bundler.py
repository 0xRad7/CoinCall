"""4337 Bundler 客户端 + UserOperation v0.7 组装/签名/提交。

v0.7 packed 字段：accountGasLimits = verificationGasLimit(左16B) + callGasLimit(右16B)；
gasFees = maxPriorityFeePerGas(左16B) + maxFeePerGas(右16B)。
gas 按链现实恒定 20 gwei（铁律 A6，baseFee=0 下 maxFee=20gwei 足够）。
"""

import time
from typing import Any, cast

from eth_account import Account
from eth_typing import ChecksumAddress
from hexbytes import HexBytes
from httpx import Client
from web3 import Web3
from web3.exceptions import Web3Exception

from app.core.abis.erc4337 import (
    ENTRY_POINT_ABI,
    SIMPLE_ACCOUNT_EXECUTE_ABI,
    SIMPLE_ACCOUNT_FACTORY_ABI,
)
from app.core.chains import ChainSpec
from app.core.errors import ChainError, ServiceError
from app.core.rpc import checksum, contract_at, hex_to_bytes

GAS_PRICE_WEI = 20 * 10**9
DEFAULT_VERIFICATION_GAS = 100_000
DEFAULT_CALL_GAS = 50_000
DEFAULT_PRE_VERIFICATION_GAS = 50_000
RECEIPT_POLL_S = 30


def _pad16(value: int) -> str:
    return Web3.to_hex(value)[2:].rjust(32, "0")


class BundlerClient:
    """Bundler JSON-RPC（eth_sendUserOperation 等）。"""

    def __init__(self, http: Client) -> None:
        self._http = http

    def rpc(self, method: str, params: list[Any]) -> Any:  # noqa: ANN401  # JSON-RPC 任意返回
        try:
            resp = self._http.post(
                "", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            )
        except Web3Exception as exc:
            msg = f"bundler 请求失败: {exc}"
            raise ChainError(msg) from exc
        body = resp.json()
        if "error" in body:
            raise ChainError(f"bundler {method} 错误: {body['error']}", code="bundler_error")
        return body.get("result")

    def supported_entry_points(self) -> list[str]:
        return list(self.rpc("eth_supportedEntryPoints", []))

    def send_user_operation(self, user_op: dict[str, str], entry_point: str) -> str:
        return str(self.rpc("eth_sendUserOperation", [user_op, entry_point]))

    def estimate_user_operation_gas(
        self, user_op: dict[str, str], entry_point: str
    ) -> dict[str, Any]:
        return dict(self.rpc("eth_estimateUserOperationGas", [user_op, entry_point]))

    def get_user_operation_by_hash(self, op_hash: str) -> dict[str, Any] | None:
        result = self.rpc("eth_getUserOperationByHash", [op_hash])
        return dict(result) if result else None

    def get_user_operation_receipt(self, op_hash: str) -> dict[str, Any] | None:
        result = self.rpc("eth_getUserOperationReceipt", [op_hash])
        return dict(result) if result else None


def predict_account_address(w3: Web3, chain: ChainSpec, owner: str, salt: int) -> str:
    factory = contract_at(w3, chain.contracts.simple_account_factory, SIMPLE_ACCOUNT_FACTORY_ABI)
    return factory.functions.getAddress(checksum(owner), salt).call()


def account_has_code(w3: Web3, address: str) -> bool:
    return w3.eth.get_code(cast(ChecksumAddress, checksum(address))) not in (b"", "0x")


def build_user_operation(
    w3: Web3,
    chain: ChainSpec,
    *,
    owner: str,
    salt: int,
    target: str,
    value_wei: int,
    calldata: bytes,
) -> dict[str, str]:
    """组装 v0.7 UserOperation（SimpleAccount.execute 承载内层调用）。"""
    sender = predict_account_address(w3, chain, owner, salt)
    account = contract_at(w3, sender, SIMPLE_ACCOUNT_EXECUTE_ABI)
    exec_data = account.encode_abi("execute", args=[checksum(target), value_wei, calldata])
    if account_has_code(w3, sender):
        init_code: bytes = b""
    else:
        factory = contract_at(
            w3, chain.contracts.simple_account_factory, SIMPLE_ACCOUNT_FACTORY_ABI
        )
        init_code = hex_to_bytes(chain.contracts.simple_account_factory) + hex_to_bytes(
            factory.encode_abi("createAccount", args=[checksum(owner), salt])
        )
    nonce = w3.eth.get_transaction_count(cast(ChecksumAddress, checksum(sender)), "pending") or 0
    return {
        "sender": sender,
        "nonce": Web3.to_hex(nonce),
        "initCode": Web3.to_hex(init_code) if init_code else "0x",
        "callData": Web3.to_hex(HexBytes(exec_data)),
        "accountGasLimits": "0x" + _pad16(DEFAULT_VERIFICATION_GAS) + _pad16(DEFAULT_CALL_GAS),
        "preVerificationGas": Web3.to_hex(DEFAULT_PRE_VERIFICATION_GAS),
        "gasFees": "0x" + _pad16(GAS_PRICE_WEI) + _pad16(GAS_PRICE_WEI),
        "paymasterAndData": "0x",
        "signature": "0x",
    }


def sign_user_operation(
    w3: Web3, chain: ChainSpec, user_op: dict[str, str], owner_key: str
) -> dict[str, str]:
    """EntryPoint.getUserOpHash（eth_call）→ owner ECDSA 签名。"""
    entry_point = contract_at(w3, chain.contracts.entry_point, ENTRY_POINT_ABI)
    op_tuple = (
        user_op["sender"],
        int(user_op["nonce"], 16),
        hex_to_bytes(user_op["initCode"]),
        hex_to_bytes(user_op["callData"]),
        hex_to_bytes(user_op["accountGasLimits"]),
        int(user_op["preVerificationGas"], 16),
        hex_to_bytes(user_op["gasFees"]),
        hex_to_bytes(user_op["paymasterAndData"]),
        hex_to_bytes(user_op["signature"]),
    )
    op_hash = entry_point.functions.getUserOpHash(op_tuple).call()
    signed = Account.sign_message(__hash=op_hash, private_key=owner_key)
    return {**user_op, "signature": Web3.to_hex(signed.signature)}


def build_and_send_user_op(
    *,
    w3: Web3,
    bundler: BundlerClient,
    chain: ChainSpec,
    owner_key: str,
    salt: int,
    target: str,
    value_wei: int,
    calldata: bytes,
    dry_run: bool,
    estimate: bool = False,
) -> dict[str, Any]:
    """一步式：预测地址→组装→(dry_run 返回预览 | 签名→Bundler→等链上回执)。"""
    owner = Account.from_key(owner_key).address
    user_op = build_user_operation(
        w3, chain, owner=owner, salt=salt, target=target, value_wei=value_wei, calldata=calldata
    )
    preview: dict[str, Any] = {
        "sender": user_op["sender"],
        "user_operation": dict(user_op),
        "entry_point": chain.contracts.entry_point,
        "note": "dry_run：未签名未提交",
    }
    if dry_run:
        preview["dry_run"] = True
        return preview
    if not account_has_code(w3, user_op["sender"]) and value_wei > 0:
        msg = "智能账户未建户且本次需转入原生币：请先向预览地址入金并先发一笔建户 UserOp"
        raise ServiceError(msg, code="aa_account_not_funded")
    signed = sign_user_operation(w3, chain, user_op, owner_key)
    op_hash = bundler.send_user_operation(signed, chain.contracts.entry_point)
    if estimate:
        try:
            est = bundler.estimate_user_operation_gas(user_op, chain.contracts.entry_point)
        except ChainError:
            est = {}
    else:
        est = {}
    deadline = time.time() + RECEIPT_POLL_S
    receipt = bundler.get_user_operation_receipt(op_hash)
    while receipt is None and time.time() < deadline:
        time.sleep(1.0)
        receipt = bundler.get_user_operation_receipt(op_hash)
    if receipt is None:
        msg = f"UserOp 未在 {RECEIPT_POLL_S}s 内上链: {op_hash}"
        raise ChainError(msg, code="userop_timeout")
    total_gas = int(receipt.get("actualGasUsed", 0))
    return {
        "dry_run": False,
        "user_op_hash": op_hash,
        "sender": user_op["sender"],
        "success": bool(receipt.get("success")),
        "transaction_hash": receipt.get("receipt", {}).get("transactionHash"),
        "gas_used": total_gas,
        "estimated_gas": int(est.get("callGasLimit", 0)) + int(est.get("verificationGasLimit", 0))
        if est
        else 0,
    }
