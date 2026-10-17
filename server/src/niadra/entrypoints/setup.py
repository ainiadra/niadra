"""`serve setup`, the Helm pre-upgrade hook: the migrations of the control, cell and space databases, then
the broker's topology and the bucket's rules. It opens only what it needs, never the cell's services.
"""

import asyncio
import logging
import os

from alembic.config import Config as AlembicConfig
from sqlalchemy.engine import URL

from niadra.adapters.observability import logging as json_logging
from niadra.adapters.outbound.clock import SystemClock
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.postgres.provisioning import AlembicHistory, PgSpaceDatabaseAdmin, upgrade
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory, make_engine
from niadra.adapters.outbound.rabbitmq.connection import RabbitConnection
from niadra.app.fleet import FleetMigrator, FleetReport
from niadra.domain.spaces.backups import BACKUP_PREFIX
from niadra.entrypoints.cell import open_object_store
from niadra.entrypoints.serve import ALEMBIC_INI
from niadra.entrypoints.settings import Settings, control_migration_url

log = logging.getLogger("niadra.serve")

# Staging that erasure and retention do not delete on their own ages out (5.14): abandoned import files.
# Turn recordings have no rule: they are kept until an erasure or the `recording.kept_days` the customer
# sets, which the retention sweep applies. Raw content (`raw/`, `media/`, `measure/raw/`) has no rule
# either: it is kept until an erasure or the `raw_content` retention the customer sets (`app.privacy`).
# `exports/` holds decrypted subject packages: the retention sweep deletes them after a day, and the
# bucket rule is the backstop if the sweep never runs.
OBJECT_EXPIRY_DAYS = {
    "imports/": 2,
    "exports/": 2,
    # A standalone interaction batch waits in `measure/intake/` only until its worker takes it (24.5.1).
    "measure/intake/": 2,
    # A BI file waits for its reconciliation, which deletes it once read.
    "measure/bi/": 2,
}


def migrate(history: str, url: str | URL) -> None:
    """One Alembic history, as the schema owner on the direct endpoint. Synchronous: Alembic's env
    runs its own event loop."""
    config = AlembicConfig(str(ALEMBIC_INI), ini_section=history)
    config.attributes["url"] = url
    upgrade(config, "head")


async def migrate_fleet(settings: Settings) -> FleetReport:
    """Every space database to the head of the space history, in waves (8.2)."""
    servers = settings.servers
    owner = servers.cell_url("owner", direct=True)
    cell_engine = make_engine(owner, pool_size=1, application_name="niadra-setup")
    admin = PgSpaceDatabaseAdmin(servers, AlembicHistory(ALEMBIC_INI))
    # Directory rows only: this process never seals a value, so its vault holds a throwaway key in memory.
    vault = LocalKeyVault(os.urandom(32))
    uow = PgUnitOfWorkFactory(vault, EngineRegistry(servers, cell_engine), cell_engine)
    try:
        migrator = FleetMigrator(uow, admin, SystemClock(), concurrency=settings.operations.setup_concurrency)
        return await migrator.run()
    finally:
        await admin.close()
        await cell_engine.dispose()


async def setup(settings: Settings) -> None:
    """Idempotent tasks after the migrations: broker topology and the bucket's expiry rules.

    It runs before the control plane is up, so it opens only what it needs instead of `open_infra`.
    """
    rabbit = RabbitConnection(settings.rabbitmq_url)
    await rabbit.open()  # declares the queues and the dead-letter exchange
    try:
        async with open_object_store(settings.object_storage) as store:
            await store.ensure_bucket()
            expiry = {**OBJECT_EXPIRY_DAYS, BACKUP_PREFIX: settings.operations.backup_retention_days}
            await store.ensure_lifecycle(expiry)
    finally:
        await rabbit.close()
    log.info("cell setup done")


def run_setup(settings: Settings) -> int:
    """The Helm pre-upgrade hook: control, cell, then the space fleet. It fails closed: any failure exits
    non-zero, the hook fails, and the running release keeps serving (8.2)."""
    migrate("control", control_migration_url())
    migrate("cell", settings.servers.cell_url("owner", direct=True))
    report = asyncio.run(migrate_fleet(settings))
    summary = {
        "head": report.head,
        "waves": report.waves,
        "upgraded": len(report.upgraded),
        "failed": report.failed,
        "unfinished": report.unfinished,
    }
    if not report.ok:
        log.error("space fleet migration failed; the upgrade stops here", extra=summary)
        return 1
    log.info("space fleet migrated", extra=summary)
    asyncio.run(setup(settings))
    return 0


def main() -> int:
    settings = Settings.from_env()
    json_logging.configure(settings.log_level)
    return run_setup(settings)
