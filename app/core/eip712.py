"""IdentityRegistry.setAgentWallet 的 EIP-712 v4 签名（源码实证定案，C-23）。

依据：results/w1_setagentwallet_findings.md（impl 0xdb74b629… 已验证源码归档）。
- typehash: AgentWalletSet(uint256 agentId,address newWallet,address owner,uint256 deadline)
- domain:   name="ERC8004IdentityRegistry" / version="1" / chainId=当前链 / 合约=代理地址
- 签名者:   newWallet 本人（ECDSA 恢复==newWallet，或 newWallet 的 ERC-1271）
- 窗口:     deadline ∈ [now, now+300s]（MAX_DEADLINE_DELAY=5min，无 nonce）

digest 构造与 eth_account.encode_typed_data 逐字节一致（单测黄金向量锁定）。
"""

from eth_abi import encode as abi_encode
from eth_account.signers.local import LocalAccount
from eth_typing import Hash32
from eth_utils import keccak
from web3 import Web3

# OZ EIP712Upgradeable._buildDomainSeparator 的 TYPE_HASH
EIP712_DOMAIN_TYPEHASH = keccak(
    b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)
# IdentityRegistryUpgradeable.AGENT_WALLET_SET_TYPEHASH
AGENT_WALLET_SET_TYPEHASH = keccak(
    b"AgentWalletSet(uint256 agentId,address newWallet,address owner,uint256 deadline)"
)

EIP712_NAME = "ERC8004IdentityRegistry"
EIP712_VERSION = "1"

AGENT_WALLET_SET_MAX_DELAY_S = 300  # 合约 MAX_DEADLINE_DELAY = 5 minutes
AGENT_WALLET_SET_DEFAULT_TTL_S = 120  # 服务端默认 deadline：链时间 + 2 分钟（窗口内留足打包余量）


def domain_separator(chain_id: int, verifying_contract: str) -> bytes:
    """EIP-712 domainSeparator（OZ 5.4 升级版：每次调用按 name/version/chainid/本合约地址重算）。"""
    return keccak(
        abi_encode(
            ["bytes32", "bytes32", "bytes32", "uint256", "address"],
            [
                EIP712_DOMAIN_TYPEHASH,
                keccak(EIP712_NAME.encode()),
                keccak(EIP712_VERSION.encode()),
                chain_id,
                verifying_contract,
            ],
        )
    )


def agent_wallet_set_digest(
    *,
    chain_id: int,
    registry: str,
    agent_id: int,
    new_wallet: str,
    owner: str,
    deadline: int,
) -> bytes:
    """setAgentWallet 的最终签名 digest（"\\x19\\x01" ‖ domainSeparator ‖ structHash）。"""
    struct_hash = keccak(
        abi_encode(
            ["bytes32", "uint256", "address", "address", "uint256"],
            [AGENT_WALLET_SET_TYPEHASH, agent_id, new_wallet, owner, deadline],
        )
    )
    return keccak(b"\x19\x01" + domain_separator(chain_id, registry) + struct_hash)


def sign_agent_wallet_set(
    signer: LocalAccount,
    *,
    chain_id: int,
    registry: str,
    agent_id: int,
    new_wallet: str,
    owner: str,
    deadline: int,
) -> bytes:
    """用 newWallet 的私钥签 digest（65 字节 r‖s‖v，v∈{27,28}，OZ ECDSA 可恢复）。

    digest 内嵌 chain_id，签名者与链必须同源（服务端两侧均取自同一 web3 实例，
    测试网出资私钥仅测试网加载，铁律 A4）。
    """
    wallet = Web3.to_checksum_address(new_wallet)
    if signer.address != wallet:
        msg = f"签名者 {signer.address} 不是 newWallet {wallet}（须新钱包本人签名）"
        raise ValueError(msg)
    digest = agent_wallet_set_digest(
        chain_id=chain_id,
        registry=registry,
        agent_id=agent_id,
        new_wallet=wallet,
        owner=owner,
        deadline=deadline,
    )
    return bytes(signer.unsafe_sign_hash(Hash32(digest)).signature)
