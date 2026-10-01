"""M3 交易：转账（dry_run 默认）/裸交易提交/回执/事件解码。"""

from collections.abc import Mapping
from typing import Any

from eth_typing import HexStr
from fastapi import APIRouter
from pydantic import BaseModel, Field
from web3 import Web3
from web3.exceptions import ContractLogicError, Web3RPCError

from app.core.abis import ERC20_ABI
from app.core.deps import TxServiceDep, Web3Dep
from app.core.errors import ChainError, ServiceError, TxRevertedError
from app.core.tx import TxPreview, TxReceiptSummary, wei_from_decimal

router = APIRouter(prefix="/tx", tags=["M3 tx"])

TX_TYPE_ANCESTOR = "0x"


class TransferRequest(BaseModel):
    from_address: str
    to_address: str
    value_bot: str = Field(description="原生币金额（十进制字符串，如 0.01）")
    data: str | None = Field(default=None, description="可选 calldata（0x…）")
    dry_run: bool = True


class SendRawRequest(BaseModel):
    raw_tx: str = Field(description="已签名 RLP 交易（0x…）")
    wait: bool = True


class TxStatus(BaseModel):
    found: bool
    tx_hash: str
    status: int | None = None
    block_number: int | None = None
    from_address: str | None = None
    to_address: str | None = None
    value_wei: str | None = None
    gas_used: int | None = None
    effective_gas_price_wei: str | None = None


class DecodedEvent(BaseModel):
    address: str
    name: str
    args: dict[str, Any]


class EventsView(BaseModel):
    tx_hash: str
    events: list[DecodedEvent]


@router.post(
    "/transfer",
    response_model=TxPreview | TxReceiptSummary,
    description="原生转账；dry_run=true 默认仅返回未签名预览（铁律 A4）",
)
def transfer(
    request: TransferRequest,
    tx_service: TxServiceDep,
) -> TxPreview | TxReceiptSummary:
    value_wei = wei_from_decimal(request.value_bot, 18)
    return tx_service.execute(
        from_address=request.from_address,
        to_address=Web3.to_checksum_address(request.to_address),
        value_wei=value_wei,
        data=request.data or b"",
        dry_run=request.dry_run,
    )


@router.post("/send-raw", response_model=TxReceiptSummary | dict[str, str])
def send_raw(
    request: SendRawRequest,
    w3: Web3Dep,
) -> TxReceiptSummary | dict[str, str]:
    try:
        tx_hash = w3.eth.send_raw_transaction(HexStr(request.raw_tx))
    except Web3RPCError as exc:
        msg = f"eth_sendRawTransaction 失败: {exc}"
        raise ChainError(msg) from exc
    hex_hash = Web3.to_hex(tx_hash)
    if not request.wait:
        return {"tx_hash": hex_hash}
    try:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
    except Web3RPCError as exc:
        msg = f"等待回执失败: {exc}"
        raise ChainError(msg) from exc
    summary = _summary_from_receipt(receipt, hex_hash)
    if summary.status == 0:
        raise TxRevertedError("交易执行失败(status=0)", tx_hash=hex_hash, code="reverted")
    return summary


@router.get("/{tx_hash}", response_model=TxStatus)
def tx_status(tx_hash: str, w3: Web3Dep) -> TxStatus:
    receipt = w3.eth.get_transaction_receipt(HexStr(tx_hash))
    if receipt is None:
        return TxStatus(found=False, tx_hash=tx_hash)
    summary = _summary_from_receipt(receipt, tx_hash)
    return TxStatus(
        found=True,
        tx_hash=summary.tx_hash,
        status=summary.status,
        block_number=summary.block_number,
        from_address=summary.from_address,
        to_address=summary.to_address,
        value_wei=None,
        gas_used=summary.gas_used,
        effective_gas_price_wei=str(summary.effective_gas_price_wei),
    )


@router.get("/{tx_hash}/events", response_model=EventsView)
def tx_events(tx_hash: str, w3: Web3Dep) -> EventsView:
    receipt = w3.eth.get_transaction_receipt(HexStr(tx_hash))
    if receipt is None:
        msg = f"交易尚未上链: {tx_hash}"
        raise ServiceError(msg, code="tx_not_found")
    events: list[DecodedEvent] = []
    for log in receipt.get("logs", []):
        decoded = _decode_transfer_like(w3, log)
        if decoded is not None:
            events.append(decoded)
    return EventsView(tx_hash=tx_hash, events=events)


def _summary_from_receipt(receipt: Mapping[str, Any], hex_hash: str) -> TxReceiptSummary:
    return TxReceiptSummary(
        dry_run=False,
        tx_hash=hex_hash,
        status=int(receipt.get("status", 0)),
        block_number=int(receipt.get("blockNumber", 0)),
        gas_used=int(receipt.get("gasUsed", 0)),
        effective_gas_price_wei=int(receipt.get("effectiveGasPrice", 0)),
        from_address=receipt.get("from", ""),
        to_address=receipt.get("to"),
        contract_address=receipt.get("contractAddress"),
    )


def _decode_transfer_like(w3: Web3, log: Mapping[str, Any]) -> DecodedEvent | None:
    """用 ERC20/721 Transfer 事件模板尝试解码（topic0 匹配）。"""
    contract = w3.eth.contract(address=log["address"], abi=ERC20_ABI)
    try:
        event = contract.events.Transfer().process_log(log)
    except (ContractLogicError, ValueError, KeyError):
        return None
    args = {k: _plain(v) for k, v in dict(event.args).items()}
    return DecodedEvent(address=log["address"], name=event.event, args=args)


def _plain(value: Any) -> Any:  # noqa: ANN401  # 事件参数为任意 JSON 结构
    if isinstance(value, bytes):
        return Web3.to_hex(value)
    return value
