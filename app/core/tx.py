"""交易构建/签名/发送（铁律 A4/A6：dry_run 默认、恒定 20 gwei、nonce 锁）。

签名者解析顺序：keystore 代管账户 → 环境变量出资私钥（仅测试网）→ ServiceError。
nonce 串行化：D2 为进程内每地址锁；D4 升级 Redis 锁（接口不变）。
"""

import threading
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import HexStr
from pydantic import BaseModel
from web3 import Web3
from web3.exceptions import ContractLogicError, Web3RPCError
from web3.types import TxParams

from app.core.errors import ChainError, ServiceError, TxRevertedError
from app.core.keystore import Keystore

GAS_PRICE_WEI = 20 * 10**9  # 铁律 A6：恒定 20 gwei（baseFee=0，不做动态费用）
LEGACY_TX_TYPE = 0
GAS_MARGIN_RATIO = 1.2
MIN_GAS = 21000
RECEIPT_TIMEOUT_S = 30


def wei_from_decimal(amount: str, decimals: int) -> int:
    """人类可读十进制金额 → 最小单位整数；位数超限/非法输入抛 ServiceError。"""
    try:
        value = Decimal(amount)
    except InvalidOperation as exc:
        msg = f"金额格式非法: {amount!r}"
        raise ServiceError(msg, code="bad_amount") from exc
    if value < 0:
        msg = f"金额不能为负: {amount!r}"
        raise ServiceError(msg, code="bad_amount")
    scaled = value.scaleb(decimals)
    if scaled != scaled.to_integral_value():
        msg = f"金额精度超过 {decimals} 位小数: {amount!r}"
        raise ServiceError(msg, code="bad_amount")
    return int(scaled)


def decimal_from_wei(amount_wei: int, decimals: int) -> str:
    return format(Decimal(amount_wei).scaleb(-decimals), "f")


class TxPreview(BaseModel):
    dry_run: bool = True
    unsigned_tx: dict[str, Any]
    estimated_gas: int
    total_cost_wei: int


class TxReceiptSummary(BaseModel):
    dry_run: bool = False
    tx_hash: str
    status: int
    block_number: int
    gas_used: int
    effective_gas_price_wei: int
    from_address: str
    to_address: str | None
    contract_address: str | None = None


class TxService:
    """构建（dry_run 预览）与签名发送（20 gwei legacy）的统一入口。"""

    def __init__(self, w3: Web3, funder_key: str | None, keystore: Keystore | None = None) -> None:
        self._w3 = w3
        self._funder_key = funder_key
        self._keystore = keystore
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ---- 签名者解析 -------------------------------------------------------
    def resolve_signer(self, from_address: str) -> LocalAccount:
        address = Web3.to_checksum_address(from_address)
        if self._keystore is not None:
            managed = self._keystore.get(address)
            if managed is not None:
                return managed
        if self._funder_key is not None:
            funder = Account.from_key(self._funder_key)
            if funder.address == address:
                return funder
        msg = f"无可用签名者: {address}（仅 keystore 代管地址或环境变量出资账户可签名）"
        raise ServiceError(msg, code="no_signer")

    def _lock_for(self, address: str) -> threading.Lock:
        with self._locks_guard:
            if address not in self._locks:
                self._locks[address] = threading.Lock()
            return self._locks[address]

    # ---- 主入口 -----------------------------------------------------------
    def execute(
        self,
        *,
        from_address: str,
        to_address: str | None,
        value_wei: int,
        data: bytes | str,
        dry_run: bool,
        gas: int | None = None,
    ) -> TxPreview | TxReceiptSummary:
        from_addr = Web3.to_checksum_address(from_address)
        calldata = data if isinstance(data, bytes) else Web3.to_bytes(hexstr=HexStr(data))
        with self._lock_for(from_addr):
            nonce = self._w3.eth.get_transaction_count(from_addr, "pending")
            estimated = gas or self._estimate(from_addr, to_address, value_wei, calldata, dry_run)
            tx: dict[str, Any] = {
                "from": from_addr,
                "to": to_address,
                "value": value_wei,
                "gas": estimated,
                "gasPrice": GAS_PRICE_WEI,
                "nonce": nonce,
                "chainId": self._w3.eth.chain_id,
                "data": calldata,
            }
            if dry_run:
                return TxPreview(
                    dry_run=True,
                    unsigned_tx=self._jsonable_tx(tx),
                    estimated_gas=estimated,
                    total_cost_wei=estimated * GAS_PRICE_WEI + value_wei,
                )
            return self._sign_send_wait(from_addr, tx)

    # ---- 内部 -------------------------------------------------------------
    def _estimate(
        self,
        from_addr: str,
        to_address: str | None,
        value_wei: int,
        data: bytes,
        dry_run: bool = False,
    ) -> int:
        try:
            call_cfg = cast(
                TxParams,
                {"from": from_addr, "to": to_address, "value": value_wei, "data": data},
            )
            estimate = self._w3.eth.estimate_gas(call_cfg)
        except (ContractLogicError, Web3RPCError) as exc:
            if dry_run:
                # C-13：dry_run 预览不依赖账户状态（无余额也会 estimate 失败），回退保守默认
                return int(MIN_GAS * GAS_MARGIN_RATIO)
            if isinstance(exc, ContractLogicError):
                raise TxRevertedError(f"estimateGas revert: {exc}") from exc
            raise ChainError(f"estimateGas RPC 失败: {exc}") from exc
        return max(MIN_GAS, int(estimate * GAS_MARGIN_RATIO))

    def _sign_send_wait(self, from_addr: str, tx: dict[str, Any]) -> TxReceiptSummary:
        signer = self.resolve_signer(from_addr)
        signed = Account.sign_transaction(cast(TxParams, tx), signer.key)
        try:
            tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
        except Web3RPCError as exc:
            raise ChainError(f"eth_sendRawTransaction 失败: {exc}") from exc
        try:
            receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=RECEIPT_TIMEOUT_S)
        except Web3RPCError as exc:
            raise ChainError(f"等待回执失败: {exc}") from exc
        summary = TxReceiptSummary(
            dry_run=False,
            tx_hash=Web3.to_hex(tx_hash),
            status=int(receipt.get("status", 0)),
            block_number=int(receipt.get("blockNumber", 0)),
            gas_used=int(receipt.get("gasUsed", 0)),
            effective_gas_price_wei=int(receipt.get("effectiveGasPrice", GAS_PRICE_WEI)),
            from_address=from_addr,
            to_address=tx.get("to"),
            contract_address=receipt.get("contractAddress"),
        )
        if summary.status == 0:
            raise TxRevertedError(
                "交易上链但执行失败(status=0)",
                tx_hash=summary.tx_hash,
                code="reverted",
            )
        return summary

    def _jsonable_tx(self, tx: dict[str, Any]) -> dict[str, Any]:
        out = dict(tx)
        if isinstance(out.get("data"), bytes):
            out["data"] = Web3.to_hex(out["data"])
        return out
