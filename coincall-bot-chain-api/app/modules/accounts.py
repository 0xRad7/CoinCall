"""M2 账户：生成 EOA（keystore 代管）/余额合并视图/nonce/交易历史。"""

from typing import Any, cast

from eth_typing import ChecksumAddress
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from web3 import Web3
from web3.exceptions import Web3Exception

from app.core.abis import ERC20_ABI
from app.core.deps import ChainDep, ExplorerDep, KeystoreDep, Web3Dep
from app.core.errors import ChainError, ServiceError
from app.core.keystore import ManagedAccount
from app.core.tx import decimal_from_wei

router = APIRouter(prefix="/accounts", tags=["M2 accounts"])

NATIVE_SYMBOL = "BOT"
NATIVE_DECIMALS = 18
DEFAULT_PAGE_ITEMS = 50


class CreateAccountRequest(BaseModel):
    reveal_private_key: bool = Field(
        default=False, description="true 时响应中包含私钥（仅此一次，请自行保存）"
    )


class TokenBalance(BaseModel):
    address: str
    symbol: str
    decimals: int
    balance_wei: str
    balance: str


class NativeBalance(BaseModel):
    symbol: str
    decimals: int
    balance_wei: str
    balance: str


class BalancesView(BaseModel):
    address: str
    native: NativeBalance
    tokens: list[TokenBalance]


class NonceInfo(BaseModel):
    address: str
    latest: int
    pending: int


class ExplorerPage(BaseModel):
    items: list[dict[str, Any]]
    next_page_params: dict[str, Any] | None = None


@router.post(
    "",
    response_model=ManagedAccount,
    response_model_exclude_none=True,
    description="生成 EOA；私钥仅 reveal=true 时返回一次",
)
def create_account(
    request: CreateAccountRequest,
    keystore: KeystoreDep,
) -> ManagedAccount:
    account = keystore.create()
    if request.reveal_private_key:
        account.private_key = keystore.reveal(account.address)
    return account


@router.get("/{address}/balances", response_model=BalancesView)
def account_balances(w3: Web3Dep, chain: ChainDep, address: str) -> BalancesView:
    addr = _checksum(address)
    try:
        native_wei = w3.eth.get_balance(cast(ChecksumAddress, addr))
        tokens = [
            _token_balance(w3, addr, c, alias)
            for c, alias in (
                (chain.contracts.wbot, "WBOT"),
                (chain.contracts.usdt, "USDT"),
            )
        ]
    except Web3Exception as exc:
        msg = f"读取余额失败: {exc}"
        raise ChainError(msg) from exc
    return BalancesView(
        address=addr,
        native=NativeBalance(
            symbol=NATIVE_SYMBOL,
            decimals=NATIVE_DECIMALS,
            balance_wei=str(native_wei),
            balance=decimal_from_wei(native_wei, NATIVE_DECIMALS),
        ),
        tokens=[t for t in tokens if t is not None],
    )


@router.get("/{address}/nonce", response_model=NonceInfo)
def account_nonce(w3: Web3Dep, address: str) -> NonceInfo:
    addr = _checksum(address)
    return NonceInfo(
        address=addr,
        latest=w3.eth.get_transaction_count(cast(ChecksumAddress, addr), "latest"),
        pending=w3.eth.get_transaction_count(cast(ChecksumAddress, addr), "pending"),
    )


@router.get("/{address}/transactions", response_model=ExplorerPage)
def account_transactions(
    explorer: ExplorerDep,
    address: str,
    items_count: int = Query(default=DEFAULT_PAGE_ITEMS, ge=1, le=100),
) -> ExplorerPage:
    raw = explorer.address_transactions(_checksum(address))
    return ExplorerPage(
        items=raw.get("items", [])[:items_count],
        next_page_params=raw.get("next_page_params"),
    )


def _token_balance(w3: Web3, holder: str, token: str, fallback_symbol: str) -> TokenBalance | None:
    contract = w3.eth.contract(address=cast(ChecksumAddress, token), abi=ERC20_ABI)
    try:
        symbol = contract.functions.symbol().call() or fallback_symbol
        decimals = contract.functions.decimals().call()
        balance = contract.functions.balanceOf(holder).call()
    except Web3Exception:
        return None
    return TokenBalance(
        address=token,
        symbol=symbol,
        decimals=decimals,
        balance_wei=str(balance),
        balance=decimal_from_wei(balance, decimals),
    )


def _checksum(address: str) -> str:
    """地址规范化；非法输入报 422 而非 500。"""
    try:
        return Web3.to_checksum_address(address)
    except ValueError as exc:
        msg = f"非法地址: {address!r}"
        raise ServiceError(msg, code="bad_address") from exc
