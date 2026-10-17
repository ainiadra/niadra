# One image for every Python deployable (ingest, read, admin, workers, insights, control) and for the
# database tooling (`python -m niadra.entrypoints.ops backup`).
# The entrypoint picks the deployable: `niadra-serve ingest`, `niadra-serve workers --pools all`...
FROM python:3.13-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

FROM python:3.13-slim
# pg_dump and pg_restore of the database's major version, for the nightly dumps and space restores
# (`python -m niadra.entrypoints.ops`); Debian's own client would be older than the server.
RUN apt-get update -q \
 && apt-get install -yq --no-install-recommends ca-certificates curl \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
 && . /etc/os-release \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update -q \
 && apt-get install -yq --no-install-recommends postgresql-client-17 \
 && apt-get purge -yq curl && apt-get autoremove -yq \
 && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 10001 --home /app niadra
WORKDIR /app
COPY --from=build --chown=niadra:niadra /app /app
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# Numeric, so Kubernetes can check runAsNonRoot without reading /etc/passwd.
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "-m", "niadra.entrypoints.serve"]
