"""跨仓黄金向量回归（汇合门固化，TEST 制品 T8×T14 绑定）。

合约侧（coincall-contracts）独立生成并经 PayVault 合约 ecrecover 验证的
EIP-712 向量，此处用网关实现复算 digest 并恢复签名者——两侧字节级一致
是验签（网关）/结算（合约）互认的永久证明。向量域为 0x…dead 占位
（纯密码学口径比对）；运行期真实域从 COINCALL_PAY_VAULT_ADDRESS 注入。
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.core.payment import XPayment, eip712_digest, recover_signer

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        not Path(
            Path(__file__).resolve().parents[2] / "coincall-contracts/vectors/eip712_golden.json"
        ).exists(),
        reason="需 coincall-contracts 仓在场（汇合环境）",
    ),
]

CONTRACT_VECTORS = Path(__file__).resolve().parents[2] / (
    "coincall-contracts/vectors/eip712_golden.json"
)


def _cases() -> list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]]:
    vec = json.loads(CONTRACT_VECTORS.read_text())
    return [(c["description"], c["message"], c, vec) for c in vec["cases"]]


@pytest.mark.parametrize(("desc", "message", "case", "vec"), _cases())
def test_contract_vector_digest_and_recover(
    desc: str, message: dict[str, Any], case: dict[str, Any], vec: dict[str, Any]
) -> None:
    vc = vec["domain"]["verifyingContract"]
    sig = case["signature"]
    xp = XPayment(
        **{
            "from": message["from"],
            "to": message["to"],
            "value": str(message["value"]),
            "validAfter": str(message["validAfter"]),
            "validBefore": str(message["validBefore"]),
            "nonce": message["nonce"],
            "v": sig["v"],
            "r": sig["r"],
            "s": sig["s"],
        }
    )
    digest = eip712_digest(xp, vc, vec["chain_id"])
    digest_hex = (digest.hex() if isinstance(digest, bytes) else str(digest)).removeprefix("0x")
    assert digest_hex == case["digest"].removeprefix("0x"), f"{desc}: digest 不一致"
    signer = recover_signer(xp, vc, vec["chain_id"])
    assert signer.lower() == case["address"].lower(), f"{desc}: 签名者不一致"
