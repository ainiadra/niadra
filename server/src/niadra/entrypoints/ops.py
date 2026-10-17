"""Operator tooling for the databases of a cell: `python -m niadra.entrypoints.ops <command>`.

    backup [--verify-weekday N]          dump the cell database and every serving space database to the
                                         bucket (backups/<db>/<YYYYMMDDTHHMM>.dump and latest.json); on
                                         weekday N (Python's weekday(): 0 Monday, 6 Sunday) also restore
                                         each latest dump into a scratch database and count its rows
    verify-backup --db NAME              that check for one database
    queue-replay [--space ID] [--task NAME] [--since ISO] [--limit N]
                                         send dead tasks back to the broker with fresh attempts: a failed
                                         erasure resumes where it stopped (each step is idempotent)
    restore-space --space ID [--from KEY] [--swap]
                                         restore a space's dump beside its database; with --swap, put it
                                         in place (503 for a few seconds) and keep the old one for 7 days

The nightly CronJob runs `backup`; the others are runbooks (README.md). Everything runs as the
schema owner on the direct endpoint, so the process needs NIADRA_DB_OWNER_PASSWORD.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from niadra.adapters.observability import logging as json_logging
from niadra.adapters.outbound.clock import SystemClock
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.postgres.provisioning import AlembicHistory, PgSpaceDatabaseAdmin
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory, make_engine
from niadra.adapters.outbound.rabbitmq.connection import RabbitConnection
from niadra.adapters.outbound.rabbitmq.tasks import RabbitTaskBus
from niadra.adapters.outbound.redis.cache import RedisCache
from niadra.app.backups import GIB, BackupService
from niadra.app.pipeline import PipelineService
from niadra.domain.tasks import TaskName
from niadra.entrypoints.cell import open_keys, open_object_store, open_redis
from niadra.entrypoints.serve import ALEMBIC_INI
from niadra.entrypoints.settings import Settings

log = logging.getLogger("niadra.ops")


@dataclass
class Tools:
    settings: Settings
    backups: BackupService
    clock: SystemClock


@asynccontextmanager
async def open_tools(settings: Settings) -> AsyncIterator[Tools]:
    async with AsyncExitStack() as stack:
        servers = settings.servers
        # The owner on the direct endpoint: it writes the directory during a restore.
        cell_engine = make_engine(
            servers.cell_url("owner", direct=True), pool_size=1, application_name="niadra-ops"
        )
        stack.push_async_callback(cell_engine.dispose)
        registry = EngineRegistry(servers, cell_engine)
        stack.push_async_callback(registry.close)
        # The same master key as every cell process (KMS when configured): a data key this tool wraps is
        # readable by them, and it opens what they sealed, such as the erasure ledger a restore reads.
        vault, _ = await open_keys(settings, cell_engine, stack)
        admin = PgSpaceDatabaseAdmin(servers, AlembicHistory(ALEMBIC_INI))
        stack.push_async_callback(admin.close)
        objects = await stack.enter_async_context(open_object_store(settings.object_storage))
        # A restore purges the space's live turns too, which live in the state store when there is one.
        redis, state_redis = open_redis(settings, stack)
        clock = SystemClock()
        backups = BackupService(
            PgUnitOfWorkFactory(vault, registry, cell_engine),
            admin,
            objects,
            clock,
            cell_database=servers.cell_database,
            max_db_bytes=settings.operations.backup_max_db_gib * GIB,
            databases=registry,
            cache=RedisCache(redis, also_purged=(state_redis,) if state_redis is not redis else ()),
            keys=vault,
        )
        yield Tools(settings, backups, clock)


async def backup(tools: Tools, verify_weekday: int | None) -> int:
    report = await tools.backups.backup_all()
    for name, info in sorted(report.dumped.items()):
        log.info("dumped", extra={"database": name, "key": info.key, "bytes": info.size_bytes})
    for name, why in sorted(report.skipped.items()):
        log.warning("not dumped", extra={"database": name, "reason": why})
    failed = dict(report.failed)
    if verify_weekday is not None and tools.clock.now().weekday() == verify_weekday:
        for name in sorted(report.dumped):
            try:
                checked = await tools.backups.verify(name)
            except Exception as exc:
                log.exception("dump verification failed", extra={"database": name})
                failed[name] = f"verify: {type(exc).__name__}: {exc}"[:500]
            else:
                log.info("dump verified", extra={"database": name, "counts": checked.counts})
    for name, why in sorted(failed.items()):
        log.error("backup failed", extra={"database": name, "error": why})
    return 1 if failed else 0


async def verify_backup(tools: Tools, db_name: str) -> int:
    checked = await tools.backups.verify(db_name)
    sys.stdout.write(
        json.dumps({"database": checked.db_name, "key": checked.key, "counts": checked.counts}) + "\n"
    )
    return 0


async def restore_space(tools: Tools, space_id: UUID, key: str | None, swap: bool) -> int:
    done = await tools.backups.restore_space(space_id, key, swap=swap)
    sys.stdout.write(
        json.dumps(
            {
                "space_id": str(done.space_id),
                "database": done.restored_as,
                "from": done.key,
                "schema_version": done.schema_version,
                "swapped": done.swapped,
                "retired": done.retired,
            }
        )
        + "\n"
    )
    return 0


async def queue_replay(
    settings: Settings, space: UUID | None, task: str | None, since: datetime | None, limit: int
) -> int:
    """Needs the broker and the cell database only, as the system role, like the processes."""
    engine = make_engine(settings.servers.cell_url("system"), pool_size=1, application_name="niadra-ops")
    rabbit = RabbitConnection(settings.rabbitmq_url)
    await rabbit.open()
    try:
        vault = LocalKeyVault(os.urandom(32))  # the cell database's dead tasks carry no sealed value
        uow = PgUnitOfWorkFactory(vault, EngineRegistry(settings.servers, engine), engine)
        pipeline = PipelineService(uow, RabbitTaskBus(rabbit), SystemClock())
        replayed = await pipeline.requeue_dead(space, TaskName(task) if task else None, since, limit)
    finally:
        await rabbit.close()
        await engine.dispose()
    sys.stdout.write(json.dumps({"replayed": replayed}) + "\n")
    return 0


async def run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    json_logging.configure(settings.log_level)
    if args.command == "queue-replay":
        return await queue_replay(settings, args.space, args.task, args.since, args.limit)
    async with open_tools(settings) as tools:
        if args.command == "backup":
            return await backup(tools, args.verify_weekday)
        if args.command == "verify-backup":
            return await verify_backup(tools, args.db)
        return await restore_space(tools, args.space, args.source, args.swap)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m niadra.entrypoints.ops")
    commands = parser.add_subparsers(dest="command", required=True)
    dump = commands.add_parser("backup", help="dump every database to the bucket")
    dump.add_argument(
        "--verify-weekday",
        type=int,
        choices=range(7),
        help="on this weekday (0 Monday, 6 Sunday) also verify",
    )
    check = commands.add_parser("verify-backup", help="restore the latest dump into a scratch database")
    check.add_argument("--db", required=True, help="database name, like s_<space hex> or niadra_cell")
    replay = commands.add_parser("queue-replay", help="send dead tasks back with fresh attempts")
    replay.add_argument("--space", type=UUID, help="only this space's tasks")
    replay.add_argument("--task", choices=[t.value for t in TaskName], help="only tasks of this name")
    replay.add_argument("--since", type=datetime.fromisoformat, help="only tasks buried since (ISO 8601)")
    replay.add_argument("--limit", type=int, default=1000)
    restore = commands.add_parser("restore-space", help="restore one space from a dump")
    restore.add_argument("--space", type=UUID, required=True)
    restore.add_argument("--from", dest="source", help="the dump's key in the bucket; the latest when absent")
    restore.add_argument("--swap", action="store_true", help="put the restored database in place")
    sys.exit(asyncio.run(run(parser.parse_args(argv))))


if __name__ == "__main__":
    main()
