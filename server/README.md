# server

The Niadra cell and its control plane: the service that takes the events from every channel, platform and
system, builds one memory of each customer and delivers it to any agent. One Python codebase
(`src/niadra`), seven deployables, one image. `models/` is the in-cell inference server (embeddings, PII
spans, a pair classifier, text in images; no language model), with its own `pyproject.toml` and image.

Related components of the monorepo: [../deploy](../deploy) (Helm chart, scripts, local end-to-end check),
[../spec](../spec) (the open specs and test vectors the server is checked against), [../docs](../docs).

## Deployables

| Deployable | What it serves |
|---|---|
| `ingest` | `POST /v1/batch`, inbound webhooks, file imports, media uploads, feedback |
| `read` | `context()`, history navigation, object reads, subject tokens, MCP |
| `admin` | the governance API behind the Console: identity, receipts, privacy, measurement, rules |
| `workers` | the background tasks of one or more queue classes (`--pools extraction,measure`), scaled on queue depth |
| `loops` | the single-instance cell loops: configuration refresh, the space database provisioner, per-space relay, schedules and sweeps, dead letters, audit writer |
| `insights` | analytical reads and the analytical MCP |
| `control` | the control plane: tenants, sources and keys, people and roles, configuration |

```
python -m niadra.entrypoints.serve <ingest|read|admin|workers|loops|insights|control|setup>
python -m niadra.entrypoints.ops <backup|verify-backup|queue-replay|restore-space>
```

`setup` migrates the control and cell databases and every space database, declares the broker topology,
and creates the bucket and its expiry rules. `ops` is the database tooling: nightly dumps, their check, and
restoring one space.

## Checks

The same steps the `server` workflow runs (`../.github/workflows/server.yml`):

```
uv sync --frozen
uv run scripts/audit.py                                # pip-audit over every locked package
uv run ruff check src tests migrations
uv run ruff format --check src tests migrations
uv run mypy
uv run lint-imports                                    # the layer contracts in pyproject.toml
uv run pytest -o addopts="" -q                         # unit tests; integration tests skip without a database
```

Integration tests need a PostgreSQL 17 server (and `pg_dump`/`pg_restore` 17 on the `PATH` for the backup
and restore tests):

```
export NIADRA_TEST_PGPORT=54499 NIADRA_TEST_PGDATA=$TMPDIR/niadra-pg
NIADRA_TEST_DSN=$(scripts/test-db.sh start) uv run pytest -o addopts="" -q
scripts/test-db.sh stop
```

