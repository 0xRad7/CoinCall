"""三段错误模型：{error, detail, code, trace_id}（全族统一风格）。"""

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError


class ApiError(Exception):
    """业务错误：携带 HTTP 状态与三段模型字段。"""

    def __init__(self, *, status_code: int, error: str, detail: str, code: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.error = error
        self.detail = detail
        self.code = code


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


def _flatten_validation(errors: Sequence[Mapping[str, Any]]) -> str:
    parts = []
    for err in errors:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}")
    return "; ".join(parts) or "请求体校验失败"


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(
        request,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "invalid_request",
        _flatten_validation(exc.errors()),
        "request_invalid",
    )


async def pydantic_error_handler(request: Request, exc: ValidationError) -> JSONResponse:
    return error_response(
        request,
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "invalid_request",
        _flatten_validation(exc.errors()),
        "request_invalid",
    )
