"""unit 测试共享 fakes：web3 / TxService / Keystore 的桩实现（零网络）。"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from app.core.tx import TxPreview, TxReceiptSummary

CHAIN_ID = 968
BLOCK_NUMBER = 25_000_000
TX_HASH = "0x" + "ab" * 32
ACCOUNT_A = "0x1111111111111111111111111111111111111111"
ACCOUNT_B = "0x2222222222222222222222222222222222222222"
USDT = "0x75edC9335175Fc0552D51D48439F229c10420fe3"
WBOT = "0xD5452816194a3784dBa983426cCe7c122F4abd30"


def make_fake_block(number: int = BLOCK_NUMBER) -> dict[str, Any]:
    return {
        "number": number,
        "hash": "0x" + format(number, "064x"),
        "parentHash": "0x" + format(number - 1, "064x"),
        "timestamp": 1_700_000_000 + number,
        "gasUsed": 21000,
        "miner": ACCOUNT_A,
        "transactions": [TX_HASH],
        "baseFeePerGas": 0,
    }


def make_fake_receipt(status: int = 1, to: str = ACCOUNT_B) -> dict[str, Any]:
    return {
        "transactionHash": TX_HASH,
        "status": status,
        "blockNumber": BLOCK_NUMBER,
        "gasUsed": 21000,
        "effectiveGasPrice": 20 * 10**9,
        "from": ACCOUNT_A,
        "to": to,
        "logs": [],
        "contractAddress": None,
        "transactionIndex": 0,
        "type": 0,
    }


class FakeFunctions:
    """已知方法返回预配置 mock；未知方法 AttributeError（对齐 hasattr 语义）。"""

    def __init__(self, results: dict) -> None:
        self._results = results

    def __getattr__(self, name):
        if name in self._results:
            return self._results[name]
        raise AttributeError(name)


def _fn(value):
    """形状对齐 web3 惯用法 functions.<m>(args).call() / .build_transaction()。"""
    inner = MagicMock()
    inner.call.return_value = value
    inner.build_transaction.return_value = {"data": "0x" + "cd" * 8}
    return MagicMock(return_value=inner)


def make_fake_contract() -> MagicMock:
    """合约桩：functions.<m>(*args).call()/build_transaction() 可用。"""
    contract = MagicMock()
    contract.functions = FakeFunctions(
        {
            "symbol": _fn("USDT"),
            "name": _fn("TestToken"),
            "decimals": _fn(6),
            "totalSupply": _fn(10**12),
            "balanceOf": _fn(0),
            "allowance": _fn(0),
            "transfer": _fn(True),
            "approve": _fn(True),
            "ownerOf": _fn(ACCOUNT_A),
            "tokenURI": _fn("ipfs://t/1"),
            "getAgentWallet": _fn(ACCOUNT_B),
            "getPair": _fn(ACCOUNT_B),
            "token0": _fn(USDT),
            "token1": _fn(WBOT),
            "getReserves": _fn((10**18, 10**9, 0)),
            "getAmountsOut": _fn([10**6, 5 * 10**17]),
            "getAddress": _fn(ACCOUNT_A),
            "createAccount": _fn(ACCOUNT_A),
            "getUserOpHash": _fn(b"\xab" * 32),
            "getSummary": _fn((0, 0, 0, 0)),
            "getAgentValidations": _fn([]),
        }
    )
    contract.encode_abi.return_value = "0x" + "ee" * 8
    ctor_result = MagicMock()
    ctor_result.build_transaction.return_value = {"data": "0x" + "60" * 4}
    contract.constructor = MagicMock(return_value=ctor_result)
    return contract


def make_fake_w3() -> MagicMock:
    w3 = MagicMock()
    w3.eth.chain_id = CHAIN_ID
    w3.client_version = "Geth/v1.5.13-fake"
    w3.eth.block_number = BLOCK_NUMBER
    w3.eth.get_block.side_effect = lambda n=None, **_: make_fake_block(
        n if isinstance(n, int) and n >= 0 else BLOCK_NUMBER
    )
    w3.eth.get_balance.return_value = 10**18
    w3.eth.get_transaction_count.return_value = 0
    w3.eth.gas_price = 20 * 10**9
    w3.eth.send_raw_transaction.return_value = bytes.fromhex(TX_HASH[2:])
    w3.eth.wait_for_transaction_receipt.return_value = make_fake_receipt()
    w3.eth.get_transaction_receipt.return_value = make_fake_receipt()
    w3.eth.estimate_gas.return_value = 21000
    w3.eth.contract.return_value = make_fake_contract()
    w3.is_connected.return_value = True
    w3.to_checksum_side_effect = None
    return w3


def make_fake_tx_service() -> MagicMock:
    """TxService 桩：dry_run 返回预览；真实发送返回回执摘要。"""
    service = MagicMock()
    service.preview.return_value = TxPreview(
        dry_run=True,
        unsigned_tx={
            "from": ACCOUNT_A,
            "to": ACCOUNT_B,
            "value": 10**16,
            "gas": 21000,
            "gasPrice": 20 * 10**9,
            "nonce": 0,
            "chainId": CHAIN_ID,
        },
        estimated_gas=21000,
        total_cost_wei=21000 * 20 * 10**9,
    )

    def _execute(*args, dry_run=True, **kwargs):  # 测试桩
        if dry_run:
            return service.preview.return_value
        return TxReceiptSummary(
            dry_run=False,
            tx_hash=TX_HASH,
            status=1,
            block_number=BLOCK_NUMBER,
            gas_used=21000,
            effective_gas_price_wei=20 * 10**9,
            from_address=ACCOUNT_A,
            to_address=ACCOUNT_B,
        )

    service.execute.side_effect = _execute
    return service


def make_fake_keystore() -> MagicMock:
    keystore = MagicMock()
    keystore.create.return_value = SimpleNamespace(
        address=ACCOUNT_A,
        persisted=True,
        created_at="2026-10-01T00:00:00Z",
    )
    keystore.get.return_value = None
    keystore.reveal.return_value = "0x" + "33" * 32
    return keystore