They create the cell's roles and many databases (`s_*`) on that server, and refuse to run where a
`niadra_cell` or `niadra_control` database exists. `NIADRA_TEST_PGDATA` must be a short path (the unix
socket's path is limited to 103 bytes). The tests over the production adapters (some unit ones too) read
`NIADRA_TEST_REDIS` (a Redis URL), `NIADRA_TEST_RABBITMQ` (an AMQP URL) and `NIADRA_TEST_PGBOUNCER`
(`host:port` of a PgBouncer in transaction mode in front of the test server); each skips without its
variable.

Migrations must match the models, for each of the three histories:

```
export NIADRA_SPACE_DATABASE_URL=postgresql://postgres@localhost:5432/ci_space
uv run alembic --name space upgrade head && uv run alembic --name space check     # likewise control, cell
```

The OpenAPI documents in `openapi/` are build artifacts for the docs site and the SDKs; a test fails when
they fall behind the code. Regenerate them with
`uv run python -m niadra.entrypoints.openapi --target <cell|control|space-config> --out openapi/<target>.json`.

The models server has its own checks: `cd models && uv sync && uv run pytest -q`.

## Running it locally

A cell needs PostgreSQL 17, RabbitMQ 4, a Redis-compatible store (Valkey or Redis), S3-protocol object
storage and the models server. `../deploy/scripts/local-e2e.sh <server checkout> --keep` starts all of it
on one computer from Homebrew services, runs every deployable as production runs it and leaves the API at
`127.0.0.1:19709`; its header lists what it needs.

To run the processes by hand, export the configuration below, then:

```
uv run python -m niadra.entrypoints.serve setup        # once: migrations, broker topology, bucket
uv run python -m niadra.entrypoints.serve control
uv run python -m niadra.entrypoints.serve loops
uv run python -m niadra.entrypoints.serve read         # likewise ingest, admin, insights
uv run python -m niadra.entrypoints.serve workers --pools extraction,measure
```

## Configuration

Every setting is an environment variable; `src/niadra/entrypoints/settings.py` (the cell) and
`src/niadra/entrypoints/control.py` (the control plane) are the complete lists.

### The AI endpoints

Extraction, the configuration assistant and the decision tasks call external model endpoints. No endpoint
and no model id is built in: they come only from the environment. Without them the cell still runs:
extraction and the assistant stay queued until an endpoint is configured, and every decision task takes
its conservative label.

| Variable | Meaning |
|---|---|
| `NIADRA_LLM_API_KEY` | Bearer key sent to both endpoints. Unset: no model is called. |
| `NIADRA_LLM_BASE_URL` | Root of an OpenAI-compatible Chat Completions endpoint; requests go to `<base>/chat/completions` with `response_format: json_schema` and `reasoning.effort`. |
| `NIADRA_LLM_MODEL` | The extraction model's id, as that endpoint names it. |
| `NIADRA_LLM_PROVIDER_ORDER` | Optional, comma-separated: providers a routing endpoint tries first (`provider.order`). |
| `NIADRA_LLM_ZERO_RETENTION` | Optional, `true`: asks a routing endpoint to deny data collection and use zero-retention providers only (`provider.data_collection`, `provider.zdr`), on both endpoints. Off by default. |
| `NIADRA_LLM_INCLUDE_USAGE` | Optional, `true`: asks the endpoint to report each call's cost (`usage.include`), which the daily AI budget counts. Off by default; without a reported cost the budget counts nothing. |
| `NIADRA_DECISIONS_BASE_URL` | Root of the decisions endpoint: typed questions about a `state`, answered with probabilities; requests go to `<base>/alpha/decisions`. |
| `NIADRA_DECISIONS_MODEL` | The decision model's id. |

### The cell

| Variable | Meaning |
|---|---|
| `NIADRA_DB_HOST`, `NIADRA_DB_PORT`, `NIADRA_DB_SSLMODE` | The pooled PostgreSQL endpoint (PgBouncer in a cluster). `NIADRA_DB_DIRECT_*` is the instance itself, for the schema owner. |
| `NIADRA_DB_APP_PASSWORD`, `NIADRA_DB_SYSTEM_PASSWORD`, `NIADRA_DB_OWNER_PASSWORD` | One per role; or a whole URL per role in `NIADRA_APP_DATABASE_URL`, `NIADRA_SYSTEM_DATABASE_URL`, `NIADRA_OWNER_DATABASE_URL`. |
| `NIADRA_RABBITMQ_URL` | The broker (required). |
| `NIADRA_REDIS_URL`, `NIADRA_STATE_REDIS_URL` | The evicting cache and the non-evicting state store; the state store defaults to the cache. |
| `NIADRA_S3_BUCKET`, `NIADRA_S3_ENDPOINT`, `NIADRA_S3_ACCESS_KEY_ID`, `NIADRA_S3_SECRET_ACCESS_KEY` | Object storage; without an endpoint it is AWS S3, without keys the SDK's default credentials. |
| `NIADRA_KEK` | The local key-encryption key: a file path or `env:NAME`. `NIADRA_KMS_KEY_ID` uses AWS KMS instead. |
| `NIADRA_KEY_PEPPER`, `NIADRA_SUBJECT_TOKEN_SECRET`, `NIADRA_CELL_TOKEN` | Secrets (required); the pepper and the cell token are shared with the control plane. |
| `NIADRA_CELL_ID`, `NIADRA_REGION` | The cell's name and region. |
| `NIADRA_CONTROL_URL`, `NIADRA_MODELS_URL` | The control plane and the models server. |
| `NIADRA_ALLOWED_ORIGINS` | The Console's origins; none means no browser may call the cell. |

## Databases

One PostgreSQL holds three kinds of database:

| Database | What it holds | History |
|---|---|---|
| `niadra_control` | tenants, projects, spaces, people, keys, configuration versions, deletion receipts | `alembic --name control` |
| `niadra_cell` | the cell's own state: the directory of space databases, wrapped data keys, secrets, source keys, the configuration copy, health, dead tasks, receipt watermarks | `alembic --name cell` |
| `s_<space id hex>`, one per space | everything of one customer space: events, identity, memory, packs, receipts, outbox, schedules | `alembic --name space` |

The `loops` process creates a space's database when the control plane's snapshot names a new space (it
answers 503 with `Retry-After: 5` for the few seconds that takes, which the SDKs wait out), drops it when
the snapshot lists the space as removed, and checks every 5 s that the directory, the snapshot and the
server agree (a space missing from a snapshot is an alert, never a deletion). Every process opens a small
connection pool per space database on first use, through PgBouncer, and closes it after two idle minutes.
`serve setup` migrates the fleet in waves before the new pods roll and fails the deploy when a database
fails (migrations in `CODE_GUIDE.md`).

Roles: `niadra_owner` owns the schemas, creates and drops databases and runs migrations and dumps on a
direct connection; `niadra_app` serves requests under row-level security; `niadra_system` runs the cell
database and the maintenance inside a space database. The workers and the loops log in as `niadra_worker`
(`NIADRA_DB_APP_LOGIN`), which becomes `niadra_app` at login: the same privileges and policies, but a pool
of its own in PgBouncer, capped at one of a space database's two server connections, so background work
never makes a batch's acknowledgement wait.

### Runbook: restore one space

The instance snapshots restore the whole cell; a nightly dump restores one space. Run it from a pod with
the server image and the `niadra-owner` secret (the `niadra-backup` CronJob's pod spec is one):

```
python -m niadra.entrypoints.ops restore-space --space <space id>                 # beside the live one
python -m niadra.entrypoints.ops restore-space --space <space id> --from backups/s_<hex>/<time>.dump --swap
```

Without `--swap` the dump is restored into `s_<hex>_r<time>`, checked (stamp, schema brought to head) and
left there to inspect. With `--swap` the space goes into `maintenance` (503 for a few seconds), the live
database is renamed `s_<hex>_old_<time>`, the restored one takes its name, the cache epoch moves, the
space's cache keys are purged and its hot layer rebuilt, and an `admin` receipt with purpose
`space.restore` records the dump in the space's receipt chain. The old database is dropped by the
reconciler after 7 days; until then it can be swapped back the same way.

### Runbook: check a dump

```
python -m niadra.entrypoints.ops verify-backup --db s_<space id hex>     # or --db niadra_cell
```

Restores the latest dump into a scratch database, counts rows of five tables, prints them and drops the
scratch database. The Sunday run of the nightly `backup` does this for every database it dumped.

### Runbook: delete a space

A person with the admin role calls `DELETE /v1/spaces/{space_id}` on the control plane: the space's keys
are revoked at once and the next snapshot lists it as removed. The cell's reconciler then marks it
`deleting` (requests get 503), drops its database, deletes its objects (`raw/`, `media/`, `imports/`,
`audit/`, `archive/events/`, `exports/`), its dumps, secrets, source keys and cache keys, destroys its data
keys last, marks it `deleted` and posts a deletion receipt with its SHA-256 to the control plane, retrying
until it is stored; `GET /v1/spaces/{space_id}/deletion-receipt` shows it. The receipt states when the last
instance snapshot that may still hold the space's (now unreadable) ciphertext expires.

### Runbook: a cell dedicated to one customer

Servers, database and keys of one tenant, shared with no one. `../deploy/scripts/cell.sh up` creates the
cell's machines, database and master key and runs these control plane commands (in the `control` pod:
`python -m niadra.entrypoints.control_cli ...`):

```
register-cell --cell niadra-<name> --address https://niadra-<name>.cells.niadra.com --tenant <slug> \
  --token-sha256 <hex>        the cell, dedicated to the tenant, with the SHA-256 of its own credential
place-project --tenant <slug> --name <name> --slug <project slug> --cell niadra-<name> [--instance <db>]
routes --cell niadra-<name>   the space addresses that must reach it (cell.sh route writes them)
rekey-cell --cell niadra-<name> --token-sha256 <hex>
close-cell --cell niadra-<name>   before its machines go; refused while it serves a space
```

A dedicated cell takes that tenant's spaces and no one else's, and once it exists every new project of the
tenant in the region lands there; when it is full, creating a project fails rather than land on a shared
cell. Its spaces are tier `t2` (an instance of their own in a shared cell is `t1`; `--instance` names one
the cell knows from `NIADRA_DB_INSTANCES`), and the snapshot carries the tier and the instance, so the cell
creates each database there with 32 partitions. The cell reaches the control plane only with its own
credential, which acts for no other cell. Copying a space's data from one cell to another is not built
(`POST /v1/spaces/{space_id}/placement` only flips the map, and refuses a cell dedicated to another
tenant).

## Portability and observability

No code depends on a proprietary cloud service: PostgreSQL 17, RabbitMQ 4, a Redis-compatible cache,
S3-protocol object storage and OpenAI-compatible model endpoints. Each sits behind a port in
`src/niadra/ports/`.

Every process prints JSON logs and serves Prometheus metrics at `/metrics` (HTTP, tasks, dead letters,
outbox lag, lost receipts, AI spend). The read process adds the context read's stages and delivery paths,
its server time by space and the statements each request ran; `Server-Timing` carries the stages after the
total. A request gets a log line only when it is slow (context over 50 ms, batch over 80 ms, others over
250 ms) or failed (5xx, 429): ids, route template, times and counts, never a subject or a body.

Read `CODE_GUIDE.md` before changing code (layers, module ownership, migrations, tests).
