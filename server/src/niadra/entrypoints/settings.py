"""Process configuration from environment variables. Nothing here is read at import time.

Databases (one per space, plus the cell database and the control database, on one PostgreSQL):
    NIADRA_DB_HOST, NIADRA_DB_PORT, NIADRA_DB_SSLMODE          the pooled endpoint (PgBouncer in a cluster)
    NIADRA_DB_DIRECT_HOST, _PORT, _SSLMODE                    the instance itself, for the schema owner;
                                                               the pooled endpoint when unset
    NIADRA_DB_APP_PASSWORD, _SYSTEM_PASSWORD, _OWNER_PASSWORD  one per role (the owner's: setup, loops, ops)
    NIADRA_APP_DATABASE_URL, _SYSTEM_, _OWNER_                 a whole URL per role instead (development)
    NIADRA_CELL_DATABASE, NIADRA_CONTROL_DATABASE             niadra_cell, niadra_control
    NIADRA_PGBOUNCER_ADMIN                                    host:port of PgBouncer's admin console
    NIADRA_DB_INSTANCES                                       names of instances of their own (tiers T1, T2),
                                                               each with NIADRA_DB_INSTANCE_<NAME>_HOST,
                                                               _PORT, _DIRECT_HOST
"""

import os
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.engine import make_url

from niadra.adapters.outbound.postgres.registry import PoolSettings
from niadra.adapters.outbound.postgres.servers import Endpoint, Servers


def _semantic_channel(value: str) -> str:
    if value not in ("off", "models"):
        raise RuntimeError("NIADRA_SEMANTIC_CHANNEL is off or models")
    return value


def _names(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(v.strip() for v in value.split(",") if v.strip()))


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"missing environment variable {name}")
    return value


def _optional(name: str) -> str | None:
    return os.environ.get(name) or None


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _endpoint(prefix: str, fallback: Endpoint | None = None) -> Endpoint:
    host = _optional(f"{prefix}_HOST")
    if host is None and fallback is not None:
        return fallback
    # A managed PostgreSQL (Aurora, Cloud SQL...) is reached over TLS; `require` refuses plaintext.
    return Endpoint(host or "postgres", int(_env(f"{prefix}_PORT", "5432")), _optional(f"{prefix}_SSLMODE"))


def servers_from_env() -> Servers:
    pooled = _endpoint("NIADRA_DB")
    direct = _endpoint("NIADRA_DB_DIRECT", fallback=pooled)
    instances = {}
    for name in filter(None, (n.strip().lower() for n in _env("NIADRA_DB_INSTANCES", "").split(","))):
        prefix = f"NIADRA_DB_INSTANCE_{name.upper()}"
        own = _endpoint(prefix)
        instances[name] = (own, _endpoint(f"{prefix}_DIRECT", fallback=own))
    admin = _optional("NIADRA_PGBOUNCER_ADMIN")
    pooler = None
    if admin:
        host, _, port = admin.rpartition(":")
        pooler = Endpoint(host, int(port))
    return Servers(
        pooled=pooled,
        direct=direct,
        passwords={
            "app": _optional("NIADRA_DB_APP_PASSWORD"),
            "system": _optional("NIADRA_DB_SYSTEM_PASSWORD"),
            "owner": _optional("NIADRA_DB_OWNER_PASSWORD"),
        },
        cell_database=_env("NIADRA_CELL_DATABASE", "niadra_cell"),
        app_login=_env("NIADRA_DB_APP_LOGIN", "niadra_app"),
        instances=instances,
        pooler_admin=pooler,
        overrides={
            role: make_url(url)
            for role in ("app", "system", "owner")
            if (url := _optional(f"NIADRA_{role.upper()}_DATABASE_URL"))
        },
    )


def control_database_url() -> str:
    """The control plane's database as the owner through the pooled endpoint: the only setting the
    control deployable needs from here. Migrations use `control_migration_url`."""
    explicit = _optional("NIADRA_CONTROL_DATABASE_URL")
    if explicit:
        return explicit
    url = servers_from_env().url("owner", _env("NIADRA_CONTROL_DATABASE", "niadra_control"))
    return url.render_as_string(hide_password=False)


