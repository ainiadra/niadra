"""The `loops` deployable: the cell's single-instance loops. Only this process imports the space database
provisioner and Alembic, and runs the per-space loops of every module.
"""

import asyncio
import contextlib
import random
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from uuid import UUID

from niadra.adapters.inbound.workers import objects as objects_workers
from niadra.adapters.inbound.workers import triggers
from niadra.adapters.inbound.workers.runtime import copy_dead_letters
from niadra.adapters.observability.metrics import PrometheusTelemetry
from niadra.adapters.outbound.controlplane.client import HttpControlClient
from niadra.adapters.outbound.postgres.provisioning import AlembicHistory, PgSpaceDatabaseAdmin
from niadra.adapters.outbound.rabbitmq.connection import DEAD_QUEUE
from niadra.app.experiments import LOOK_HOUR as EXPERIMENTS_NIGHT_HOUR
from niadra.app.extract import extractor_fingerprint, extractor_key
from niadra.app.insights import GAPS_NIGHT_HOUR
from niadra.app.measure import WEIGHTS_EVERY_S, WEIGHTS_HOUR
from niadra.app.memory import (
    BACKFILL_EVERY_S,
    BACKFILL_IDLE_CAP_S,
    NIGHT_HOUR,
    NIGHTLY_EVERY_S,
)
from niadra.app.outcomes import OUTCOMES_EVERY_S, OUTCOMES_IDLE_CAP_S
from niadra.app.provisioning import Provisioner, ProvisioningSettings
from niadra.app.sessions import SWEEP_EVERY_S, SWEEP_IDLE_CAP_S
from niadra.app.signals import NIGHT_HOUR as SIGNALS_NIGHT_HOUR
from niadra.app.signals import SIGNALS_EVERY_S, SIGNALS_IDLE_CAP_S
from niadra.app.space_loops import LoopSpec, RelayStep, Remembered, SpaceLoopSupervisor, night_day
from niadra.app.traits import rules_fingerprint
from niadra.config.models import SpaceConfig
from niadra.domain.extract.withhold import WITHHOLD_VERSION
from niadra.domain.ingest.masking import fingerprint as masking_fingerprint
from niadra.entrypoints.cell import CellContainer, Infra
from niadra.entrypoints.roles import CACHE_EPOCH_EVERY_S, RECEIPT_HEARTBEAT_EVERY_S, Loop, epoch_known, every
from niadra.entrypoints.serve import ALEMBIC_INI

TRIGGER_SWEEP_EVERY_S = 300.0
HOT_LAYER_CHECK_EVERY_S = 5.0
# A quiet space's relay comes back at least this often; the doorbell and the due times it reads wake it
# sooner. With PgBouncer's 10 s idle timeout its database holds a server connection a twelfth of the time.
RELAY_IDLE_CAP_S = 120.0
# New spaces' loops start at a random moment within this window, so a restart does not open every
# database at once (on 28/09/2026 forty spaces starting together took all 79 connections).
LOOPS_START_SPREAD_S = 30.0


def loops_role(c: CellContainer, infra: Infra, stack: AsyncExitStack) -> list[Loop]:
    # Only `loops` holds the owner's password: it creates and drops the space databases.
    admin = PgSpaceDatabaseAdmin(infra.settings.servers, AlembicHistory(ALEMBIC_INI))
    stack.push_async_callback(admin.close)
    return cell_loops(c, infra, admin)


