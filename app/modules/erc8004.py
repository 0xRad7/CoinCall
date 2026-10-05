"""M6 ERC-8004 Agent 身份：contracts/{tokenId}/registry/register✏/reputation/validations。

P0-1 扩展（09 篇）：{token_id}/wallet✏（setAgentWallet，EIP-712 定案
见 results/w1_setagentwallet_findings.md）、register-result/{tx_hash}（Transfer mint
解析）、聚合视图 metadata_keys。
"""

from collections.abc import Mapping, Sequence
from typing import Annotated, Any, cast

from eth_account import Account
from eth_typing import ChecksumAddress, HexStr
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from web3 import Web3
from web3.exceptions import ContractLogicError, TransactionNotFound, Web3Exception

from app.core.abis.erc8004 import (
    IDENTITY_REGISTRY_ABI,
    REPUTATION_REGISTRY_ABI,
    VALIDATION_REGISTRY_ABI,
)
from app.core.deps import ChainDep, ExplorerDep, SettingsDep, TxServiceDep, Web3Dep
from app.core.eip712 import (
    AGENT_WALLET_SET_DEFAULT_TTL_S,
    AGENT_WALLET_SET_MAX_DELAY_S,
    sign_agent_wallet_set,
)
from app.core.errors import ChainError, ServiceError, TxRevertedError
from app.core.rpc import checksum, contract_at, hex_to_bytes
from app.core.tx import TxPreview, TxReceiptSummary

router = APIRouter(prefix="/agent-identity", tags=["M6 agent-identity"])

ZERO_ADDRESS = "0x" + "00" * 20
# 离线事件解码专用实例（无 provider）：process_log 是纯本地 ABI 解码，
# 与链客户端解耦使端点可全离线单测；链上行为与经真实 w3 解码完全一致（topic0 匹配同源）。
_DECODE_W3 = Web3()


class ContractStatus(BaseModel):
    name: str
    address: str
    deployed: bool
    note: str = ""


class ContractsView(BaseModel):
    identity_registry: ContractStatus
    reputation_registry: ContractStatus
    validation_registry: ContractStatus


class IdentityView(BaseModel):
    token_id: int
    owner: str
    token_uri: str
    agent_wallet: str
    metadata: dict[str, str] = Field(default_factory=dict)


class RegisterRequest(BaseModel):
    owner: str | None = Field(default=None, description="默认取环境变量出资账户")
    agent_uri: str = Field(default="https://bot-chain-api.local/agents/default")
    dry_run: bool = True


class WalletBindingRequest(BaseModel):
    """端点 A 请求体。签名语义（C-23 源码实证）：EIP-712 v4，newWallet 本人签。"""

    owner: str | None = Field(
        default=None, description="交易发送者；默认取环境变量出资账户，须为 owner/被授权者"
    )
    wallet_address: str = Field(description="新收款钱包地址（EIP-712 签名主体，即签名者本人）")
    deadline: int | None = Field(
        default=None,
        description="unix 秒；缺省=链时间+120s；链上窗口 [now, now+300s]",
    )
    signature: str | None = Field(
        default=None,
        description="newWallet 的签名（0x…，65 字节 ECDSA 或智能钱包 ERC-1271）；"
        "wallet_address 为服务代管/出资账户时可省略，由服务代签",
    )
    dry_run: bool = True


class RegisterResultView(BaseModel):
    """端点 C 响应：register 回执的 Transfer mint 解析。"""

    found: bool
    tx_hash: str
    status: int | None = None
    block_number: int | None = None
    agent_ids: list[int] = Field(default_factory=list)
    owner: str | None = None
    agent_wallet: str | None = None


class ReputationView(BaseModel):
    token_id: int
    summary: dict[str, Any] | None = None
    note: str = ""


class ValidationsView(BaseModel):
    token_id: int
    validations: list[Any] = Field(default_factory=list)
    note: str = ""