def control_migration_url() -> str:
    explicit = _optional("NIADRA_CONTROL_DATABASE_URL")
    if explicit:
        return explicit
    url = servers_from_env().url("owner", _env("NIADRA_CONTROL_DATABASE", "niadra_control"), direct=True)
    return url.render_as_string(hide_password=False)


@dataclass(frozen=True)
class ObjectStorage:
    """S3-protocol storage. Without an endpoint it is AWS S3; without keys the SDK's default
    credential chain applies (an instance or pod role), so no long-lived key has to exist."""

    bucket: str
    region: str
    endpoint_url: str | None = None
    public_endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None

    @classmethod
    def from_env(cls, region: str) -> "ObjectStorage":
        return cls(
            bucket=_env("NIADRA_S3_BUCKET"),
            region=_env("NIADRA_S3_REGION", region),
            endpoint_url=_optional("NIADRA_S3_ENDPOINT"),
            public_endpoint_url=_optional("NIADRA_S3_PUBLIC_ENDPOINT"),
            access_key_id=_optional("NIADRA_S3_ACCESS_KEY_ID"),
            secret_access_key=_optional("NIADRA_S3_SECRET_ACCESS_KEY"),
        )


@dataclass(frozen=True)
class Operations:
    """Knobs of the database fleet: migration and loop concurrency, dumps and their retention."""

    setup_concurrency: int = 2
    loops_concurrency: int = 4
    backup_max_db_gib: int = 50
    backup_retention_days: int = 35
    instance_backup_retention: timedelta = timedelta(days=1)

    @classmethod
    def from_env(cls) -> "Operations":
        return cls(
            setup_concurrency=int(_env("NIADRA_SETUP_CONCURRENCY", "2")),
            loops_concurrency=int(_env("NIADRA_LOOPS_CONCURRENCY", "4")),
            backup_max_db_gib=int(_env("NIADRA_BACKUP_MAX_DB_GIB", "50")),
            backup_retention_days=int(_env("NIADRA_BACKUP_RETENTION_DAYS", "35")),
            instance_backup_retention=timedelta(days=int(_env("NIADRA_INSTANCE_BACKUP_RETENTION_DAYS", "1"))),
        )


