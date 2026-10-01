"""M4 代币：ERC20/721/1155 读写与历史（写接口 dry_run 默认）。

NFT 路径规范化（偏差 #3）：/tokens/erc721/{contract}/owner/{token_id}
与 /tokens/erc721/{contract}/token/{token_id}/uri。
"""

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from web3 import Web3
from web3.exceptions import ContractLogicError, Web3Exception

from app.core.abis import ERC20_ABI, ERC721_ABI
from app.core.deps import ChainDep, ExplorerDep, TxServiceDep, Web3Dep
from app.core.errors import ChainError, TxRevertedError
from app.core.tx import TxPreview, TxReceiptSummary, decimal_from_wei, wei_from_decimal

router = APIRouter(prefix="/tokens", tags=["M4 tokens"])

DEFAULT_PAGE_ITEMS = 50


class KnownToken(BaseModel):
    symbol: str
    address: str
    decimals: int
    kind: str


class TokenList(BaseModel):
    tokens: list[KnownToken]


class TokenInfo(BaseModel):
    address: str
    name: str | None
    symbol: str | None
    decimals: int | None
    total_supply: str
    kind: str


class Erc20TransferRequest(BaseModel):
    token: str
    from_address: str
    to_address: str
    amount: str = Field(description="人类可读金额（按代币 decimals 换算）")
    dry_run: bool = True


class Erc20ApproveRequest(BaseModel):
    token: str
    owner: str
    spender: str
    amount: str
    dry_run: bool = True


class AllowanceRequest(BaseModel):
    token: str
    owner: str
    spender: str


class AllowanceView(BaseModel):
    token: str
    owner: str
    spender: str
    allowance_raw: str
    allowance: str


class OwnerView(BaseModel):
    contract: str
    token_id: int
    owner: str


class TokenUriView(BaseModel):
    contract: str
    token_id: int
    token_uri: str


class ExplorerPage(BaseModel):
    items: list[dict[str, Any]]
    next_page_params: dict[str, Any] | None = None


@router.get("", response_model=TokenList)
def list_tokens(chain: ChainDep) -> TokenList:
    return TokenList(
        tokens=[
            KnownToken(symbol="WBOT", address=chain.contracts.wbot, decimals=18, kind="erc20"),
            KnownToken(symbol="USDT", address=chain.contracts.usdt, decimals=6, kind="erc20"),
        ]
    )


@router.get("/{address}/info", response_model=TokenInfo)
def token_info(w3: Web3Dep, address: str) -> TokenInfo:
    token = Web3.to_checksum_address(address)
    contract = w3.eth.contract(address=token, abi=ERC20_ABI)
    try:
        symbol = contract.functions.symbol().call()
        name = contract.functions.name().call()
        decimals = contract.functions.decimals().call()
        supply = contract.functions.totalSupply().call()
    except ContractLogicError as exc:
        raise TxRevertedError(f"代币元数据读取失败（非 ERC20?）: {exc}") from exc
    except Web3Exception as exc:
        msg = f"RPC 失败: {exc}"
        raise ChainError(msg) from exc
    return TokenInfo(
        address=token,
        name=name,
        symbol=symbol,
        decimals=decimals,
        total_supply=str(supply),
        kind="erc20",
    )


@router.get("/{address}/holders", response_model=ExplorerPage)
def token_holders(
    explorer: ExplorerDep, address: str, items_count: int = Query(default=50, ge=1, le=100)
) -> ExplorerPage:
    raw = explorer.token_holders(Web3.to_checksum_address(address))
    return ExplorerPage(
        items=raw.get("items", [])[:items_count],
        next_page_params=raw.get("next_page_params"),
    )


@router.get("/{address}/transfers", response_model=ExplorerPage)
def token_transfers(
    explorer: ExplorerDep, address: str, items_count: int = Query(default=50, ge=1, le=100)
) -> ExplorerPage:
    raw = explorer.token_transfers(Web3.to_checksum_address(address))
    return ExplorerPage(
        items=raw.get("items", [])[:items_count],
        next_page_params=raw.get("next_page_params"),
    )


