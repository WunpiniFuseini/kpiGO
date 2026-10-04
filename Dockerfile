# kpiGo application image. One image runs the app, worker and beat services.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir "uv>=0.8,<0.9"

RUN groupadd --system kpigo && useradd --system --gid kpigo --home /app kpigo
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

# --- dev: adds the test toolchain; used by CI's no-inference boot gate -----
FROM base AS dev
RUN uv sync --frozen
USER kpigo
CMD ["uvicorn", "kpigo.asgi:application", "--host", "0.0.0.0", "--port", "8000"]

# --- runtime: what ships in the offline bundle ------------------------------
FROM base AS runtime
USER kpigo
EXPOSE 8000
CMD ["uvicorn", "kpigo.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
