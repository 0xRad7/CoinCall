"""M7 BDEX：config/pairs/quote/swap build/swap execute✏/pool volume（getLogs 版）。"""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from web3 import Web3
from web3.exceptions import Web3Exception

from app.core.abis.bdex import V2_FACTORY_ABI, V2_PAIR_ABI, V2_ROUTER_ABI
from app.core.abis.erc20 import ERC20_ABI
from app.core.chains import ChainSpec
from app.core.deps import ChainDep, TxServiceDep, Web3Dep
from app.core.errors import ChainError, ServiceError, TxRevertedError
from app.core.rpc import contract_at, hex_to_bytes
from app.core.tx import TxPreview, TxReceiptSummary, wei_from_decimal

router = APIRouter(prefix="/bdex", tags=["M7 bdex"])

VOLUME_WINDOW_BLOCKS = 5000
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
SWAP_DEADLINE_S = 600


class BdexConfig(BaseModel):
    v2_factory: str
    v2_router: str
    v3_factory: str
    v3_swap_router: str
    known_pairs: list[dict[str, str]]


class PairView(BaseModel):
    pair: str
    token0: str
    token1: str
    reserve0: str
    reserve1: str


class QuoteRequest(BaseModel):
    token_in: str
    token_out: str
    amount_in: str = Field(description="按 token_in decimals 的人类可读金额")


class QuoteView(BaseModel):
    token_in: str
    token_out: str
    amount_in_raw: str
    amount_out_raw: str
    path: list[str]


class SwapRequest(BaseModel):
    from_address: str
    token_in: str
    token_out: str
    amount_in: str
    amount_out_min: str = "0"
    dry_run: bool = True


@router.get("/config", response_model=BdexConfig)
def bdex_config(chain: ChainDep) -> BdexConfig:
    return BdexConfig(
        v2_factory=chain.contracts.v2_factory,
        v2_router=chain.contracts.v2_router,
        v3_factory=chain.contracts.v3_factory,
        v3_swap_router=chain.contracts.v3_swap_router,
        known_pairs=[
            {"name": "WBOT/USDT", "token0": chain.contracts.wbot, "token1": chain.contracts.usdt},
        ],
    )


@router.get("/pairs", response_model=list[PairView])
def bdex_pairs(w3: Web3Dep, chain: ChainDep) -> list[PairView]:
    factory = contract_at(w3, chain.contracts.v2_factory, V2_FACTORY_ABI)
    out: list[PairView] = []
    for a, b in ((chain.contracts.wbot, chain.contracts.usdt),):
        pair = factory.functions.getPair(a, b).call()
        if pair == ZERO_ADDRESS:
            continue
        contract = contract_at(w3, pair, V2_PAIR_ABI)
        r0, r1, _ = contract.functions.getReserves().call()
        out.append(
            PairView(
                pair=pair,
                token0=contract.functions.token0().call(),
                token1=contract.functions.token1().call(),
                reserve0=str(r0),
                reserve1=str(r1),
            )
        )
    return out


@router.post("/quote", response_model=QuoteView)
def bdex_quote(request: QuoteRequest, w3: Web3Dep, chain: ChainDep) -> QuoteView:
    token_in = Web3.to_checksum_address(request.token_in)
    token_out = Web3.to_checksum_address(request.token_out)
    erc20 = contract_at(w3, token_in, ERC20_ABI)
    decimals = erc20.functions.decimals().call()
    amount_raw = wei_from_decimal(request.amount_in, decimals)
    router = contract_at(w3, chain.contracts.v2_router, V2_ROUTER_ABI)
    try:
        amounts = router.functions.getAmountsOut(amount_raw, [token_in, token_out]).call()
    except Web3Exception as exc:
        raise TxRevertedError(f"报价失败（池不存在或路径非法）: {exc}") from exc
    return QuoteView(
        token_in=token_in,
        token_out=token_out,
        amount_in_raw=str(amounts[0]),
        amount_out_raw=str(amounts[-1]),
        path=[token_in, token_out],
    )


@router.post(
    "/swap/build",
    description="构建 V2 路由 calldata（返回待签交易预览）",
)
def bdex_swap_build(
    request: SwapRequest, w3: Web3Dep, chain: ChainDep, tx_service: TxServiceDep
) -> dict[str, Any]:
    data = _swap_calldata(w3, chain, request)
    outcome = tx_service.execute(
        from_address=request.from_address,
        to_address=chain.contracts.v2_router,
        value_wei=0,
        data=data,
        dry_run=True,
    )
    return outcome.model_dump()


@router.post(
    "/swap/execute",
    response_model=TxPreview | TxReceiptSummary,
    description="一步式 V2 兑换（先决条件：已 approve）；dry_run=true 默认仅预览",
)
def bdex_swap_execute(
    request: SwapRequest,
    w3: Web3Dep,
    chain: ChainDep,
    tx_service: TxServiceDep,
) -> TxPreview | TxReceiptSummary:
    data = _swap_calldata(w3, chain, request)
    return tx_service.execute(
        from_address=request.from_address,
        to_address=chain.contracts.v2_router,
        value_wei=0,
        data=data,
        dry_run=request.dry_run,
    )


@router.get("/pool/{pair}/volume")
def bdex_pool_volume(pair: str, w3: Web3Dep, chain: ChainDep) -> dict[str, Any]:
    """近 N 块 Swap 事件统计（eth_getLogs；indexer 前的轻量实现）。"""
    tip = w3.eth.block_number
    try:
        logs = w3.eth.get_logs(
            {
                "address": Web3.to_checksum_address(pair),
                "fromBlock": max(0, tip - VOLUME_WINDOW_BLOCKS),
                "toBlock": "latest",
            }
        )
    except Web3Exception as exc:
        msg = f"getLogs 失败: {exc}"
        raise ChainError(msg) from exc
    return {
        "pair": pair,
        "window_blocks": VOLUME_WINDOW_BLOCKS,
        "swap_count": len(logs),
        "note": f"近 {VOLUME_WINDOW_BLOCKS} 块 Swap 事件数（getLogs 版；D4 后由 DuckDB 支撑）",
    }


def _swap_calldata(w3: Web3, chain: ChainSpec, request: SwapRequest) -> bytes:
    token_in = Web3.to_checksum_address(request.token_in)
    token_out = Web3.to_checksum_address(request.token_out)
    if token_in == token_out:
        msg = "token_in 与 token_out 相同"
        raise ServiceError(msg, code="bad_path")
    erc20 = contract_at(w3, token_in, ERC20_ABI)
    decimals = erc20.functions.decimals().call()
    out_decimals = contract_at(w3, token_out, ERC20_ABI).functions.decimals().call()
    amount_in_raw = wei_from_decimal(request.amount_in, decimals)
    amount_out_min_raw = wei_from_decimal(request.amount_out_min, out_decimals)
    recipient = Web3.to_checksum_address(request.from_address)
    deadline = w3.eth.get_block("latest")["timestamp"] + SWAP_DEADLINE_S
    router = contract_at(w3, chain.contracts.v2_router, V2_ROUTER_ABI)
    return hex_to_bytes(
        router.encode_abi(
            "swapExactTokensForTokens",
            args=[amount_in_raw, amount_out_min_raw, [token_in, token_out], recipient, deadline],
        )
    )
