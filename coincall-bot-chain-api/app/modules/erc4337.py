"""M5 ERC-4337 账户抽象：config/predict/create✏/build/sign-send✏/hash/estimate/execute✏。"""

from typing import Any

from eth_account import Account
from fastapi import APIRouter
from pydantic import BaseModel, Field
from web3 import Web3

from app.core.abis.erc4337 import SIMPLE_ACCOUNT_FACTORY_ABI
from app.core.bundler import (
    BundlerClient,
    _probe_userop_nonce,
    account_has_code,
    build_and_send_user_op,
    build_user_operation,
    predict_account_address,
    sign_user_operation,
)
from app.core.config import Settings
from app.core.deps import BundlerDep, ChainDep, SettingsDep, TxServiceDep, Web3Dep
from app.core.errors import ServiceError
from app.core.rpc import contract_at, hex_to_bytes
from app.core.tx import TxPreview, TxReceiptSummary, TxService, wei_from_decimal

router = APIRouter(prefix="/aa", tags=["M5 aa"])


class AaConfig(BaseModel):
    entry_point: str
    simple_account_factory: str
    kernel_v033: str
    bundler_url: str
    supported_entry_points: list[str]


class PredictRequest(BaseModel):
    owner: str
    salt: int = 0


class PredictView(BaseModel):
    owner: str
    salt: int
    address: str
    deployed: bool


class CreateAccountRequest(BaseModel):
    owner: str | None = Field(default=None, description="默认取环境变量出资账户")
    salt: int = 0
    fund_bot: str = Field(default="0", description="建户同时从出资账户转入的原生币")
    dry_run: bool = True


class UserOpBuildRequest(BaseModel):
    owner: str | None = None
    salt: int = 0
    target: str
    value_wei: str = "0"
    calldata: str = "0x"


class UserOpSendRequest(UserOpBuildRequest):
    dry_run: bool = True


class UserOpEstimateRequest(UserOpBuildRequest):
    pass


class ExecuteRequest(UserOpBuildRequest):
    dry_run: bool = True


class UserOpHashView(BaseModel):
    user_op_hash: str
    found: bool
    entry_point: str | None = None
    sender: str | None = None
    success: bool | None = None
    transaction_hash: str | None = None
    actual_gas_used: int | None = None


def _resolve_owner(request_owner: str | None, settings: SettingsDep) -> str:
    if request_owner:
        return Web3.to_checksum_address(request_owner)
    if not settings.funded_key:
        msg = "未提供 owner 且环境变量未配置 BOT_CHAIN_TEST_PRIVATE_KEY"
        raise ServiceError(msg, code="no_owner")
    return Account.from_key(settings.funded_key).address


@router.get("/config", response_model=AaConfig)
def aa_config(chain: ChainDep, bundler: BundlerDep) -> AaConfig:
    try:
        supported = BundlerClient(bundler).supported_entry_points()
    except Exception:  # config 探测失败不应炸接口，返回空列表
        supported = []
    return AaConfig(
        entry_point=chain.contracts.entry_point,
        simple_account_factory=chain.contracts.simple_account_factory,
        kernel_v033=chain.contracts.kernel_v033,
        bundler_url=chain.bundler_url,
        supported_entry_points=supported,
    )


@router.post("/account/predict", response_model=PredictView)
def aa_predict(request: PredictRequest, w3: Web3Dep, chain: ChainDep) -> PredictView:
    owner = Web3.to_checksum_address(request.owner)
    address = predict_account_address(w3, chain, owner, request.salt)
    return PredictView(
        owner=owner, salt=request.salt, address=address, deployed=account_has_code(w3, address)
    )


@router.post(
    "/account/create",
    response_model=TxPreview | TxReceiptSummary,
    description="SimpleAccountFactory.createAccount 建户交易；dry_run=true 默认仅预览",
)
def aa_create_account(
    request: CreateAccountRequest,
    w3: Web3Dep,
    chain: ChainDep,
    settings: SettingsDep,
    tx_service: TxServiceDep,
) -> TxPreview | TxReceiptSummary:
    owner = _resolve_owner(request.owner, settings)
    factory = contract_at(w3, chain.contracts.simple_account_factory, SIMPLE_ACCOUNT_FACTORY_ABI)
    data = factory.encode_abi("createAccount", args=[owner, request.salt])
    fund_wei = wei_from_decimal(request.fund_bot, 18)
    return tx_service.execute(
        from_address=owner,
        to_address=chain.contracts.simple_account_factory,
        value_wei=fund_wei,
        data=data,
        dry_run=request.dry_run,
    )