def space_loops(c: CellContainer, infra: Infra, telemetry: PrometheusTelemetry) -> SpaceLoopSupervisor:
    """Relay and scheduler, idle sweeper and history search vector backfill per serving space database,
    each backing off while idle (6, 16.2); the relay sleeps until its space has something due, and the
    watchers and nightly steps ask their database again only when their marker moved (06, 6.3)."""

    def on(marker: Callable[[SpaceConfig], object]) -> Callable[[UUID], object | None]:
        return lambda space_id: marker(c.config.space(space_id))

    def night(hour: int) -> Callable[[UUID], object | None]:
        return lambda space_id: night_day(c.clock.now(), c.config.space(space_id).settings.timezone, hour)

    return SpaceLoopSupervisor(
        infra.registry,
        [
            LoopSpec("relay", 0.5, RELAY_IDLE_CAP_S, RelayStep(c.pipeline, telemetry), wakes=True),
            LoopSpec("sweep_idle", SWEEP_EVERY_S, SWEEP_IDLE_CAP_S, c.sessions.sweep_idle),
            LoopSpec("vectors", BACKFILL_EVERY_S, BACKFILL_IDLE_CAP_S, c.memory.backfill_vectors),
            # Pattern rules changed by an approved diff reach every profile, not only the next one.
            LoopSpec("trait_rules", 30.0, 300.0, Remembered(c.traits.watch_rules, on(rules_fingerprint))),
            # A new extractor, prompt or schema extracts the recent sessions again, a bounded pass.
            LoopSpec(
                "extractor",
                30.0,
                300.0,
                Remembered(c.extract.watch_extractor, on(lambda s: extractor_fingerprint(extractor_key(s)))),
            ),
            # New masking rules or types mask what was stored before them, a bounded pass (WP-A).
            LoopSpec(
                "masking",
                30.0,
                300.0,
                Remembered(c.ingest.watch_masking, on(lambda s: masking_fingerprint(s.masking_types()))),
            ),
            # New withholding rules scrub what memory stored before them, a bounded pass.
            LoopSpec(
                "withholding",
                30.0,
                300.0,
                Remembered(c.extract.watch_withholding, on(lambda _: WITHHOLD_VERSION)),
            ),
            # The nightly consolidation of every active profile, after 03:00 in the space's time zone.
            LoopSpec(
                "nightly", NIGHTLY_EVERY_S, NIGHTLY_EVERY_S, Remembered(c.memory.nightly, night(NIGHT_HOUR))
            ),
            # Every space moves its retrieval weights once a night from the slots' verdicts (11, 3.4).
            LoopSpec(
                "retrieval_weights",
                WEIGHTS_EVERY_S,
                WEIGHTS_EVERY_S,
                Remembered(c.measure.learn_weights, night(WEIGHTS_HOUR)),
            ),
            # And each agent's and model's learned view, from what it used and missed (13, 6.3).
            LoopSpec(
                "learned_views",
                WEIGHTS_EVERY_S,
                WEIGHTS_EVERY_S,
                Remembered(c.measure.learn_views, night(WEIGHTS_HOUR)),
            ),
            # Rows written before the space declared its purposes get them; once a day, expiry by purpose
            # and objections (13, 6.6). A space without purposes returns at once.
            LoopSpec("purposes", 60.0, 1800.0, c.purposes.step),
            # What customers asked that memory had no record of, counted once a day (13, 6.5).
            LoopSpec(
                "knowledge_gaps",
                NIGHTLY_EVERY_S,
                NIGHTLY_EVERY_S,
                Remembered(c.insights.record_gaps, night(GAPS_NIGHT_HOUR)),
            ),
            # The turn objects of held conversations get a copy no bucket rule expires (24.5.1).
            LoopSpec("turn_holds", 300.0, 3600.0, c.turns.keep_held),
            # Working states and customers' objects past their retention leave, and so do fields past their
            # own and superseded values past 90 days, except what a legal hold holds (24.5.1).
            LoopSpec(
                "typed_retention", 300.0, 3600.0, lambda space_id: objects_workers.retention(c, space_id)
            ),
            # What pushes and observations moved of the shared objects, one statement a second (24.3.12).
            LoopSpec("consolidate_shared", 1.0, 1.0, c.shared.consolidate),
            # What the typed shared objects the consolidator wrote declare besides their fields: values'
            # versions, timers, and the customers' objects derived from them.
            LoopSpec("shared_typed", 5.0, 5.0, lambda space_id: objects_workers.plan_shared(c, space_id)),
            # Shared objects without a reference for their type's `leave_after` leave the working set.
            LoopSpec(
                "working_set", 3600.0, 3600.0, lambda space_id: objects_workers.leave_working_set(c, space_id)
            ),
            # The refresh requests reads and writes admitted, one statement a pass (24.3.5); and the ones the
            # resolver worker never answered, given up with their paid call counted.
            LoopSpec("refresh_requests", 1.0, 10.0, c.refresh.flush),
            LoopSpec("refresh_given_up", 300.0, 3600.0, c.refresh.give_up),
            # Content whose type asks for a full scan goes to the decision model in batches (24.3.10).
            LoopSpec("content_scan", 30.0, 600.0, lambda space_id: objects_workers.scan_content(c, space_id)),
            # Subject signals derived once their window closes or their session goes quiet (24.3.2).
            LoopSpec("signals", SIGNALS_EVERY_S, SIGNALS_IDLE_CAP_S, c.signals.derive_due),
            # Once a night: the daily aggregate, the interleaved lists and the priors; and the priors back in
            # the cache whenever it lost them.
            LoopSpec(
                "signals_nightly",
                NIGHTLY_EVERY_S,
                NIGHTLY_EVERY_S,
                Remembered(c.signals.nightly, night(SIGNALS_NIGHT_HOUR)),
            ),
            LoopSpec("signal_priors", 300.0, 3600.0, c.signals.keep_priors),
            # Provisional outcomes final at the end of their window, objects past their deadline (24.7).
            LoopSpec("outcomes", OUTCOMES_EVERY_S, OUTCOMES_IDLE_CAP_S, c.outcomes.step),
            # Once a night, each experiment's look, which every later report takes the minimum over (6.4).
            LoopSpec(
                "experiment_looks",
                NIGHTLY_EVERY_S,
                NIGHTLY_EVERY_S,
                Remembered(c.experiments.look, night(EXPERIMENTS_NIGHT_HOUR)),
            ),
        ],
        concurrency=infra.settings.operations.loops_concurrency,
        telemetry=telemetry,
        jitter=lambda: random.uniform(0.85, 1.15),  # noqa: S311 (timing, not a secret)
        start_spread_s=LOOPS_START_SPREAD_S,
    )


