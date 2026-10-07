"""步骤①：api key 认证——httpx 调 core 的 /internal/apikeys/validate（base url 走 env）。"""

import httpx
from pydantic import BaseModel, ConfigDict

HTTP_OK = 200


class ApiKeyInfo(BaseModel):
    """core validate 响应（key → 绑定 wallet + quota）。"""

    model_config = ConfigDict(extra="ignore")

    key_id: str
    consumer_wallet: str
    quota_raw: int | None = None
    status: str


class AuthError(Exception):
    """认证失败（401）或 core 不可达（fail-closed）。"""

    def __init__(self, code: str, detail: str, status_code: int = 401) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


class CoreAuthClient:
    """core 管理面的 api key 校验客户端。"""

    def __init__(self, base_url: str, http: httpx.AsyncClient) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http

    async def validate(self, api_key: str) -> ApiKeyInfo:
        try:
            resp = await self.http.post(
                f"{self.base_url}/internal/apikeys/validate",
                json={"api_key": api_key},
            )
        except httpx.HTTPError as exc:
            raise AuthError("core_unavailable", f"core 不可达: {exc}", status_code=502) from exc
        if resp.status_code == HTTP_OK:
            return ApiKeyInfo.model_validate(resp.json())
        code = resp.json().get("code", "apikey_unknown") if resp.content else "apikey_unknown"
        raise AuthError(code, f"api key 校验失败（{resp.status_code}）")
