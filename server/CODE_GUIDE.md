# How this codebase is organized

The rules for day-to-day work on the server: layers, module ownership, migrations, tests and style.
Section numbers in older comments, like `(5.5)`, point at a design document that is not part of this
repository; read the code and its tests as the reference.

## Portability is a rule

No code depends on a proprietary cloud service. The cell runs on PostgreSQL, RabbitMQ, a
Redis-compatible cache, S3-protocol object storage and OpenAI-compatible model endpoints, named only
by the environment (`README.md`, Configuration). Every one of them sits behind a port in
`niadra/ports/`. Docker and Kubernetes are the deployment targets.

## Layers

```
entrypoints  ->  adapters  ->  app  ->  ports  ->  config / contracts  ->  domain
```

- `domain/` is pure: dataclasses and functions, no I/O, no framework. Business rules live here
  and are tested without mocks.
- `contracts/` holds the public API models (Pydantic). They generate the OpenAPI that the SDKs,
  the Console and the specs are tested against. Identifiers are ASCII English.
- `config/` holds the signed configuration documents a cell receives.
- `ports/` declares what the app needs from the outside world (`typing.Protocol`).
  `ports/uow.py` is the transaction; `ports/repos/<module>.py` is each module's persistence.
- `app/<module>.py` holds the use cases of one module as a service class,
  `<Module>Service`, constructed with only the ports it uses.
- `adapters/` implements ports (`outbound/postgres/<module>.py`, `outbound/redis/...`,
  `memory/<module>.py`) and drives the app (`inbound/http/<group>.py`, `inbound/workers/`).
- `entrypoints/` wires one deployable: `ingest`, `read`, `admin`, `workers`, `insights`, `control`.

`import-linter` enforces the arrows in CI.

## Databases

One database per space (`s_<space id hex>`), one cell database (`niadra_cell`) and the control plane's
own (`niadra_control`), on the same PostgreSQL for now. `schema/__init__.py` lists which table lives
where (`CELL_TABLES`, `SPACE_TABLES`); a unit test keeps that list equal to the models. The cell
database holds the space directory (`space_databases`), wrapped data keys, secrets, source keys, the
configuration copy, health, dead tasks and receipt watermarks; everything of a customer lives in the
space's own database, which also carries a stamp (`space_identity`) checked on first use.

## Module ownership

Each table in `adapters/outbound/postgres/schema/` has one owner module, and only its repository
touches it. Code that crosses modules goes through the other module's port. The SQLAlchemy models
are the schema; a change adds an Alembic revision to the history of the database the table lives in
(`alembic --name space|cell|control revision -m "<module>: ..."`), never edits an applied one, and
`alembic --name <history> check` must report no drift (CI checks all three).

## Migrations

Each history starts at one baseline revision (`*_baseline.py`) that creates the whole schema; a change adds
a revision on top of it. `serve setup` migrates the control and cell databases, then every space database in
waves (sandboxes, small production spaces, large ones), before the new pods roll.

- A deploy may take downtime: a revision may add, drop, rename or tighten in one step, and the code of the
  release it ships with is the only code that has to run on it. Nothing keeps the previous release working
  on the new schema, and nothing keeps rows written by an older release readable: when a change makes old
  rows wrong, the revision rewrites or deletes them.
- Indexes on partitioned parents iterate `pg_inherits` or are created on the parent; see the modulus rule.
- A revision that fails rolls back whole: that database keeps its previous revision (status `failed`), the
  rest of its wave goes on, later waves do not start, and setup exits 1, so Helm never rolls the pods.

## Hash partitions: the modulus rule

The memory tables are hash-partitioned, and the number of partitions is per database (8 for a small
space, 32 for a large one; `space_identity.hash_modulus`). No code or migration may name a partition
(`facts_p0`...`facts_p31`): queries hit the parent and rely on pruning, and a migration that adds an
index or a constraint to a partitioned parent iterates `pg_inherits` instead of assuming a count. A
unit test fails on a migration that names one.

## Writing a use case

```python
class IngestService:
    def __init__(self, uow: UnitOfWorkFactory, keys: KeyVault, bus: TaskBus, clock: Clock) -> None: ...

    async def ingest_batch(self, caller: SourcePrincipal, request: BatchRequest) -> BatchResponse:
        async with self._uow(caller.space.space_id) as uow:
            ...  # repositories share this transaction
            await uow.pipeline.enqueue(tasks)  # outbox rows commit with the data
```

Three kinds of transaction, one factory (`ports/uow.py`):

| Call | Database | Role | For |
|---|---|---|---|
| `uow(space_id)` | the space's | `niadra_app`, row-level security on, the space pinned (workers and loops log in as `niadra_worker`, which becomes `niadra_app`) | every use case of a customer |
| `uow.system(space_id)` | the space's | `niadra_system` (BYPASSRLS) | maintenance of that database: relay, scheduler, idle sweep, partitions, dedupe rotation |
| `uow.cell()` | the cell's | `niadra_system` | directory, source keys, health, receipt watermarks, dead tasks, configuration |

- One transaction per use case. The space is always the first argument to the factory.
- A transaction never spans two databases. A repository of the other kind raises
  (`"<repo> lives in the cell database"`). Code that needs both reads the cell first, in its own
  transaction, then opens the space one (the audit anchor reads the watermark that way).
- A space whose database is being created or restored raises `Unavailable` with a retry delay; the
  HTTP adapters answer 503 with `Retry-After`, and loops treat it as a quiet pass.
- Tasks are enqueued through `uow.pipeline`, never published directly; the relay publishes.
- Content is encrypted before it reaches a repository (`KeyVault.encrypt`), with AAD
  `table:row_id:column` and never the profile id.
- Time comes from the `Clock` port. Ids come from `domain.common.ids.uuid7`.

## Errors

Raise the classes in `domain/common/errors.py`. HTTP adapters turn them into
`application/problem+json`. Never put personal data in an error message, a log line or a URL.

## Tests

- `tests/unit/<module>/`: domain functions and services against `niadra.testing.cell.Cell`
  (in-memory unit of work and fakes). Fast, run on every change.
- `tests/integration/`: the PostgreSQL repositories against a real server, one database per space,
  as production. Start one with `scripts/test-db.sh start` and export `NIADRA_TEST_DSN`; these tests
  skip without it. The session creates the cell's roles, a cell database and two space databases
  through the provisioner; `pg_uow(space_id)` clones a fresh migrated database for any other space id,
  so each test keeps its own space. `tests/integration/spaces/` covers the databases themselves:
  isolation, the stamp, provisioning and deletion, restore, the fleet runner, the per-space loops.
- Property tests with Hypothesis for invariants: same input, same bytes; unmerge undoes
  merge; no fact without evidence.

Run everything as `README.md` (Checks) lists it: `uv run pytest -o addopts="" -q`, `uv run ruff check src
tests migrations`, `uv run ruff format --check src tests migrations`, `uv run mypy`, `uv run lint-imports`.

## Language

Code is English: identifiers, file names, comments, docstrings, log lines and test names. Internal
codenames stay out of it; say "the context compiler" or "the context pack". The external models are
"the extraction model" and "the decision model": no vendor or model id is written in the code.
Portuguese appears only in user-facing copy (the `pt` templates) and in test data that plays a customer.

## Comments

Write them for the next engineer, in plain English, one or two lines.

- Explain why, a constraint, a protocol detail or a trap. Do not narrate what the code does.
- No banners, no ALL CAPS labels, no emoji, no "Step 1", no "This function...".
- Docstrings on public classes and functions only when they add something the signature does not.
