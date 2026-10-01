"""Blockscout /api/v2 客户端封装（免 key；分页原样透传 next_page_params）。"""

from typing import Any

from httpx import Client, HTTPError

from app.core.errors import ChainError

CONNECT_TIMEOUT_HINT = "explorer 不可达"


class ExplorerClient:
    def __init__(self, http: Client) -> None:
        self._http = http

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:  # noqa: ANN401  # 透传任意 JSON
        try:
            resp = self._http.get(path, params=params)
            resp.raise_for_status()
        except HTTPError as exc:
            raise ChainError(f"{CONNECT_TIMEOUT_HINT}: {path} ({exc})") from exc
        return resp.json()

    def stats(self) -> dict[str, Any]:
        return self._get("/stats")

    def address_transactions(
        self, address: str, page_params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._get(f"/addresses/{address}/transactions", params=page_params)

    def token_holders(
        self, address: str, page_params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._get(f"/tokens/{address}/holders", params=page_params)

    def token_transfers(
        self, address: str, page_params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._get(f"/tokens/{address}/transfers", params=page_params)

    def transaction(self, tx_hash: str) -> dict[str, Any]:
        return self._get(f"/transactions/{tx_hash}")

    def block(self, number_or_hash: str) -> dict[str, Any]:
        return self._get(f"/blocks/{number_or_hash}")
