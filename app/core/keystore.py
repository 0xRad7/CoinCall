"""服务代管账户 keystore：Fernet 加密落盘（或进程内临时态）。

铁律 A4：私钥仅两处存在——加密文件与进程内存；接口仅在 reveal=true 时返回一次；
日志层有 private_key 脱敏兜底。
"""

import base64
import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from eth_account import Account
from eth_account.signers.local import LocalAccount
from pydantic import BaseModel

from app.core.errors import ServiceError


class ManagedAccount(BaseModel):
    address: str
    persisted: bool
    created_at: str
    private_key: str | None = None  # 仅 reveal=true 时出现一次


class Keystore:
    """目录 data/keystore/ 下每个地址一个加密 JSON；secret 未配置时仅内存态。"""

    def __init__(self, directory: Path, secret: str | None) -> None:
        self._dir = directory
        self._persist = secret is not None
        self._fernet = self._derive_fernet(secret)
        self._memory: dict[str, LocalAccount] = {}
        self._guard = threading.Lock()
        if self._persist:
            self._dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _derive_fernet(secret: str | None) -> Fernet:
        material = secret.encode() if secret else Fernet.generate_key()
        key = base64.urlsafe_b64encode(hashlib.sha256(material).digest())
        return Fernet(key)

    def create(self) -> ManagedAccount:
        acct = Account.create()
        created_at = datetime.now(UTC).isoformat()
        with self._guard:
            self._memory[acct.address] = acct
            if self._persist:
                payload: dict[str, Any] = {
                    "address": acct.address,
                    "ciphertext": self._fernet.encrypt(acct.key.hex().encode()).decode(),
                    "created_at": created_at,
                }
                (self._dir / f"{acct.address}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        return ManagedAccount(address=acct.address, persisted=self._persist, created_at=created_at)

    def get(self, address: str) -> LocalAccount | None:
        with self._guard:
            cached = self._memory.get(address)
        if cached is not None:
            return cached
        path = self._dir / f"{address}.json"
        if not self._persist or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            key_hex = self._fernet.decrypt(payload["ciphertext"].encode()).decode()
            acct = Account.from_key("0x" + key_hex)
        except (InvalidToken, KeyError, ValueError) as exc:
            msg = f"keystore 解密失败（secret 不匹配或文件损坏）: {address}"
            raise ServiceError(msg, code="keystore_error") from exc
        with self._guard:
            self._memory[address] = acct
        return acct

    def reveal(self, address: str) -> str:
        acct = self.get(address)
        if acct is None:
            msg = f"地址非服务代管: {address}"
            raise ServiceError(msg, code="not_managed")
        return "0x" + acct.key.hex()
