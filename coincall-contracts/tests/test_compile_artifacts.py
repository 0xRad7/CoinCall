"""编译产物一致性：源码与 artifacts/ 不许漂移；版本口径钉死。"""

import pytest

from payvault.compile import CONTRACTS_DIR, EVM_VERSION, SOLC_VERSION, compile_all, load_artifact


def test_artifacts_fresh() -> None:
    """当前源码重编译结果与入库产物逐字段一致（bytecode/ABI/元数据）。"""
    fresh = compile_all(write=False)
    for name, artifact in fresh.items():
        stored = load_artifact(name)
        assert artifact == stored, f"{name} 产物与源码漂移，请重跑 compile_all()"


def test_compiler_pinned() -> None:
    for name in ("PayVault", "MockUSDT"):
        artifact = load_artifact(name)
        assert artifact["compilerVersion"] == SOLC_VERSION
        assert artifact["evmVersion"] == EVM_VERSION


def test_sources_present() -> None:
    assert sorted(p.name for p in CONTRACTS_DIR.glob("*.sol")) == ["MockUSDT.sol", "PayVault.sol"]


def test_load_artifact_missing() -> None:
    with pytest.raises(FileNotFoundError):
        load_artifact("Nonexistent")
