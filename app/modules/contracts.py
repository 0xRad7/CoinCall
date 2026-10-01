"""M8 通用合约：任意 call/send/deploy/decode（赛题自定义合约入口，写接口 dry_run 默认）。"""

from typing import Any

from eth_abi import decode
from eth_abi.exceptions import DecodingError, InsufficientDataBytes
from eth_utils import function_abi_to_4byte_selector
from fastapi import APIRouter
from pydantic import BaseModel, Field
from web3 import Web3
from web3.exceptions import ContractLogicError, Web3RPCError

from app.core.deps import TxServiceDep, Web3Dep
from app.core.errors import ChainError, ServiceError, TxRevertedError
from app.core.tx import TxPreview, TxReceiptSummary

router = APIRouter(prefix="/contracts", tags=["M8 contracts"])


class CallRequest(BaseModel):
    to: str
    abi: list[dict[str, Any]] = Field(description="函数/事件 ABI 片段数组")
    method: str
    args: list[Any] = Field(default_factory=list)
    block: str = "latest"


class CallView(BaseModel):
    to: str
    method: str
    result: Any


class SendRequest(BaseModel):
    from_address: str
    to: str
    abi: list[dict[str, Any]]
    method: str
    args: list[Any] = Field(default_factory=list)
    value_wei: str = Field(default="0", description="随交易发送的原生币（wei 整数字符串）")
    dry_run: bool = True


class DeployRequest(BaseModel):
    from_address: str
    bytecode: str = Field(description="0x 前缀部署字节码")
    abi: list[dict[str, Any]] | None = None
    args: list[Any] = Field(default_factory=list)
    dry_run: bool = True


class DeployPreview(BaseModel):
    dry_run: bool
    unsigned_tx: dict[str, Any]
    estimated_gas: int
    total_cost_wei: int


class DecodeRequest(BaseModel):
    abi: list[dict[str, Any]]
    calldata: str | None = None
    log: dict[str, Any] | None = Field(default=None, description="{topics: [...], data: '0x…'}")


class DecodeView(BaseModel):
    decoded: Any


@router.post("/call", response_model=CallView)
def contract_call(request: CallRequest, w3: Web3Dep) -> CallView:
    contract = _contract(w3, request.to, request.abi)
    func = _function(contract, request.method)
    try:
        result = func(*request.args).call(block_identifier=request.block)
    except ContractLogicError as exc:
        raise TxRevertedError(f"{request.method} revert: {exc}") from exc
    except Web3RPCError as exc:
        msg = f"{request.method} RPC 失败: {exc}"
        raise ChainError(msg) from exc
    return CallView(to=request.to, method=request.method, result=_plain(result))


@router.post(
    "/send",
    response_model=TxPreview | TxReceiptSummary,
    description="任意写调用；dry_run=true 默认仅返回未签名预览",
)
def contract_send(
    request: SendRequest,
    w3: Web3Dep,
    tx_service: TxServiceDep,
) -> TxPreview | TxReceiptSummary:
    contract = _contract(w3, request.to, request.abi)
    func = _function(contract, request.method)
    built = func(*request.args).build_transaction({"from": request.from_address})
    data = built["data"]
    return tx_service.execute(
        from_address=request.from_address,
        to_address=Web3.to_checksum_address(request.to),
        value_wei=int(request.value_wei),
        data=data,
        dry_run=request.dry_run,
    )