@router.get("/contracts", response_model=ContractsView)
def erc8004_contracts(w3: Web3Dep, chain: ChainDep) -> ContractsView:
    def status(name: str, address: str) -> ContractStatus:
        return ContractStatus(
            name=name,
            address=address,
            deployed=w3.eth.get_code(cast(ChecksumAddress, checksum(address))) not in (b"", "0x"),
        )

    return ContractsView(
        identity_registry=status("IdentityRegistry", chain.contracts.identity_registry),
        reputation_registry=status("ReputationRegistry", chain.contracts.reputation_registry),
        validation_registry=status("ValidationRegistry", chain.contracts.validation_registry),
    )


@router.get("/registry")
def erc8004_registry(
    explorer: ExplorerDep,
    chain: ChainDep,
    items_count: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """已注册身份分页（Blockscout token transfers 视角；mint=Transfer from 0x0）。"""
    raw = explorer.token_transfers(chain.contracts.identity_registry)
    items = [
        {
            "token_id": item.get("total", {}).get("value"),
            "tx_hash": item.get("tx_hash"),
            "from": item.get("from", {}).get("hash"),
            "to": item.get("to", {}).get("hash"),
            "timestamp": item.get("timestamp"),
        }
        for item in raw.get("items", [])[:items_count]
    ]
    return {"items": items, "next_page_params": raw.get("next_page_params")}


@router.get("/{token_id}", response_model=IdentityView)
def erc8004_identity(
    w3: Web3Dep,
    chain: ChainDep,
    token_id: int,
    metadata_keys: Annotated[list[str] | None, Query()] = None,
) -> IdentityView:
    """聚合视图；?metadata_keys=k1&metadata_keys=k2 附带按键读取的链上 metadata（默认不带）。"""
    reg = contract_at(w3, chain.contracts.identity_registry, IDENTITY_REGISTRY_ABI)
    try:
        owner = reg.functions.ownerOf(token_id).call()
        uri = reg.functions.tokenURI(token_id).call()
        wallet = reg.functions.getAgentWallet(token_id).call()
    except ContractLogicError as exc:
        raise TxRevertedError(f"tokenId {token_id} 不存在或未注册: {exc}") from exc
    except Web3Exception as exc:
        msg = f"RPC 失败: {exc}"
        raise ChainError(msg) from exc
    keys = metadata_keys or []
    metadata = {key: _decode_metadata(_call_metadata(reg, token_id, key)) for key in keys}
    return IdentityView(
        token_id=token_id, owner=owner, token_uri=uri, agent_wallet=wallet, metadata=metadata
    )


def _call_metadata(reg: Any, token_id: int, key: str) -> bytes:  # noqa: ANN401  # web3 Contract 动态类型
    try:
        raw = reg.functions.getMetadata(token_id, key).call()
    except (ContractLogicError, Web3Exception):
        return b""
    return bytes(raw)


def _decode_metadata(raw: bytes) -> str:
    """metadata 值按 UTF-8 返回，非法 UTF-8 回退 0x hex（值本体可能存 hash/二进制）。"""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return Web3.to_hex(raw)


@router.post(
    "/register",
    description="注册新 agent 身份 register(agentURI)；dry_run=true 默认仅预览",
)
def erc8004_register(
    request: RegisterRequest,
    w3: Web3Dep,
    chain: ChainDep,
    settings: SettingsDep,
    tx_service: TxServiceDep,
) -> dict[str, Any]:
    owner = Web3.to_checksum_address(request.owner) if request.owner else None
    if owner is None:
        if not settings.funded_key:
            msg = "未提供 owner 且环境变量未配置 BOT_CHAIN_TEST_PRIVATE_KEY"
            raise ServiceError(msg, code="no_owner")
        owner = Account.from_key(settings.funded_key).address
    reg = contract_at(w3, chain.contracts.identity_registry, IDENTITY_REGISTRY_ABI)
    data = reg.encode_abi("register", args=[request.agent_uri])
    outcome = tx_service.execute(
        from_address=owner,
        to_address=chain.contracts.identity_registry,
        value_wei=0,
        data=data,
        dry_run=request.dry_run,
    )
    return outcome.model_dump()


@router.get("/{token_id}/reputation", response_model=ReputationView)
def erc8004_reputation(w3: Web3Dep, chain: ChainDep, token_id: int) -> ReputationView:
    rep = contract_at(w3, chain.contracts.reputation_registry, REPUTATION_REGISTRY_ABI)
    try:
        summary = rep.functions.getSummary(token_id, [], "total", "desc").call()
    except (ContractLogicError, Web3Exception):
        summary = None
    return ReputationView(
        token_id=token_id,
        summary={"raw": list(summary)} if summary else None,
        note="ReputationRegistry.getSummary（无数据时为空）",
    )


@router.get("/{token_id}/validations", response_model=ValidationsView)
def erc8004_validations(w3: Web3Dep, chain: ChainDep, token_id: int) -> ValidationsView:
    val = contract_at(w3, chain.contracts.validation_registry, VALIDATION_REGISTRY_ABI)
    try:
        items = list(val.functions.getAgentValidations(token_id).call())
    except (ContractLogicError, Web3Exception):
        items = []
    return ValidationsView(token_id=token_id, validations=items)


@router.post(
    "/{token_id}/wallet",
    response_model=TxPreview | TxReceiptSummary,
    description=(
        "绑定/更换 agent 收款钱包 setAgentWallet(agentId,newWallet,deadline,signature)。"
        "签名语义（C-23 源码实证）：EIP-712 v4，"
        "typehash=AgentWalletSet(uint256 agentId,address newWallet,"
        "address owner,uint256 deadline)，"
        "domain name=ERC8004IdentityRegistry/version=1/合约=注册表代理地址，"
        "**签名者必须是 newWallet 本人**（ECDSA 恢复==newWallet 或其 ERC-1271）；"
        "owner（或被授权者）作为交易发送者。deadline 窗口 [now, now+300s]。"
        "dry_run=true 默认仅预览"
    ),
)
def erc8004_bind_wallet(
    token_id: int,
    request: WalletBindingRequest,
    *,
    w3: Web3Dep,
    chain: ChainDep,
    settings: SettingsDep,
    tx_service: TxServiceDep,
) -> TxPreview | TxReceiptSummary:
    # ---- 地址与 owner 解析 -------------------------------------------------
    wallet = checksum(request.wallet_address)
    if wallet == Web3.to_checksum_address(ZERO_ADDRESS):
        msg = "wallet_address 不能为零地址（链上 require bad wallet）"
        raise ServiceError(msg, code="bad_wallet")
    owner = checksum(request.owner) if request.owner else None
    if owner is None:
        if not settings.funded_key:
            msg = "未提供 owner 且环境变量未配置 BOT_CHAIN_TEST_PRIVATE_KEY"
            raise ServiceError(msg, code="no_owner")
        owner = Account.from_key(settings.funded_key).address
    reg = contract_at(w3, chain.contracts.identity_registry, IDENTITY_REGISTRY_ABI)
    try:
        onchain_owner = reg.functions.ownerOf(token_id).call()
    except ContractLogicError as exc:
        raise TxRevertedError(f"tokenId {token_id} 不存在或未注册: {exc}") from exc
    except Web3Exception as exc:
        msg = f"RPC 失败: {exc}"
        raise ChainError(msg) from exc
    if owner.lower() != str(onchain_owner).lower():
        msg = f"owner {owner} 非 tokenId {token_id} 的 owner（链上 owner={onchain_owner}）"
        raise ServiceError(msg, code="not_owner")

    # ---- deadline 窗口（链时间基准，C-23：[now, now+300s]）------------------
    chain_ts = int(w3.eth.get_block("latest")["timestamp"])
    deadline = (
        chain_ts + AGENT_WALLET_SET_DEFAULT_TTL_S
        if request.deadline is None
        else int(request.deadline)
    )
    if not chain_ts <= deadline <= chain_ts + AGENT_WALLET_SET_MAX_DELAY_S:
        msg = (
            f"deadline {deadline} 超出链上窗口 [now, now+{AGENT_WALLET_SET_MAX_DELAY_S}s]"
            f"（链时间 {chain_ts}）"
        )
        raise ServiceError(msg, code="bad_deadline")

    # ---- 签名：调用方自带（newWallet 的 ECDSA/ERC-1271）或服务代签 ----------
    if request.signature is not None:
        signature = hex_to_bytes(request.signature)
        if not signature:
            msg = "signature 不能为空（空签名必 revert，C-23）"
            raise ServiceError(msg, code="signature_required")
    else:
        try:
            signer = tx_service.resolve_signer(wallet)  # keystore 代管 → 出资账户（仅测试网）
        except ServiceError as exc:
            msg = (
                f"wallet_address 非服务代管/出资账户且未提供 signature"
                f"（须 newWallet 本人的 EIP-712 签名）: {exc.detail}"
            )
            raise ServiceError(msg, code="signature_required") from exc
        signature = sign_agent_wallet_set(
            signer,
            chain_id=w3.eth.chain_id,
            registry=chain.contracts.identity_registry,
            agent_id=token_id,
            new_wallet=wallet,
            owner=str(onchain_owner),
            deadline=deadline,
        )

    # ---- 交易（铁律 A4/A6：dry_run 默认、20 gwei、经 TxService）------------
    data = reg.encode_abi("setAgentWallet", args=[token_id, wallet, deadline, signature])
    return tx_service.execute(
        from_address=owner,
        to_address=chain.contracts.identity_registry,
        value_wei=0,
        data=data,
        dry_run=request.dry_run,
    )


def _decode_mints(logs: Sequence[Mapping[str, Any]], registry: str) -> tuple[list[int], str | None]:
    """解析注册表合约的 ERC-721 Transfer 铸造事件（from=0x0）→ (agent_ids, owner)。"""
    reg = contract_at(_DECODE_W3, registry, IDENTITY_REGISTRY_ABI)
    agent_ids: list[int] = []
    owner: str | None = None
    for log in logs:
        if str(log.get("address", "")).lower() != registry.lower():
            continue
        try:
            event = reg.events.Transfer().process_log(log)
        except (ContractLogicError, Web3Exception, ValueError, KeyError, TypeError):
            continue
        args = dict(event["args"])  # web3 v7 的 args 可能是 dict 或 AttributeDict，统一取 dict
        if str(args.get("from", "")).lower() != ZERO_ADDRESS:
            continue
        agent_ids.append(int(args["tokenId"]))
        owner = str(args["to"])
    return agent_ids, owner


@router.get(
    "/register-result/{tx_hash}",
    response_model=RegisterResultView,
    description=(
        "注册结果解析：register 交易回执 → ERC-721 Transfer mint 事件 → agent_ids+owner；"
        "未上链 found=false"
    ),
)
def erc8004_register_result(tx_hash: str, w3: Web3Dep, chain: ChainDep) -> RegisterResultView:
    try:
        receipt = w3.eth.get_transaction_receipt(HexStr(tx_hash))
    except TransactionNotFound:
        receipt = None
    except ValueError as exc:
        msg = f"tx_hash 非法: {tx_hash!r}"
        raise ServiceError(msg, code="bad_tx_hash") from exc
    if receipt is None:
        return RegisterResultView(found=False, tx_hash=tx_hash)
    agent_ids, owner = _decode_mints(receipt.get("logs", []), chain.contracts.identity_registry)
    agent_wallet: str | None = None
    if agent_ids:
        reg = contract_at(w3, chain.contracts.identity_registry, IDENTITY_REGISTRY_ABI)
        try:
            agent_wallet = reg.functions.getAgentWallet(agent_ids[0]).call()
        except (ContractLogicError, Web3Exception):
            agent_wallet = None
    return RegisterResultView(
        found=True,
        tx_hash=tx_hash,
        status=int(receipt.get("status", 0)),
        block_number=int(receipt.get("blockNumber", 0)),
        agent_ids=agent_ids,
        owner=owner,
        agent_wallet=agent_wallet,
    )