@router.post(
    "/erc20/transfer",
    response_model=TxPreview | TxReceiptSummary,
    description="ERC20 转账；dry_run=true 默认仅返回未签名预览",
)
def erc20_transfer(
    request: Erc20TransferRequest,
    w3: Web3Dep,
    tx_service: TxServiceDep,
) -> TxPreview | TxReceiptSummary:
    token = Web3.to_checksum_address(request.token)
    contract = w3.eth.contract(address=token, abi=ERC20_ABI)
    decimals = contract.functions.decimals().call()
    amount_raw = wei_from_decimal(request.amount, decimals)
    data = contract.encode_abi(
        "transfer", args=[Web3.to_checksum_address(request.to_address), amount_raw]
    )
    return tx_service.execute(
        from_address=request.from_address,
        to_address=token,
        value_wei=0,
        data=data,
        dry_run=request.dry_run,
    )


@router.post(
    "/erc20/approve",
    response_model=TxPreview | TxReceiptSummary,
    description="ERC20 授权；dry_run=true 默认仅返回未签名预览",
)
def erc20_approve(
    request: Erc20ApproveRequest,
    w3: Web3Dep,
    tx_service: TxServiceDep,
) -> TxPreview | TxReceiptSummary:
    token = Web3.to_checksum_address(request.token)
    contract = w3.eth.contract(address=token, abi=ERC20_ABI)
    decimals = contract.functions.decimals().call()
    amount_raw = wei_from_decimal(request.amount, decimals)
    data = contract.encode_abi(
        "approve", args=[Web3.to_checksum_address(request.spender), amount_raw]
    )
    return tx_service.execute(
        from_address=request.owner,
        to_address=token,
        value_wei=0,
        data=data,
        dry_run=request.dry_run,
    )


@router.post("/erc20/allowance", response_model=AllowanceView)
def erc20_allowance(request: AllowanceRequest, w3: Web3Dep) -> AllowanceView:
    token = Web3.to_checksum_address(request.token)
    contract = w3.eth.contract(address=token, abi=ERC20_ABI)
    owner = Web3.to_checksum_address(request.owner)
    spender = Web3.to_checksum_address(request.spender)
    try:
        raw = contract.functions.allowance(owner, spender).call()
        decimals = contract.functions.decimals().call()
    except Web3Exception as exc:
        msg = f"allowance 读取失败: {exc}"
        raise ChainError(msg) from exc
    return AllowanceView(
        token=token,
        owner=owner,
        spender=spender,
        allowance_raw=str(raw),
        allowance=decimal_from_wei(raw, decimals),
    )


@router.get("/erc721/{contract}/owner/{token_id}", response_model=OwnerView)
def erc721_owner(w3: Web3Dep, contract: str, token_id: int) -> OwnerView:
    nft = w3.eth.contract(address=Web3.to_checksum_address(contract), abi=ERC721_ABI)
    try:
        owner = nft.functions.ownerOf(token_id).call()
    except ContractLogicError as exc:
        raise TxRevertedError(f"ownerOf revert（tokenId 不存在?）: {exc}") from exc
    return OwnerView(contract=contract, token_id=token_id, owner=owner)


@router.get("/erc721/{contract}/token/{token_id}/uri", response_model=TokenUriView)
def erc721_token_uri(w3: Web3Dep, contract: str, token_id: int) -> TokenUriView:
    nft = w3.eth.contract(address=Web3.to_checksum_address(contract), abi=ERC721_ABI)
    try:
        uri = nft.functions.tokenURI(token_id).call()
    except ContractLogicError as exc:
        raise TxRevertedError(f"tokenURI revert: {exc}") from exc
    return TokenUriView(contract=contract, token_id=token_id, token_uri=uri)