@router.post("/userop/build")
def aa_userop_build(
    request: UserOpBuildRequest, w3: Web3Dep, chain: ChainDep, settings: SettingsDep
) -> dict[str, Any]:
    owner = _resolve_owner(request.owner, settings)
    user_op = build_user_operation(
        w3,
        chain,
        owner=owner,
        salt=request.salt,
        target=Web3.to_checksum_address(request.target),
        value_wei=int(request.value_wei),
        calldata=hex_to_bytes(request.calldata),
    )
    return {"user_operation": user_op, "entry_point": chain.contracts.entry_point}


@router.post(
    "/userop/sign-send",
    description="签名并提交 Bundler；dry_run=true 默认返回待签结构",
)
def aa_userop_sign_send(
    request: UserOpSendRequest,
    w3: Web3Dep,
    chain: ChainDep,
    settings: SettingsDep,
    bundler: BundlerDep,
) -> dict[str, Any]:
    owner = _resolve_owner(request.owner, settings)
    if not settings.funded_key or Account.from_key(settings.funded_key).address != owner:
        msg = f"owner={owner} 无可用签名私钥（仅环境变量出资账户可签名）"
        raise ServiceError(msg, code="no_signer")
    user_op = build_user_operation(
        w3,
        chain,
        owner=owner,
        salt=request.salt,
        target=Web3.to_checksum_address(request.target),
        value_wei=int(request.value_wei),
        calldata=hex_to_bytes(request.calldata),
    )
    if request.dry_run:
        return {"dry_run": True, "unsigned_user_operation": user_op}
    signed = sign_user_operation(w3, chain, user_op, settings.funded_key)
    op_hash = BundlerClient(bundler).send_user_operation(signed, chain.contracts.entry_point)
    return {"dry_run": False, "user_op_hash": op_hash, "sender": user_op["sender"]}


@router.get("/userop/{op_hash}", response_model=UserOpHashView)
def aa_userop_status(op_hash: str, bundler: BundlerDep) -> UserOpHashView:
    receipt = BundlerClient(bundler).get_user_operation_receipt(op_hash)
    if receipt is None:
        return UserOpHashView(user_op_hash=op_hash, found=False)
    return UserOpHashView(
        user_op_hash=op_hash,
        found=True,
        entry_point=receipt.get("entryPoint"),
        sender=receipt.get("sender"),
        success=receipt.get("success"),
        transaction_hash=receipt.get("receipt", {}).get("transactionHash"),
        actual_gas_used=int(receipt.get("actualGasUsed", 0)),
    )


def _owner_key_or_raise(owner: str, settings: Settings, tx_service: TxService) -> str:
    """estimate/探测签名需要 owner 的私钥：keystore 代管账户或 env 出资账户。"""
    del settings  # 签名者统一经 TxService.resolve_signer（keystore/env 双支持）
    return tx_service.resolve_signer(owner).key.hex()


@router.post("/userop/estimate")
def aa_userop_estimate(  # noqa: PLR0917  # FastAPI 依赖注入多参
    request: UserOpEstimateRequest,
    w3: Web3Dep,
    chain: ChainDep,
    settings: SettingsDep,
    bundler: BundlerDep,
    tx_service: TxServiceDep,
) -> dict[str, Any]:
    owner = _resolve_owner(request.owner, settings)
    user_op = build_user_operation(
        w3,
        chain,
        owner=owner,
        salt=request.salt,
        target=Web3.to_checksum_address(request.target),
        value_wei=int(request.value_wei),
        calldata=hex_to_bytes(request.calldata),
    )
    client = BundlerClient(bundler)
    try:
        # 本链 nonce 独立计数（C-19）：先探测正确 nonce 再取估值
        user_op["nonce"] = _probe_userop_nonce(
            w3, client, chain, user_op, _owner_key_or_raise(owner, settings, tx_service)
        )
        est = client.estimate_user_operation_gas(user_op, chain.contracts.entry_point)
    except Exception as exc:  # estimate 失败返回错误详情而非 5xx
        return {"error": str(exc)[:200]}
    return {"estimate": est}


@router.post(
    "/execute",
    description="一步式 build+sign+send+等回执（demo 主力）；dry_run=true 默认仅预览",
)
def aa_execute(  # noqa: PLR0917  # FastAPI 依赖注入多参（与 PLR0913 同理）
    request: ExecuteRequest,
    w3: Web3Dep,
    chain: ChainDep,
    settings: SettingsDep,
    bundler: BundlerDep,
    tx_service: TxServiceDep,
) -> dict[str, Any]:
    owner = _resolve_owner(request.owner, settings)
    signer = tx_service.resolve_signer(owner)  # keystore 代管账户或 env 出资账户
    return build_and_send_user_op(
        w3=w3,
        bundler=BundlerClient(bundler),
        chain=chain,
        owner_key=signer.key.hex(),
        salt=request.salt,
        target=Web3.to_checksum_address(request.target),
        value_wei=int(request.value_wei),
        calldata=hex_to_bytes(request.calldata),
        dry_run=request.dry_run,
        tx_service=tx_service,
    )