def provisioner(c: CellContainer, infra: Infra, admin: PgSpaceDatabaseAdmin) -> Provisioner:
    s = infra.settings
    return Provisioner(
        infra.uow,
        admin,
        infra.registry,
        infra.config,
        c.pipeline,
        infra.keys,
        infra.secrets,
        infra.objects,
        infra.cache,
        HttpControlClient(s.control_url, s.cell_token, infra.control_http),
        infra.clock,
        ProvisioningSettings(s.cell_id, s.operations.instance_backup_retention),
        telemetry=PrometheusTelemetry(),
    )


def cell_loops(c: CellContainer, infra: Infra, admin: PgSpaceDatabaseAdmin) -> list[Loop]:
    """Work that one process does for the whole cell; the workers only consume their pools."""
    telemetry = PrometheusTelemetry()
    supervisor = space_loops(c, infra, telemetry)
    provisioning = provisioner(c, infra, admin)

    async def after_snapshot() -> None:
        # A new or removed space gets its database now, not at the next tick.
        await infra.config.refresh()
        provisioning.wake()

    c.control_sync.on_applied = after_snapshot

    async def sweep_triggers() -> None:
        await triggers.trigger_sweep(c, c.clock.now())

    async def spaces(stop: asyncio.Event) -> None:
        # The typed pass and the content scan publish the copies of what they wrote under the cache epoch.
        await epoch_known(c, stop)
        if not stop.is_set():
            await supervisor.run(stop)

    async def listen(stop: asyncio.Event) -> None:
        # The doorbell wakes a quiet space's relay for what was scheduled or left behind (06, 6.3).
        if infra.doorbell is not None:
            await supervisor.listen(infra.doorbell, stop)

    return [
        c.config.run,
        c.control_sync.run,
        # The control plane's changes are recorded from here: this producer heartbeats too.
        lambda stop: every(stop, RECEIPT_HEARTBEAT_EVERY_S, c.admin_audit.heartbeat, "admin_heartbeat"),
        infra.registry.run,
        spaces,
        lambda stop: every(stop, CACHE_EPOCH_EVERY_S, c.serve.refresh_cache_epoch, "cache_epoch"),
        listen,
        provisioning.run,
        lambda stop: copy_dead_letters(infra.bus, c.pipeline, DEAD_QUEUE, stop),
        lambda stop: _until(stop, c.audit.run_writer()),
        lambda stop: every(stop, TRIGGER_SWEEP_EVERY_S, sweep_triggers, "trigger_sweep"),
        # The state store keeps nothing across a restart: its hot layer is rebuilt from the event log (06, 3).
        lambda stop: every(stop, HOT_LAYER_CHECK_EVERY_S, c.ingest.watch_hot_layer, "hot_layer"),
        # The agents' working state, from the confirmed queue to the spaces' databases in batches (24.3.9).
        lambda stop: _until(stop, c.agent_state.run_writer()),
        # The durable record of coordination, one statement per batch, and the state store's reload after it
        # came back empty (24.3.4).
        lambda stop: _until(stop, c.coordinate.run_writer()),
        lambda stop: every(stop, 5.0, c.coordinate.watch_state_store, "coordination"),
        # The session overlay of subject signals back from the pending interactions after the state store came
        # back empty (24.3.2).
        lambda stop: every(stop, 5.0, c.signals.watch_overlay, "signals_overlay"),
    ]


async def _until(stop: asyncio.Event, work: Awaitable[None]) -> None:
    job = asyncio.ensure_future(work)
    waiter = asyncio.create_task(stop.wait())
    await asyncio.wait({job, waiter}, return_when=asyncio.FIRST_COMPLETED)
    for task in (job, waiter):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
