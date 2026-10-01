"""4337 Bundler 客户端 + UserOperation v0.7 组装/签名/提交。

v0.7 packed 字段：accountGasLimits = verificationGasLimit(左16B) + callGasLimit(右16B)；
gasFees = maxPriorityFeePerGas(左16B) + maxFeePerGas(右16B)。
gas 按链现实恒定 20 gwei（铁律 A6，baseFee=0 下 maxFee=20gwei 足够）。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast

from eth_account import Account
from eth_keys.datatypes import PrivateKey
from eth_typing import ChecksumAddress, HexStr
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

if TYPE_CHECKING:
    from app.core.tx import TxService
from app.core.errors import ChainError, ServiceError
from app.core.rpc import checksum, contract_at, hex_to_bytes

GAS_PRICE_WEI = 20 * 10**9
AA_MAX_FEE_WEI = 30 * 10**9  # UserOp 报 1.5×：bundler 打包激励（结算按 effectiveGasPrice）
DEFAULT_VERIFICATION_GAS = (
    500_000  # 含 initCode 建户（SimpleAccount 代理部署约 300-400k，AA20 教训）
)
DEFAULT_CALL_GAS = 100_000
DEFAULT_PRE_VERIFICATION_GAS = 50_000
RECEIPT_POLL_S = 30


def _pad16(value: int) -> str:
    return Web3.to_hex(value)[2:].rjust(32, "0")


def _pack_pair(left: int, right: int) -> bytes:
    """v0.7 packed bytes32：左 16B ‖ 右 16B（仅 getUserOpHash 使用）。"""
    return left.to_bytes(16, "big") + right.to_bytes(16, "big")


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
    # bundler.bohr.life 实测要求离散字段形态（C-16）；packed bytes32 仅用于 getUserOpHash
    return {
        "sender": sender,
        "nonce": str(nonce),
        "initCode": Web3.to_hex(init_code) if init_code else "0x",
        "callData": Web3.to_hex(HexBytes(exec_data)),
        "verificationGasLimit": str(DEFAULT_VERIFICATION_GAS),
        "callGasLimit": str(DEFAULT_CALL_GAS),
        "preVerificationGas": str(DEFAULT_PRE_VERIFICATION_GAS),
        "maxFeePerGas": str(AA_MAX_FEE_WEI),
        "maxPriorityFeePerGas": str(AA_MAX_FEE_WEI),
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
        int(user_op["nonce"]),
        hex_to_bytes(user_op["initCode"]),
        hex_to_bytes(user_op["callData"]),
        _pack_pair(int(user_op["verificationGasLimit"]), int(user_op["callGasLimit"])),
        int(user_op["preVerificationGas"]),
        _pack_pair(int(user_op["maxPriorityFeePerGas"]), int(user_op["maxFeePerGas"])),
        hex_to_bytes(user_op["paymasterAndData"]),
        hex_to_bytes(user_op["signature"]),
    )
    op_hash = entry_point.functions.getUserOpHash(op_tuple).call()
    # SimpleAccount 校验的是对 userOpHash 的裸 ECDSA（recover 直接对 32B hash），非 EIP-191
    private_key = PrivateKey(Web3.to_bytes(hexstr=HexStr(owner_key)))
    sig = private_key.sign_msg_hash(bytes(op_hash))
    # eth_keys 的 v 是 0/1 形态；SimpleAccount 用 OZ ECDSA.recover，要求 v∈{27,28}（AA23 教训）
    signature = sig.r.to_bytes(32, "big") + sig.s.to_bytes(32, "big") + bytes([sig.v + 27])
    return {**user_op, "signature": Web3.to_hex(HexBytes(signature))}


DEPOSIT_TOPUP_WEI = 10**17  # 预补 0.1 BOT 保证金（SimpleAccount 经 EntryPoint 付 gas）


def ensure_entry_point_balance(
    *,
    w3: Web3,
    chain: ChainSpec,
    sender: str,
    signer_key: str,
    tx_service: TxService | None = None,
) -> int:
    """智能账户在 EntryPoint 的保证金不足时，由签名者补一笔 depositFor。"""
    ep = contract_at(w3, chain.contracts.entry_point, ENTRY_POINT_ABI)
    current = int(ep.functions.balanceOf(checksum(sender)).call())
    if current >= DEPOSIT_TOPUP_WEI // 2 or tx_service is None:
        return current
    owner = Account.from_key(signer_key).address
    # depositFor 在本链实测 revert（C-18）；SimpleAccount 的 receive() 自动 addDeposit，
    # 直接向智能账户普通转账即完成 EntryPoint 入金
    tx_service.execute(
        from_address=owner,
        to_address=checksum(sender),
        value_wei=DEPOSIT_TOPUP_WEI,
        data=b"",
        dry_run=False,
    )
    return int(ep.functions.balanceOf(checksum(sender)).call())


NONCE_PROBE_MAX = 8


def _probe_userop_nonce(
    w3: Web3,
    bundler: BundlerClient,
    chain: ChainSpec,
    user_op: dict[str, str],
    owner_key: str,
) -> str:
    """本链 EntryPoint 无 getNonce 视图且 nonce 独立于 tx-nonce（C-19）；
    用 estimate 扫 0..N 取第一个不报 AA25 的值（仅 RPC 模拟，不花链上 gas）。"""
    for n in range(NONCE_PROBE_MAX):
        trial = {**user_op, "nonce": str(n)}
        signed = sign_user_operation(w3, chain, trial, owner_key)
        try:
            bundler.estimate_user_operation_gas(signed, chain.contracts.entry_point)
            return str(n)
        except ChainError as exc:
            if "AA25" not in str(exc):
                raise
    msg = f"nonce 探测 0..{NONCE_PROBE_MAX - 1} 全部 AA25: {user_op['sender']}"
    raise ChainError(msg, code="nonce_probe_failed")


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
    tx_service: TxService | None = None,
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
    if not account_has_code(w3, user_op["sender"]):
        if value_wei > 0:
            msg = "智能账户未建户且本次需转入原生币：请先向预览地址入金并先发一笔建户 UserOp"
            raise ServiceError(msg, code="aa_account_not_funded")
        if tx_service is not None:
            # C-17：bundler.bohr.life 对 initCode 模拟报 AA20（EOA 直调 factory 正常），
            # 规避为两步式——先普通交易建户，再发无 initCode 的 UserOp
            factory = contract_at(
                w3, chain.contracts.simple_account_factory, SIMPLE_ACCOUNT_FACTORY_ABI
            )
            data = Web3.to_bytes(
                hexstr=HexStr(factory.encode_abi("createAccount", args=[checksum(owner), salt]))
            )
            tx_service.execute(
                from_address=owner,
                to_address=chain.contracts.simple_account_factory,
                value_wei=0,
                data=data,
                dry_run=False,
            )
            user_op["initCode"] = "0x"
            user_op["nonce"] = "0"  # 本链 EntryPoint nonce 独立计数（见 _probe_userop_nonce）
    ensure_entry_point_balance(
        w3=w3,
        chain=chain,
        sender=user_op["sender"],
        signer_key=owner_key,
        tx_service=tx_service,
    )
    user_op["nonce"] = _probe_userop_nonce(w3, bundler, chain, user_op, owner_key)
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