@dataclass(frozen=True)
class Settings:
    cell_id: str
    region: str
    log_level: str
    servers: Servers
    pools: PoolSettings
    operations: Operations
    rabbitmq_url: str
    redis_url: str
    object_storage: ObjectStorage
    kek_source: str
    key_pepper: bytes
    subject_token_secret: bytes
    cell_token: str
    models_url: str
    control_url: str
    llm_api_key: str | None
    llm_base_url: str | None
    allowed_origins: tuple[str, ...]
    kms_key_id: str | None = None
    """The master key in AWS KMS (an id, ARN or alias); unset keeps the local KEK (5.14.1)."""
    kms_endpoint: str | None = None
    state_redis_url: str | None = None
    """The store for state whose loss changes what an agent sees: the hot layer, key revocations, rate and
    daily limits. It never evicts (`noeviction`), while `redis_url` holds rebuildable caches under LRU
    eviction, so a full cache never evicts a live turn. Unset keeps everything on `redis_url`."""
    audit_bucket: str | None = None
    """A bucket created with Object Lock for the daily roots of the receipt chains; unset keeps them in
    the cell's bucket (5.13)."""
    audit_retention_days: int = 5 * 365
    llm_model: str | None = None
    llm_provider_order: tuple[str, ...] = ()
    """Providers a routing endpoint tries first for the extraction model (`provider.order`); empty leaves it
    to the router."""
    llm_zero_retention: bool = False
    llm_include_usage: bool = False
    retrieval_cache_mib: int = 128
    """The read process's per-customer retrieval cache, in MiB. models/README.md has what 1,000 customers
    cost."""
    semantic_channel: str = "off"
    """`off` or `models` (the question goes to the models pod). Off by default: models/README.md."""
    semantic_deadline_ms: float = 30.0
    budget_spread_s: float = 7200.0
    """Extraction that waits for the daily LLM budget comes back spread over this many seconds after
    00:00 UTC, each task at its own fixed offset, instead of all at midnight."""
    decisions_base_url: str | None = None
    """Where the decision model of the decisions port is asked; unset, its tasks take their conservative
    label. The local end-to-end check points it at an address that never answers, to prove the cell works
    with the decision model down."""
    decisions_model: str | None = None
    extract_batch_window_s: float = 8.0
    """How long a closed session waits for its customer's other sessions before extraction:
    the sessions that close together go to the models in one call. 0 extracts each one at once."""
    large_bodies: int = 2
    """How many request bodies over `large_body_bytes`, sent or decoded, a process holds at once (turn records
    and OTLP, up to 16 MB decoded); the next answers 429 with `Retry-After`."""
    large_body_bytes: int = 1024 * 1024
    prometheus_url: str | None = None
    """The cell's Prometheus, which the admin process asks for the Console's latency card; unset, the card
    says the metrics are unavailable."""

    @classmethod
    def from_env(cls) -> "Settings":
        region = _env("NIADRA_REGION", "us-east-2")
        # The Console's origins; none means no browser may call the cell.
        origins = _env("NIADRA_ALLOWED_ORIGINS", "")
        return cls(
            cell_id=_env("NIADRA_CELL_ID", "sandbox-1"),
            region=region,
            log_level=_env("NIADRA_LOG_LEVEL", "INFO"),
            servers=servers_from_env(),
            pools=PoolSettings(
                pool_size=int(_env("NIADRA_DB_SPACE_POOL_SIZE", "1")),
                max_overflow=int(_env("NIADRA_DB_SPACE_MAX_OVERFLOW", "2")),
            ),
            operations=Operations.from_env(),
            rabbitmq_url=_env("NIADRA_RABBITMQ_URL"),
            redis_url=_env("NIADRA_REDIS_URL", "redis://valkey:6379/0"),
            object_storage=ObjectStorage.from_env(region),
            # A file path (a mounted secret) or `env:NAME`.
            kek_source=_env("NIADRA_KEK", "/run/secrets/kek.bin"),
            key_pepper=_env("NIADRA_KEY_PEPPER").encode(),
            subject_token_secret=_env("NIADRA_SUBJECT_TOKEN_SECRET").encode(),
            cell_token=_env("NIADRA_CELL_TOKEN"),
            models_url=_env("NIADRA_MODELS_URL", "http://models:8080"),
            control_url=_env("NIADRA_CONTROL_URL", "http://control:8000"),
            llm_api_key=_optional("NIADRA_LLM_API_KEY"),
            llm_base_url=_optional("NIADRA_LLM_BASE_URL"),
            llm_model=_optional("NIADRA_LLM_MODEL"),
            allowed_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
            kms_key_id=_optional("NIADRA_KMS_KEY_ID"),
            kms_endpoint=_optional("NIADRA_KMS_ENDPOINT"),
            state_redis_url=_optional("NIADRA_STATE_REDIS_URL"),
            audit_bucket=_optional("NIADRA_AUDIT_BUCKET"),
            audit_retention_days=int(_env("NIADRA_AUDIT_RETENTION_DAYS", str(5 * 365))),
            llm_provider_order=_names(_env("NIADRA_LLM_PROVIDER_ORDER", "")),
            llm_zero_retention=_flag("NIADRA_LLM_ZERO_RETENTION"),
            llm_include_usage=_flag("NIADRA_LLM_INCLUDE_USAGE"),
            retrieval_cache_mib=int(_env("NIADRA_RETRIEVAL_CACHE_MIB", "128")),
            semantic_channel=_semantic_channel(_env("NIADRA_SEMANTIC_CHANNEL", "off")),
            semantic_deadline_ms=float(_env("NIADRA_SEMANTIC_DEADLINE_MS", "30")),
            budget_spread_s=float(_env("NIADRA_BUDGET_SPREAD_S", "7200")),
            decisions_base_url=_optional("NIADRA_DECISIONS_BASE_URL"),
            decisions_model=_optional("NIADRA_DECISIONS_MODEL"),
            prometheus_url=_optional("NIADRA_PROMETHEUS_URL"),
            extract_batch_window_s=float(_env("NIADRA_EXTRACT_BATCH_WINDOW_S", "8")),
            large_bodies=int(_env("NIADRA_LARGE_BODIES", "2")),
            large_body_bytes=int(_env("NIADRA_LARGE_BODY_BYTES", str(1024 * 1024))),
        )
