"""三段错误模型 + 网关专用异常（402 质询走 PaymentRequiredError 携带完整 challenge）。"""

import uuid
from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response


class ApiError(Exception):
    """三段错误（401/404/409/422/502…）。"""

    def __init__(self, *, status_code: int, error: str, detail: str, code: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.error = error
        self.detail = detail
        self.code = code


class PaymentRequiredError(Exception):
    """402 付费质询：challenge dict（02 §3 字段名对齐 x402）。"""

    def __init__(self, challenge: dict[str, Any]) -> None:
        super().__init__(str(challenge.get("code", "payment_required")))
        self.challenge = challenge


def _trace_id(request: Request) -> str:
    return str(getattr(request.state, "trace_id", ""))


def error_response(
    request: Request, status_code: int, error: str, detail: str, code: str
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "detail": detail, "code": code, "trace_id": _trace_id(request)},
    )


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return error_response(request, exc.status_code, exc.error, exc.detail, exc.code)


async def payment_required_handler(request: Request, exc: PaymentRequiredError) -> JSONResponse:
    challenge = dict(exc.challenge)
    challenge["trace_id"] = _trace_id(request)
    return JSONResponse(status_code=status.HTTP_402_PAYMENT_REQUIRED, content=challenge)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    locs = [".".join(str(p) for p in err.get("loc", ())) for err in exc.errors()]
    return error_response(
        request,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "invalid_request",
        "; ".join(locs) or "请求体校验失败",
        "request_invalid",
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return error_response(request, 500, "internal_error", "未处理异常", "internal_error")


async def trace_middleware(request: Request, call_next: RequestResponseEndpoint) -> Response:
    request.state.trace_id = uuid.uuid4().hex
    return await call_next(request)
