"""主网部署参数化单测（全部离线，无网络）：网络表 / CLI 解析 / 私钥纪律 / dry-run guard。"""

import inspect
from pathlib import Path

import pytest
from web3 import Web3

import script.deploy_testnet as deploy_mod
from payvault.networks import (
    MAINNET_DEFAULT_OPERATOR,
    NETWORKS,
    get_network,
    network_with_overrides,
)
from script.deploy_testnet import (
    load_private_key,
    mainnet_dry_run_guard,
    parse_args,
    resolve_deployer_key,
    resolve_operator,
    run_mainnet,
)

ANY_KEY = "0x" + "11" * 32
TEST_KEY = "0x" + "22" * 32

# 事实源快照（bot_chain_scripts/config.py BDEX 表 + mainnet-readiness.md §1/§2）：
# 网络表若被改动，这里必须同步——防地址静默漂移
EXPECTED = {
    "testnet": {
        "rpc_url": "https://rpc.bohr.life/",
        "chain_id": 968,
        "token": "0x75edC9335175Fc0552D51D48439F229c10420fe3",
        "network_label": "botchain-testnet",
    },
    "mainnet": {
        "rpc_url": "https://rpc.botchain.ai/",
        "chain_id": 677,
        "token": "0xaBabc7Ddc03e501d190C676BF3d92ef0e6e87a3C",
        "network_label": "botchain-mainnet",
    },
}


# ------------------------------- 网络表 -------------------------------


def test_network_table_complete() -> None:
    """网络表恰含两网，基础事实与事实源快照一致。"""
    assert set(NETWORKS) == {"testnet", "mainnet"}
    for name, expect in EXPECTED.items():
        net = NETWORKS[name]
        assert net.rpc_url == expect["rpc_url"]
        assert net.chain_id == expect["chain_id"]
        assert net.token == expect["token"]
        assert net.network_label == expect["network_label"]
        assert net.token_symbol == "USDT"
        assert net.token_decimals == 6
        assert Web3.is_checksum_address(net.token)


def test_deployment_files_split_by_network() -> None:
    """产物按网络分文件：mainnet-677.json 与 testnet-968.json 互不相同（绝不互覆）。"""
    assert NETWORKS["testnet"].deployment_file == "testnet-968.json"
    assert NETWORKS["mainnet"].deployment_file == "mainnet-677.json"
    files = {net.deployment_file for net in NETWORKS.values()}
    assert len(files) == len(NETWORKS)


def test_mainnet_default_operator_is_keystore_address() -> None:
    """主网默认 operator=keystore 托管地址（mainnet-readiness.md §2.1），checksum 合法。"""
    assert MAINNET_DEFAULT_OPERATOR == "0xb1ea3EA94e2Fd7Cb9244cD460dA863FC4b61033A"
    assert Web3.is_checksum_address(MAINNET_DEFAULT_OPERATOR)


def test_get_network_unknown_name() -> None:
    with pytest.raises(KeyError, match="未知网络"):
        get_network("devnet")


def test_network_with_overrides() -> None:
    """--rpc/--chain-id/--token 覆写派生新 Network；未覆写项保持网络表原值。"""
    net = network_with_overrides(
        "mainnet", chain_id=1234, token="0x75edc9335175fc0552d51d48439f229c10420fe3"
    )
    assert net.chain_id == 1234
    assert net.token == Web3.to_checksum_address(
        "0x75edc9335175fc0552d51d48439f229c10420fe3"
    )  # 小写输入归一 checksum
    assert net.rpc_url == EXPECTED["mainnet"]["rpc_url"]  # 未覆写保持
    assert net.deployment_file == "mainnet-1234.json"  # 产物文件名随链 ID 联动
    # 零覆写 = 网络表原实例（值相等）
    assert network_with_overrides("testnet") == NETWORKS["testnet"]


# ------------------------------- CLI 解析 -------------------------------


def test_parse_args_defaults_to_testnet() -> None:
    args = parse_args([])
    assert args.network == "testnet"
    assert args.yes_i_will_deploy is False
    for override in (args.rpc, args.chain_id, args.token, args.operator):
        assert override is None


def test_parse_args_mainnet_and_confirm_flag() -> None:
    args = parse_args(
        ["--network", "mainnet", "--yes-i-will-deploy", "--operator", "0x" + "33" * 20]
    )
    assert args.network == "mainnet"
    assert args.yes_i_will_deploy is True
    assert args.operator == "0x" + "33" * 20


def test_parse_args_rejects_unknown_network() -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(["--network", "devnet"])
    assert exc.value.code == 2  # argparse choices 拦截


