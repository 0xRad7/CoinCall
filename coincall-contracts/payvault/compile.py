"""Solidity 编译封装（04 §5：solc 0.8.24 / shanghai，离线编译产物入库）。

产物 artifacts/<Name>.json 是其他仓接线的 ABI/bytecode 唯一来源；
``tests/test_artifacts.py`` 会重编译比对，防止源码与产物漂移。
"""

import json
from pathlib import Path
from typing import Any

import solcx
from eth_utils import keccak

SOLC_VERSION = "0.8.24"
EVM_VERSION = "shanghai"  # PUSH0 可用，与链上已验证合约同口径

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = ROOT / "contracts"
ARTIFACTS_DIR = ROOT / "artifacts"
CONTRACT_NAMES = ("PayVault", "MockUSDT")


def ensure_solc() -> None:
    """本机无目标版本则下载安装（网络兜底链见 CONSTRAINTS.md；主网机器可 brew install solc）。"""
    installed = [str(v) for v in solcx.get_installed_solc_versions()]
    if SOLC_VERSION not in installed:
        solcx.install_solc(SOLC_VERSION)


def solc_binary_path() -> Path:
    """显式解析 ~/.solcx 下的 solc 二进制。

    必须显式指定：若 PATH 上存在其他 solc（如 anaconda 的 solc-select 空壳），
    py-solc-x 会优先取 PATH 导致误用。
    """
    ensure_solc()
    path = Path(solcx.get_solcx_install_folder()) / f"solc-v{SOLC_VERSION}"
    if not path.exists():
        msg = f"solc 二进制不存在: {path}"
        raise FileNotFoundError(msg)
    return path


def compile_all(write: bool = True) -> dict[str, dict[str, Any]]:
    """编译 contracts/ 下全部 .sol，返回 {名字: 产物}；write=True 时写 artifacts/。"""
    ensure_solc()
    sources = {
        f"contracts/{path.name}": {"content": path.read_text(encoding="utf-8")}
        for path in sorted(CONTRACTS_DIR.glob("*.sol"))
    }
    standard_input = {
        "language": "Solidity",
        "sources": sources,
        "settings": {
            "evmVersion": EVM_VERSION,
            "optimizer": {"enabled": True, "runs": 200},
            "outputSelection": {
                "*": {
                    "*": [
                        "abi",
                        "evm.bytecode.object",
                        "evm.deployedBytecode.object",
                    ]
                }
            },
        },
    }
    compiled = solcx.compile_standard(
        standard_input, allow_paths=str(ROOT), solc_binary=str(solc_binary_path())
    )
    artifacts: dict[str, dict[str, Any]] = {}
    for source_name, data in compiled["contracts"].items():
        for contract_name, out in data.items():
            if contract_name not in CONTRACT_NAMES:
                continue
            abi = out["abi"]
            bytecode = "0x" + out["evm"]["bytecode"]["object"]
            deployed = "0x" + out["evm"]["deployedBytecode"]["object"]
            artifacts[contract_name] = {
                "contractName": contract_name,
                "compilerVersion": SOLC_VERSION,
                "evmVersion": EVM_VERSION,
                "sourceFile": source_name,
                "abi": abi,
                "bytecode": bytecode,
                "deployedBytecode": deployed,
                "deployedBytecodeHash": "0x" + keccak(bytes.fromhex(deployed[2:])).hex(),
            }
    missing = sorted(set(CONTRACT_NAMES) - set(artifacts))
    if missing:
        msg = f"编译产物缺少合约: {missing}"
        raise RuntimeError(msg)
    if write:
        ARTIFACTS_DIR.mkdir(exist_ok=True)
        for name, artifact in artifacts.items():
            target = ARTIFACTS_DIR / f"{name}.json"
            target.write_text(
                json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
    return artifacts


def load_artifact(contract_name: str) -> dict[str, Any]:
    """读取入库的编译产物（不触发编译）。"""
    path = ARTIFACTS_DIR / f"{contract_name}.json"
    if not path.exists():
        msg = f"编译产物不存在，先运行 compile_all(): {path}"
        raise FileNotFoundError(msg)
    return json.loads(path.read_text(encoding="utf-8"))
