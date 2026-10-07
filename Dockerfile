# kpiGo application image. One image runs the app, worker and beat services.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir "uv>=0.8,<0.9"

# The Postgres 16 client: system.backup shells to pg_dump, and restore uses
# pg_restore, so the client must match the server's major (16). Debian bookworm
# ships 15, so pull 16 from the PostgreSQL project's APT repo. Build-time only —
# the fetch happens on the networked build machine, never on the air-gapped host.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc; \
    codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
http://apt.postgresql.org/pub/repos/apt ${codename}-pgdg main" > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends postgresql-client-16; \
    apt-get purge -y --auto-remove curl gnupg; \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --system kpigo && useradd --system --gid kpigo --home /app kpigo \
    && mkdir -p /var/lib/kpigo/drop /var/lib/kpigo/licence /var/lib/kpigo/backups \
    && chown kpigo:kpigo /var/lib/kpigo/drop /var/lib/kpigo/licence /var/lib/kpigo/backups
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
