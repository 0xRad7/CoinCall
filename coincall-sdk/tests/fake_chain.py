"""注入型链假件（A2：unit 零网络）——实现 ChainGateway Protocol，记录全部调用。

刻意不在 build_tx 断言链 ID：钱包侧"断言先于签名"（CONSTRAINTS A4）必须自行拦截。
"""

from typing import Any

from coincall.chain import GAS_PRICE_GWEI


class FakeChain:
    """链读写假件：可编程余额/授权/回执，记录 build_tx 与 send_raw 的全部载荷。"""

    def __init__(
        self,
        *,
        chain_id: int = 968,
        balance_raw: int = 0,
        allowance_raw: int = 0,
    ) -> None:
        self.chain_id = chain_id
        self.balance_raw = balance_raw
        self.allowance_raw = allowance_raw
        self.built_txs: list[dict[str, Any]] = []
        self.sent_raw: list[bytes] = []
        self.tx_counter = 0

    def build_tx(self, from_addr: str, to: str, data: bytes, gas: int) -> dict[str, Any]:
        self.tx_counter += 1
        tx = {
            "chainId": self.chain_id,
            "from": from_addr,
            "to": to,
            "value": 0,
            "gas": gas,
            "gasPrice": GAS_PRICE_GWEI * 10**9,
            "nonce": self.tx_counter,
            "data": data,
        }
        self.built_txs.append(tx)
        return tx

    def send_raw(self, raw: bytes) -> str:
        self.sent_raw.append(raw)
        return f"0xdeadbeef{len(self.sent_raw):064x}"

    def wait_receipt(self, tx_hash: str, timeout: int = 90) -> dict[str, Any]:
        return {"transactionHash": tx_hash, "status": 1, "gasUsed": 46192}

    def erc20_balance(self, wallet: str, token: str) -> int:
        return self.balance_raw

    def erc20_allowance(self, owner: str, spender: str, token: str) -> int:
        return self.allowance_raw
