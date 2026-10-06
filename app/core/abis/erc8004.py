"""IdentityRegistry（ERC-8004）ABI 副本切片（事实源：bot-chain-api d3 归档/W1 精录）。

跨仓约定同 payvault.json：合约 ABI 唯一事实源在合约侧与本仓只读副本；
本切片只含锚定任务用到的 setMetadata(uint256,string,bytes)。
合约地址（testnet 968）：0xec8fFbC3c9A34AdDCbB3A14F91db2bd26A8b99c0
（bot-chain-api app/core/chains.py CHAINS["testnet"].contracts.identity_registry）。
"""

from functools import lru_cache
from typing import Any

_SET_METADATA_ABI: list[dict[str, Any]] = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "agentId", "type": "uint256"},
            {"internalType": "string", "name": "metadataKey", "type": "string"},
            {"internalType": "bytes", "name": "metadataValue", "type": "bytes"},
        ],
        "name": "setMetadata",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


@lru_cache(maxsize=1)
def set_metadata_abi() -> list[dict[str, Any]]:
    """setMetadata 函数片段（POST /contracts/send 只需要这一个）。"""
    return [dict(entry) for entry in _SET_METADATA_ABI]
