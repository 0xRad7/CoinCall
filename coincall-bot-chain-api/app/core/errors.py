"""三段错误模型与 HTTP 映射（规范 §B.4）。

chain_error → 502（RPC/链通道层）
tx_reverted → 409（执行层，带 revert reason / tx_hash）
service_error → 422（服务层：签名者缺失、格式非法、状态冲突等）
未处理异常 → 500；pydantic 校验 → 422。
响应永不泄漏原生异常栈。
"""

import logging
from enum import StrEnum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.log import current_trace_id

logger = logging.getLogger(__name__)


class ErrorKind(StrEnum):
    CHAIN_ERROR = "chain_error"
    TX_REVERTED = "tx_reverted"
    SERVICE_ERROR = "service_error"


class ErrorResponse(BaseModel):
    error: str
    detail: str
    tx_hash: str | None = None
    trace_id: str | None = None
    code: str | None = None


class BotChainError(Exception):
    """业务错误基类：kind + detail + 可选 tx_hash/code + HTTP 状态。"""

    kind: ErrorKind
    http_status: int

    def __init__(
        self,
        detail: str,
        *,
        tx_hash: str | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.tx_hash = tx_hash
        self.code = code

    def to_payload(self) -> dict[str, str | None]:
        return {
            "error": self.kind.value,
            "detail": self.detail,
            "tx_hash": self.tx_hash,
            "trace_id": current_trace_id(),
            "code": self.code,
        }


class ChainError(BotChainError):
    kind = ErrorKind.CHAIN_ERROR
    http_status = 502


class TxRevertedError(BotChainError):
    kind = ErrorKind.TX_REVERTED
    http_status = 409


class ServiceError(BotChainError):
    kind = ErrorKind.SERVICE_ERROR
    http_status = 422


def _error_response(payload: dict[str, str | None], status: int) -> JSONResponse:
    validated = ErrorResponse(**payload).model_dump()
    return JSONResponse(status_code=status, content=validated)


def install_error_handlers(app: FastAPI) -> None:
    """把三段错误模型挂到 FastAPI 实例上。"""

    @app.exception_handler(BotChainError)
    async def handle_bot_chain_error(_: Request, exc: BotChainError) -> JSONResponse:
        return _error_response(exc.to_payload(), exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        payload: dict[str, str | None] = {
            "error": ErrorKind.SERVICE_ERROR.value,
            "detail": f"参数校验失败: {exc.errors()[:5]}",
            "tx_hash": None,
            "trace_id": current_trace_id(),
            "code": "validation_error",
        }
        return _error_response(payload, 422)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error path=%s", request.url.path)
        payload: dict[str, str | None] = {
            "error": ErrorKind.SERVICE_ERROR.value,
            "detail": "服务内部错误（详情见服务端日志，trace_id 可检索）",
            "tx_hash": None,
            "trace_id": current_trace_id(),
            "code": "internal",
        }
        return _error_response(payload, 500)
