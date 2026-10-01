"""live + needs_funds：ERC-4337 EntryPoint/Bundler/UserOp（02 篇 test_05 矩阵）。

证据出处：results/d3_probes/bundler_probe.json（supportedEntryPoints=[v0.7]）。
"""

import pytest
from eth_account import Account
from web3 import Web3

from app.core.abis.erc4337 import ENTRY_POINT_ABI, SIMPLE_ACCOUNT_FACTORY_ABI
from app.core.bundler import (
    BundlerClient,
    build_and_send_user_op,
    build_user_operation,
    sign_user_operation,
)
from app.core.chains import get_chain
from app.core.errors import ChainError
from app.core.rpc import make_http_client
from app.core.tx import TxService

pytestmark = pytest.mark.live

CHAIN = get_chain("testnet")
ENTRY_POINT = CHAIN.contracts.entry_point
FACTORY = CHAIN.contracts.simple_account_factory
GAS_PRICE_WEI = 20 * 10**9
FUND_KEY_FOR_AA = "0x" + "55" * 32  # 仅计算 owner 地址，不发交易


def _bundler():
    return make_http_client(CHAIN.bundler_url)


class TestAaLive:
    def test_entry_point_deployed(self, w3) -> None:
        """EntryPoint v0.7 单例在 968 有代码（实测 16035 bytes）。"""
        assert w3.eth.get_code(ENTRY_POINT) not in (b"", "0x")

    def test_bundler_chain_id_matches(self) -> None:
        client = _bundler()
        try:
            resp = client.post(
                "", json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}
            )
            assert int(resp.json()["result"], 16) == 968
        finally:
            client.close()

    def test_bundler_supported_entry_points_v07(self) -> None:
        client = _bundler()
        try:
            resp = client.post(
                "",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_supportedEntryPoints",
                    "params": [],
                },
            )
            assert ENTRY_POINT.lower() in [a.lower() for a in resp.json()["result"]]
        finally:
            client.close()

    def test_factory_address_prediction_stable(self, w3) -> None:
        """SimpleAccountFactory.getAddress(owner, salt)：同参同址、异 salt 异址。"""
        factory = w3.eth.contract(address=FACTORY, abi=SIMPLE_ACCOUNT_FACTORY_ABI)
        owner = Account.from_key(FUND_KEY_FOR_AA).address
        a1 = factory.functions.getAddress(owner, 0).call()
        a2 = factory.functions.getAddress(owner, 0).call()
        a3 = factory.functions.getAddress(owner, 1).call()
        assert a1 == a2 and Web3.is_address(a1)
        assert a1 != a3

    def test_entry_point_get_user_op_hash_callable(self, w3) -> None:
        """EntryPoint.getUserOpHash 视图可用（UserOp 签名依赖它）。"""
        ep = w3.eth.contract(address=ENTRY_POINT, abi=ENTRY_POINT_ABI)
        packed = {
            "sender": "0x0000000000000000000000000000000000000000",
            "nonce": 0,
            "initCode": "0x",
            "callData": "0x",
            "accountGasLimits": "0x" + "00" * 32,
            "preVerificationGas": 0,
            "gasFees": "0x" + "00" * 32,
            "paymasterAndData": "0x",
            "signature": "0x",
        }
        op_hash = ep.functions.getUserOpHash(packed).call()
        assert isinstance(op_hash, bytes) and len(op_hash) == 32


@pytest.mark.needs_funds
class TestAaNeedsFunds:
    def test_full_userop_lifecycle(self, w3, funder_key) -> None:
        """建户→组装→签名→Bundler 提交→回执 success（4337 全链路）。

        实测（2026-10-02，偏差 #17）：bundler.bohr.life 接受 UserOp（estimate 模拟全过）
        但不出 bundle 提交上链 → 上链环节 skip（基础设施限制，非协议/代码缺陷）。
        """
        bundler = BundlerClient(_bundler())
        owner = Account.from_key(funder_key).address
        svc = TxService(w3=w3, funder_key=funder_key)
        factory = w3.eth.contract(
            address=CHAIN.contracts.simple_account_factory, abi=SIMPLE_ACCOUNT_FACTORY_ABI
        )
        svc.execute(  # 显式建户（bundler initCode 模拟 AA20，见偏差 #17）
            from_address=owner,
            to_address=CHAIN.contracts.simple_account_factory,
            value_wei=0,
            data=factory.encode_abi("createAccount", args=[owner, 7]),
            dry_run=False,
        )
        try:
            outcome = build_and_send_user_op(
                w3=w3,
                bundler=bundler,
                chain=CHAIN,
                owner_key=funder_key,
                tx_service=svc,
                salt=7,
                target=owner,
                value_wei=0,
                calldata=b"",
                dry_run=False,
            )
        except ChainError as exc:
            if exc.code == "userop_timeout":
                pytest.skip("bundler 收单不打包（estimate 模拟已验证签名/结构/nonce；偏差 #17）")
            raise
        assert outcome["success"] is True
        receipt = bundler.get_user_operation_receipt(outcome["user_op_hash"])
        assert receipt is not None

    def test_estimate_simulation_full_pass(self, w3, funder_key) -> None:
        """estimate 模拟全链路（签名/结构/nonce 探测）——上链受 bundler 限制（偏差 #17）。"""
        owner = Account.from_key(funder_key).address
        svc = TxService(w3=w3, funder_key=funder_key)
        factory = w3.eth.contract(
            address=CHAIN.contracts.simple_account_factory, abi=SIMPLE_ACCOUNT_FACTORY_ABI
        )
        svc.execute(  # 显式建户（bundler initCode 模拟 AA20，见偏差 #17）
            from_address=owner,
            to_address=CHAIN.contracts.simple_account_factory,
            value_wei=0,
            data=factory.encode_abi("createAccount", args=[owner, 8]),
            dry_run=False,
        )
        op = build_user_operation(
            w3, CHAIN, owner=owner, salt=8, target=owner, value_wei=0, calldata=b""
        )
        assert op["initCode"] == "0x"  # 已建户
        svc.execute(  # 入金：普通转账经 receive()→addDeposit（depositFor 本链 revert，偏差 #18）
            from_address=owner,
            to_address=op["sender"],
            value_wei=10**17,
            data=b"",
            dry_run=False,
        )
        signed = sign_user_operation(w3, CHAIN, {**op, "nonce": "0"}, funder_key)
        est = BundlerClient(_bundler()).estimate_user_operation_gas(
            signed, CHAIN.contracts.entry_point
        )
        assert int(est.get("verificationGasLimit", "0"), 16) > 0  # 模拟通过=签名与结构被认可
