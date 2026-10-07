"""needs_funds（10 §1 冻结契约·G2）：决策摘要锚定真链实跑（一次）。

前置：coincall-bot-chain-api 在 8010 在线（operator 由其 keystore 签名）、
coincall-gateway 在 8030 在线（提供真实窗口统计）。

流程：
  ① 拉网关真实统计（GET 8030 /internal/stats/calls?window_hours=720）→ 规范 JSON
     的 sha256 即决策摘要 digest（core 的 anchor-pending 未落地期由测试自算顶位，
     value=digest|pointer 语义与生产逐字节一致）；
  ② BotChainAnchorChain.submit_anchor：ERC-8004 setMetadata(tokenId=162,
     "coincall:decision:v1", digest|pointer UTF-8 bytes) 经 8010 真实上链
     （from=出资账户/operator，gas 由其承担）；
  ③ getMetadata 链上回读比对（scan 语义：链上值 == 提交值）。

tokenId=162 为 operator 名下真实 provider 身份 token（ownerOf 链上核过）。
"""

import hashlib
import json
import logging
import os

import httpx
import pytest

from app.modules.anchor import ANCHOR_KEY, BotChainAnchorChain

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.needs_funds

BOTCHAIN = os.environ.get("COINCALL_BOT_CHAIN_API_BASE_URL", "http://127.0.0.1:8010")
GATEWAY = os.environ.get("COINCALL_GATEWAY_BASE_URL", "http://127.0.0.1:8030")
#: IdentityRegistry（testnet 968；事实源 bot-chain-api chains.py）
REGISTRY = "0xec8fFbC3c9A34AdDCbB3A14F91db2bd26A8b99c0"
OPERATOR = "0xC37fFE97B4D2C3D0187B1dDEDF273E52A461B63a"
#: 真实 provider 身份 token（owner=OPERATOR，链上 ownerOf 核验过）
TOKEN_ID = 162
SCAN = "https://scan.bohr.life"

GET_METADATA_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "agentId", "type": "uint256"},
            {"internalType": "string", "name": "metadataKey", "type": "string"},
        ],
        "name": "getMetadata",
        "outputs": [{"internalType": "bytes", "name": "", "type": "bytes"}],
        "stateMutability": "view",
        "type": "function",
    }
]


async def test_real_decision_anchor_setmetadata_roundtrip() -> None:
    async with httpx.AsyncClient(timeout=70.0, trust_env=False) as http:  # C-07：直连绕系统代理
        # ① 真实统计 → digest（并行 core 任务落地前由本测试代行聚合摘要角色）
        stats_url = f"{GATEWAY}/internal/stats/calls?window_hours=720"
        resp = await http.get(stats_url)
        assert resp.status_code == 200, resp.text
        canonical = json.dumps(resp.json(), sort_keys=True, separators=(",", ":"))
        digest = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
        value = f"{digest}|{stats_url}"
        logger.info("锚定摘要: %s（720h 窗口，%d 字节规范 JSON）", digest, len(canonical))

        # ② 真实上链：setMetadata(162, coincall:decision:v1, digest|pointer)
        chain = BotChainAnchorChain(
            base_url=BOTCHAIN,
            registry_address=REGISTRY,
            operator_address=OPERATOR,
            http=http,
        )
        tx_hash = await chain.submit_anchor(TOKEN_ID, ANCHOR_KEY, value)
        logger.info("锚定交易: %s/tx/%s", SCAN, tx_hash)

        # ③ 链上回读（scan 哈希核验语义）：getMetadata 返回值逐字节等于提交值
        read = await http.post(
            f"{BOTCHAIN}/api/v1/contracts/call",
            json={
                "to": REGISTRY,
                "abi": GET_METADATA_ABI,
                "method": "getMetadata",
                "args": [TOKEN_ID, ANCHOR_KEY],
            },
        )
        assert read.status_code == 200, read.text
        onchain_hex = str(read.json()["result"])
        assert "0x" + value.encode().hex() == onchain_hex, {
            "expected": value,
            "onchain": bytes.fromhex(onchain_hex[2:]).decode(errors="replace"),
        }
        # 交易回执状态由 submit_anchor 内部断言（status=1）；此处再核 tx 哈希格式
        assert tx_hash.startswith("0x") and len(tx_hash) == 66