@router.post(
    "/deploy",
    response_model=DeployPreview | TxReceiptSummary,
    description="部署合约；dry_run=true 默认仅返回未签名预览",
)
def contract_deploy(
    request: DeployRequest,
    w3: Web3Dep,
    tx_service: TxServiceDep,
) -> DeployPreview | TxReceiptSummary:
    abi = request.abi or []
    ctor_args = request.args
    if abi and ctor_args:
        contract = w3.eth.contract(abi=abi, bytecode=request.bytecode)
        data = contract.constructor(*ctor_args).build_transaction({"from": request.from_address})[
            "data"
        ]
    else:
        data = request.bytecode if request.bytecode.startswith("0x") else "0x" + request.bytecode
    outcome = tx_service.execute(
        from_address=request.from_address,
        to_address=None,
        value_wei=0,
        data=data,
        dry_run=request.dry_run,
    )
    if isinstance(outcome, TxPreview):
        return DeployPreview(
            dry_run=True,
            unsigned_tx=outcome.unsigned_tx,
            estimated_gas=outcome.estimated_gas,
            total_cost_wei=outcome.total_cost_wei,
        )
    return outcome


@router.post("/decode", response_model=DecodeView)
def contract_decode(request: DecodeRequest, w3: Web3Dep) -> DecodeView:
    """calldata 用纯 eth-utils/eth-abi 解码（不触网）；log 走合约事件模板。"""
    if request.calldata:
        calldata = request.calldata.removeprefix("0x")
        selector = calldata[:8]
        for abi_entry in request.abi:
            if abi_entry.get("type") != "function":
                continue
            name = str(abi_entry.get("name", ""))
            types = [str(i.get("type", "")) for i in abi_entry.get("inputs", [])]
            signature = f"{name}({','.join(types)})"
            if (
                function_abi_to_4byte_selector(
                    {"type": "function", "name": name, "inputs": abi_entry.get("inputs", [])}
                ).hex()
                != selector
            ):
                continue
            try:
                values = decode(types, bytes.fromhex(calldata[8:]))
            except (ValueError, DecodingError, InsufficientDataBytes) as exc:
                msg = f"calldata 解码失败: {exc}"
                raise ServiceError(msg, code="decode_error") from exc
            inputs = abi_entry.get("inputs", [])
            named = {
                str(item.get("name", f"arg{i}")): _plain(v)
                for i, (item, v) in enumerate(zip(inputs, values, strict=False))
            }
            return DecodeView(decoded={"function": signature, "args": named})
        msg = f"calldata 选择器 0x{selector} 与给定 ABI 不匹配"
        raise ServiceError(msg, code="decode_mismatch")
    if request.log:
        contract = _contract(w3, "0x0000000000000000000000000000000000000001", request.abi)
        for abi_entry in request.abi:
            if abi_entry.get("type") != "event":
                continue
            name = str(abi_entry.get("name", ""))
            try:
                event = contract.events[name]().process_log({"address": "0x0", **request.log})
            except (ContractLogicError, ValueError, KeyError, TypeError):
                continue
            args = {k: _plain(v) for k, v in dict(event.args).items()}
            return DecodeView(decoded={"event": name, "args": args})
        msg = "log 与给定事件 ABI 均不匹配"
        raise ServiceError(msg, code="decode_mismatch")
    msg = "calldata 与 log 至少提供一个"
    raise ServiceError(msg, code="empty_input")


def _contract(w3: Web3, address: str, abi: list[dict[str, Any]]) -> Any:  # noqa: ANN401  # web3 Contract 动态类型
    if not abi:
        msg = "abi 不能为空"
        raise ServiceError(msg, code="bad_abi")
    try:
        return w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)
    except (ValueError, TypeError) as exc:
        msg = f"ABI 非法: {exc}"
        raise ServiceError(msg, code="bad_abi") from exc


def _function(contract: Any, method: str) -> Any:  # noqa: ANN401  # web3 ContractFunction 动态类型
    functions = getattr(contract, "functions", None)
    if functions is None or not hasattr(functions, method):
        msg = f"ABI 中不存在方法: {method}"
        raise ServiceError(msg, code="unknown_method")
    return getattr(functions, method)


def _plain(value: Any) -> Any:  # noqa: ANN401  # 解码结果为任意 JSON 结构
    if isinstance(value, bytes):
        return Web3.to_hex(value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value