# ------------------------------- 私钥纪律 -------------------------------


def test_mainnet_key_requires_deployer_env_exclusively() -> None:
    """主网只认 DEPLOYER_PRIVATE_KEY：给了测试键也绝不回退（anvil/测试 .env 路径全部禁用）。"""
    with pytest.raises(SystemExit) as exc:
        load_private_key("mainnet", {"BOT_CHAIN_TEST_PRIVATE_KEY": TEST_KEY})
    assert exc.value.code == 1
    assert load_private_key("mainnet", {"DEPLOYER_PRIVATE_KEY": ANY_KEY}) == ANY_KEY


def test_testnet_key_env_priority_and_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """测试网现行行为不变：env 优先，缺省解析 bot-chain-api/.env。"""
    assert load_private_key("testnet", {"BOT_CHAIN_TEST_PRIVATE_KEY": TEST_KEY}) == TEST_KEY
    env_file = tmp_path / ".env"
    env_file.write_text(f'BOT_CHAIN_TEST_PRIVATE_KEY="{TEST_KEY}"\n', encoding="utf-8")
    monkeypatch.setattr(deploy_mod, "ENV_FALLBACK", env_file)
    assert load_private_key("testnet", {}) == TEST_KEY


def test_resolve_deployer_key_accepts_hex_forms() -> None:
    assert resolve_deployer_key(ANY_KEY) == ANY_KEY
    assert resolve_deployer_key(f"{ANY_KEY}\n") == ANY_KEY
    assert resolve_deployer_key(ANY_KEY[2:]) == ANY_KEY  # 64 位裸 hex 归一补 0x


def test_resolve_deployer_key_accepts_0600_file(tmp_path: Path) -> None:
    key_file = tmp_path / "deployer.key"
    key_file.write_text(ANY_KEY + "\n", encoding="utf-8")
    key_file.chmod(0o600)
    assert resolve_deployer_key(str(key_file)) == ANY_KEY
    bare = tmp_path / "bare.key"
    bare.write_text(ANY_KEY[2:], encoding="utf-8")
    bare.chmod(0o600)
    assert resolve_deployer_key(str(bare)) == ANY_KEY  # 文件内容裸 hex 同样归一


def test_resolve_deployer_key_rejects_loose_file_permissions(tmp_path: Path) -> None:
    key_file = tmp_path / "deployer.key"
    key_file.write_text(ANY_KEY, encoding="utf-8")
    key_file.chmod(0o644)  # 组/其他可读 → 拒绝
    with pytest.raises(SystemExit) as exc:
        resolve_deployer_key(str(key_file))
    assert exc.value.code == 1


def test_resolve_deployer_key_rejects_nonexistent_garbage() -> None:
    with pytest.raises(SystemExit) as exc:
        resolve_deployer_key("/nonexistent/not-a-key")
    assert exc.value.code == 1


# ------------------------------- 主网 dry-run guard -------------------------------


def test_mainnet_guard_stops_before_signing_without_confirm(capsys: pytest.CaptureFixture) -> None:
    """红线行为：无 --yes-i-will-deploy → 签名前退出（exit 0=dry-run 按预期停止）。"""
    with pytest.raises(SystemExit) as exc:
        mainnet_dry_run_guard(False)
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "未签名" in out and "--yes-i-will-deploy" in out


def test_mainnet_guard_passes_with_confirm() -> None:
    mainnet_dry_run_guard(True)  # 显式确认 → 不退出（真部署仅发起人手动触发）


def test_mainnet_flow_has_no_anvil_mnemonic() -> None:
    """结构性红线：主网流程源码不得引用 anvil 助记词（垫付/批量冒烟是测试网专属）。"""
    assert "ANVIL_MNEMONIC" not in inspect.getsource(run_mainnet)


# ------------------------------- operator 解析 -------------------------------


def test_resolve_operator_precedence() -> None:
    """--operator 覆写 > 主网默认 keystore 地址 > 测试网=署名者。"""
    override = "0x" + "44" * 20
    assert resolve_operator(
        NETWORKS["mainnet"], parse_args(["--operator", override]), "0x" + "55" * 20
    ) == Web3.to_checksum_address(override)
    assert (
        resolve_operator(NETWORKS["mainnet"], parse_args([]), "0x" + "55" * 20)
        == MAINNET_DEFAULT_OPERATOR
    )
    deployer = "0xC37fFE97B4D2C3D0187B1dDEDF273E52A461B63a"
    assert resolve_operator(NETWORKS["testnet"], parse_args([]), deployer) == deployer
