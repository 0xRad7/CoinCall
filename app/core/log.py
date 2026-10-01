"""结构化日志 + trace_id 贯穿 + 敏感字段脱敏（规范 §B.6 / 铁律 A4）。"""

import contextvars
import json
import logging
import time
import uuid
from collections.abc import Mapping
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REDACTED = "***"
TRACE_ID_HEADER = "X-Trace-Id"
# 归一化（去 -/_ 并小写）后的敏感键集合：privatekey/authorization/apikey/xapikey/secret/password
SENSITIVE_KEYS = frozenset(
    {"privatekey", "authorization", "apikey", "xapikey", "secret", "password"}
)

_trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)

SLOW_REQUEST_S = 3.0
_STD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
    }
)


def current_trace_id() -> str | None:
    return _trace_id_var.get()


def redact(obj: Any) -> Any:  # noqa: ANN401  # 递归脱敏遍历任意 JSON 结构，Any 是正确类型
    """递归脱敏：键名（大小写/连字符/下划线不敏感）命中敏感集合的值替换为 ***。"""
    if isinstance(obj, Mapping):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            normalized = str(k).lower().replace("-", "").replace("_", "")
            out[str(k)] = REDACTED if normalized in SENSITIVE_KEYS else redact(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(item) for item in obj]
    return obj


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": current_trace_id(),
        }
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _STD_ATTRS and not k.startswith("_")
        }
        payload.update(redact(extras))
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    """幂等装配 root logger 的 JSON 输出。"""
    root = logging.getLogger()
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)


class TraceIdMiddleware(BaseHTTPMiddleware):
    """trace_id 贯穿：透传 X-Trace-Id 或生成新值；回写响应头；记录访问日志。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        trace_id = request.headers.get(TRACE_ID_HEADER) or uuid.uuid4().hex
        token = _trace_id_var.set(trace_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            _trace_id_var.reset(token)
        duration_ms = (time.perf_counter() - started) * 1000
        response.headers[TRACE_ID_HEADER] = trace_id
        log = get_logger("app.access")
        extra = {
            "trace_id": trace_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": round(duration_ms, 1),
        }
        if duration_ms > SLOW_REQUEST_S * 1000:
            log.warning("slow request", extra=extra)
        else:
            log.info("request", extra=extra)
        return response
