# syntax=docker/dockerfile:1
# 说明：基础镜像用 python:3.11-slim（蓝图基线 3.11+；本机/内网镜像源可达，避免外网拉取超时）
FROM docker.m.daocloud.io/library/python:3.11-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON=3.11
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY scripts ./scripts

FROM docker.m.daocloud.io/library/python:3.11-slim
WORKDIR /app
RUN adduser --system --uid 1001 botchain
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    DUCKDB_PATH=/app/data/botchain.duckdb
RUN mkdir -p /app/data && chown -R botchain /app
USER botchain
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/v1/chain/info', timeout=4)" || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
