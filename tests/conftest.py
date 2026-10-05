"""pytest 共享 fixture：eth-tester 本地 EVM + 固定助记词账户 + 部署好的 vault 环境。

所有行为测试（T4~T8）都在本地 EVM 上跑（marker: unit），无网络依赖。
"""

from types import SimpleNamespace
from typing import Any

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3 import Web3
from web3.contract import Contract

from payvault.chain import call_contract, connect_local_tester, deploy_contract
from payvault.compile import load_artifact

# 公开 anvil 测试助记词派生固定账户（与黄金向量同源，测试确定性）
TEST_MNEMONIC = "test test test test test test test test test test test junk"
ROLES = ("operator", "consumer", "provider", "attacker", "outsider")
FUND_WEI = 10 * 10**18
MINT_AMOUNT = 100_000 * 10**6  # 100,000 MockUSDT (6dp)


@pytest.fixture(scope="session")
def artifacts() -> dict[str, dict[str, Any]]:
    return {name: load_artifact(name) for name in ("PayVault", "MockUSDT")}


@pytest.fixture()
def w3() -> Web3:
    return connect_local_tester()


@pytest.fixture()
def keys() -> dict[str, LocalAccount]:
    Account.enable_unaudited_hdwallet_features()
    return {
        role: Account.from_mnemonic(TEST_MNEMONIC, account_path=f"m/44'/60'/0'/0/{index}")
        for index, role in enumerate(ROLES)
    }


@pytest.fixture()
def vault_env(w3: Web3, keys: dict[str, LocalAccount]) -> SimpleNamespace:
    """部署好的完整环境：MockUSDT + PayVault，consumer 已 mint、已 approve 全额。"""
    # 用 eth-tester 预置账户垫付 ETH 给固定角色账户
    funder = w3.eth.accounts[0]
    for key in keys.values():
        w3.eth.send_transaction({"from": funder, "to": key.address, "value": FUND_WEI})

    mock_usdt, _ = deploy_contract(w3, keys["operator"], "MockUSDT")
    vault, _ = deploy_contract(
        w3, keys["operator"], "PayVault", mock_usdt.address, keys["operator"].address
    )

    call_contract(
        w3, keys["operator"], mock_usdt.functions.mint(keys["consumer"].address, MINT_AMOUNT)
    )
    call_contract(w3, keys["consumer"], mock_usdt.functions.approve(vault.address, 2**256 - 1))

    return SimpleNamespace(
        w3=w3,
        mock_usdt=mock_usdt,
        vault=vault,
        chain_id=w3.eth.chain_id,
        **keys,
    )


@pytest.fixture()
def now(w3: Web3) -> int:
    return int(w3.eth.get_block("latest")["timestamp"])


def vault_balance(mock_usdt: Contract, vault: Contract) -> int:
    return int(mock_usdt.functions.balanceOf(vault.address).call())


def vault_state(mock_usdt: Contract, vault: Contract) -> tuple[int, int, int]:
    """(合约 token 余额, totalCredits, 两者差) —— I4 断言用。"""
    balance = vault_balance(mock_usdt, vault)
    total = int(vault.functions.totalCredits().call())
    return balance, total, balance - total


def checksum(value: str) -> str:
    return Web3.to_checksum_address(value)
