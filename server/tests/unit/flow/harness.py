"""A whole cell in memory, from the batch API to the outgoing webhooks.

Every service runs over the in-memory unit of work, with the same task handlers the workers register,
so a flow crosses the real module boundaries. Module tests fake each other's ports; a mismatch between
two modules (an item waiting for `invoice.credited` while agents record `credit`) only shows here.
"""

from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import fakeredis

from niadra.adapters.inbound.workers.runtime import WorkerRuntime, registry
from niadra.adapters.memory.fakes import InMemoryObjectStore, RecordedDelivery, ScriptedLlm
from niadra.adapters.memory.intake import TransactionIntakeLog
from niadra.adapters.memory.notifications import InMemoryFeedCache
from niadra.adapters.memory.refresh import InMemoryRefreshWants
from niadra.adapters.memory.shared import InMemorySharedObjects
from niadra.adapters.memory.signals import InMemorySignalOverlay, InMemoryTurnCounters
from niadra.adapters.memory.turns import TransactionTurnIntakeLog
from niadra.adapters.outbound.redis.coordinate import RedisCoordination
from niadra.adapters.outbound.redis.erasure import RedisErasureMarks
from niadra.app.agent_memory import AgentMemoryService
from niadra.app.audit import AuditService
from niadra.app.budget import BudgetReadBlock, BudgetService, TurnsTaken
from niadra.app.compile import CompileService
from niadra.app.configure import AssistService
from niadra.app.coordinate import CoordinateService, CoordinationReadBlock
from niadra.app.data_issues import DataIssuesService
from niadra.app.decide import DecideService
from niadra.app.extract import ExtractService
from niadra.app.identity import IdentityService
from niadra.app.ingest import IngestService
from niadra.app.insights import InsightsService
from niadra.app.measure import MeasureService
from niadra.app.memory import MemoryService
from niadra.app.metering import MeteringService
from niadra.app.notifications import NotificationsService
from niadra.app.notify import NotifyService
from niadra.app.objects import ObjectsService
from niadra.app.observations import ObservationsConsumer
from niadra.app.observed_config import ObservedConfigService
from niadra.app.outcomes import OutcomesService
from niadra.app.pipeline import PipelineService
from niadra.app.privacy import PrivacyService
from niadra.app.read_state import ReadState
from niadra.app.refresh import RefreshService
from niadra.app.review import ReviewService
from niadra.app.serve import ServeService
from niadra.app.sessions import SessionService
from niadra.app.shared_objects import SharedObjectsService
from niadra.app.signals import ConstraintsReadBlock, SignalsService
from niadra.app.state import StateBlock, StateService
from niadra.app.traits import TraitsService
from niadra.app.triggers import TriggersService
from niadra.app.turns import TurnService
from niadra.app.type_drift import TypeDriftService
from niadra.config.models import ExportSchedule
from niadra.contracts.events import BatchResponse
from niadra.contracts.ingest import validate_items
from niadra.domain.coordinate.records import CoordinationRecord
from niadra.domain.pipeline.delay import DelayReason
from niadra.domain.receipts import ProducerMark, ReceiptDraft
from niadra.domain.serve.cache_keys import CacheEpoch
from niadra.domain.serve.receipt_buffer import ReceiptBuffer
from niadra.domain.serve.subject_token import SubjectTokens
from niadra.domain.tasks import QueueClass, Task, TaskName
from niadra.entrypoints.serve import worker_modules
from niadra.ports.turns import TurnConsumers
from niadra.testing.cell import Cell, principal
from tests.unit.audit.fakes import RecordingAdminAudit

MAX_TASKS = 500


class CoordinationQueue:
    """The confirmed queue of coordination: it keeps what was published for the writer to take."""

    def __init__(self) -> None:
        self.records: list[CoordinationRecord] = []

    async def publish(self, records: Sequence[CoordinationRecord], timeout_s: float) -> bool:
        self.records.extend(records)
        return True


class DirectReceipts:
    """A receipt sink that writes through the chain writer at once, as a confirmed broker publish would."""

    def __init__(self) -> None:
        self.audit: AuditService | None = None
        self.sent: list[ReceiptDraft] = []

    async def emit(self, drafts: Sequence[ReceiptDraft], timeout_s: float) -> bool:
        assert self.audit is not None
        await self.audit.write(drafts)
        self.sent.extend(drafts)
        return True

    async def mark(self, mark: ProducerMark) -> None:
        assert self.audit is not None
        await self.audit.write([], [mark])


@dataclass
class RecordingTelemetry:
    memory: list[float] = field(default_factory=list)
    object_events: list[float] = field(default_factory=list)
    measurements: list[float] = field(default_factory=list)

    def receipts_lost(self, count: int) -> None: ...

    def llm_spent(self, purpose: str, cost_micros: int) -> None: ...

    def llm_budget_exhausted(self, purpose: str) -> None: ...

    def memory_ready(self, seconds: float) -> None:
        self.memory.append(seconds)

    def object_event_applied(self, seconds: float) -> None:
        self.object_events.append(seconds)

    def measurement_ready(self, seconds: float) -> None:
        self.measurements.append(seconds)


class _NoSource:
    async def batches(self, max_items: int) -> AsyncIterator[Any]:
        return
        yield


class _NoProposals:
    async def submit(self, proposal: object) -> str:
        raise AssertionError("the configuration assistant is not part of these flows")


class _NoDestinations:
    @asynccontextmanager
    async def open(self, schedule: ExportSchedule, credential: str) -> AsyncIterator[InMemoryObjectStore]:
        yield InMemoryObjectStore()

    async def check_write_only(self, schedule: ExportSchedule, credential: str) -> None:
        return None


@dataclass
class Flow:
    cell: Cell
    llm: ScriptedLlm
    receipts: DirectReceipts
    identity: IdentityService
    sessions: SessionService
    ingest: IngestService
    decide: DecideService
    extract: ExtractService
    memory: MemoryService
    objects: ObjectsService
    traits: TraitsService
    compile: CompileService
    serve: ServeService
    audit: AuditService
    measure: MeasureService
    privacy: PrivacyService
    triggers: TriggersService
    notify: NotifyService
    review: ReviewService
    configure: AssistService
    metering: MeteringService
    observed_config: ObservedConfigService
    insights: InsightsService
    pipeline: PipelineService
    telemetry: RecordingTelemetry
    agent_memory: AgentMemoryService
    turns: TurnService
    data_issues: DataIssuesService
    notifications: NotificationsService
    type_drift: TypeDriftService
    state: StateService
    observations: ObservationsConsumer
    shared: SharedObjectsService
    refresh: RefreshService
    signals: SignalsService
    signal_overlay: InMemorySignalOverlay
    outcomes: OutcomesService
    coordinate: CoordinateService
    coordination_queue: CoordinationQueue
    budget: BudgetService
    turn_counters: InMemoryTurnCounters
    coordination_server: fakeredis.FakeServer
    ran: list[Task] = field(default_factory=list)

    @property
    def clock(self) -> Any:
        """The cell's clock, as the workers' handlers read it from the services they are wired with."""
        return self.cell.clock

    @classmethod
    def build(cls, cell: Cell, llm: ScriptedLlm | None = None) -> "Flow":
        llm = llm or cell.llm
        uow, clock, config = cell.uow, cell.clock, cell.config
        receipts = DirectReceipts()
        epoch = CacheEpoch()
        data_issues = DataIssuesService(uow, clock, config, RecordingAdminAudit(), cell.cache)  # type: ignore[arg-type]
        identity = IdentityService(uow, clock, config)
        sessions = SessionService(uow, clock, config)
        decide = DecideService(uow, cell.models, clock, decider=cell.decider)
        compile_ = CompileService(uow, cell.keys, cell.cache, clock, config, hot=cell.hot, epoch=epoch)
        audit = AuditService(uow, _NoSource(), cell.objects, clock, config=config)
        receipts.audit = audit
        telemetry = RecordingTelemetry()
        ingest = IngestService(
            uow,
            identity,
            sessions,
            cell.keys,
            cell.hot,
            clock,
            config,
            cell.objects,
            cell.secrets,
            intake=TransactionIntakeLog(uow),
            cache=cell.cache,
            issues=data_issues,
        )
        # The read state of a read process, told of every commit as the triggers' feed would tell it; on
        # PostgreSQL (the local benchmark cell), the triggers and the poll tell it.
        listeners = getattr(uow, "listeners", None)
        read_state = ReadState(synchronous=listeners is not None)
        if listeners is not None:
            listeners.append(read_state.invalidate)
        observations = ObservationsConsumer(uow, cell.keys, config)
        type_drift = TypeDriftService(uow, clock, config, data_issues, cell.cache)
        refresh = RefreshService(uow, InMemoryRefreshWants(), clock, config)
        shared = SharedObjectsService(
            uow,
            InMemorySharedObjects(),
            cell.keys,
            clock,
            config,
            observations,
            cell.objects,
            refresh=refresh,
        )
        overlay = InMemorySignalOverlay()
        signals = SignalsService(
            uow,
            cell.keys,
            cell.cache,
            overlay,
            cell.objects,
            clock,
            config,
            identity=identity,
            working_set=shared,
        )
        serve = ServeService(
            uow,
            cell.cache,
            cell.hot,
            cell.keys,
            receipts,
            compile_,
            cell.models,
            clock,
            config,
            "flow-read",
            tokens=SubjectTokens(b"s" * 32),
            epoch=epoch,
            buffer=ReceiptBuffer(),
            read_state=read_state,
        )
        state = StateService(
            uow, cell.cache, cell.keys, clock, config, epoch=epoch, shared=shared, refresh=refresh
        )
        state.interests = signals
        compile_.instructions = signals
        serve.add_block(StateBlock(state, serve.guarded))
        serve.add_block(ConstraintsReadBlock(signals))
        counters = InMemoryTurnCounters()
        budget = BudgetService(uow, counters, cell.keys, config)
        serve.add_block(BudgetReadBlock(budget))
        outcomes = OutcomesService(uow, signals, clock, config, cell.objects)
        queue = CoordinationQueue()
        server = fakeredis.FakeServer()
        # A state store the loops process already reloaded and sealed.
        fakeredis.FakeRedis(server=server).set("coord:marker", "whole")
        coordinate = CoordinateService(
            uow,
            RedisCoordination(fakeredis.FakeAsyncRedis(server=server)),
            queue,
            cell.keys,
            clock,
            config,
            marks=RedisErasureMarks(fakeredis.FakeAsyncRedis(server=server)),
            secrets=cell.secrets,
            hot=cell.hot,
            handle_cache=cell.cache,
            views=serve,
            working_set=shared,
        )
        coordinate.events = ingest
        coordinate.interactions = signals
        serve.add_block(CoordinationReadBlock(coordinate))
        turns = TurnService(
            uow,
            TransactionTurnIntakeLog(uow),
            cell.keys,
            cell.objects,
            clock,
            config,
            RecordingAdminAudit(),  # type: ignore[arg-type]
            consumers=TurnConsumers(observations=observations, interactions=TurnsTaken(signals, budget)),
            workers=cell.bus,
            issues=data_issues,
        )
        return cls(
            cell=cell,
            llm=llm,
            receipts=receipts,
            identity=identity,
            sessions=sessions,
            ingest=ingest,
            decide=decide,
            extract=ExtractService(uow, llm, decide, clock, config),
            memory=MemoryService(
                uow,
                cell.models,
                decide,
                clock,
                config,
                telemetry=telemetry,
                instructions=signals,
                issues=data_issues,
            ),
            objects=ObjectsService(
                uow,
                clock,
                config,
                telemetry=telemetry,
                decide=decide,
                outcomes=outcomes,
                refresh=refresh,
                drift=type_drift,
                instructions=signals,
                keys=cell.keys,
            ),
            traits=TraitsService(uow, clock, config),
            compile=compile_,
            serve=serve,
            audit=audit,
            measure=MeasureService(uow, clock, config, telemetry=telemetry, instructions=signals),
            privacy=PrivacyService(
                uow,
                cell.keys,
                cell.objects,
                cell.hot,
                clock,
                config,
                secrets=cell.secrets,
                destinations=_NoDestinations(),
                cache=cell.cache,
                intake=ingest,
                fences=[coordinate, turns],
                instructions=signals,
            ),
            triggers=TriggersService(uow, clock, config),
            notify=NotifyService(uow, cell.egress, cell.secrets, clock, config),
            review=ReviewService(uow, clock, config),
            configure=AssistService(uow, llm, clock, config, _NoProposals()),
            metering=MeteringService(uow, clock),
            observed_config=ObservedConfigService(uow, config, clock, issues=data_issues),
            insights=InsightsService(
                uow, cell.keys, clock, config, receipts, cell.models, cell.cache, producer_id="flow-insights"
            ),
            pipeline=PipelineService(uow, cell.bus, clock),
            telemetry=telemetry,
            agent_memory=AgentMemoryService(
                uow,
                config,
                clock,
                cell.models,
                RecordingAdminAudit(),
                cell.keys,
                llm=llm,  # type: ignore[arg-type]
            ),
            turns=turns,
            data_issues=data_issues,
            notifications=NotificationsService(uow, InMemoryFeedCache()),
            type_drift=type_drift,
            state=state,
            observations=observations,
            shared=shared,
            refresh=refresh,
            signals=signals,
            signal_overlay=overlay,
            outcomes=outcomes,
            coordinate=coordinate,
            coordination_queue=queue,
            budget=budget,
            turn_counters=counters,
            coordination_server=server,
        )

    async def send(self, source_id: UUID, *items: dict[str, Any]) -> BatchResponse:
        valid, errors = validate_items(list(items))
        caller = principal(source_id, self.cell.config.space(_space(self)))
        return await self.ingest.ingest_items(caller, valid, errors)

    async def prove(self, source_id: UUID, conversation: str, phone: str, level: str = "V1") -> None:
        """The source proved who it speaks with in the conversation: its reads get `level` there."""
        proof = {
            "type": "verify",
            "idempotency_key": f"{conversation}-proof-{level}",
            "method": "otp_sms",
            "level": level,
            "conversation_id": conversation,
            "handle": {"type": "phone_e164", "value": phone},
            "occurred_at": self.cell.clock.now().isoformat(),
        }
        assert (await self.send(source_id, proof)).errors == []

    def pending(self, name: TaskName | None = None) -> list[Task]:
        return [t for t in self._outbox() if name is None or t.name == name]

    async def drain(
        self,
        *,
        skip: Collection[TaskName] = (),
        only: Collection[TaskName] | None = None,
        windows: bool = True,
        leased: bool = False,
    ) -> list[Task]:
        """Runs outbox tasks through the workers' handlers until none is left; returns what ran. With
        `windows`, the extraction's batch windows then close (the clock does not move here): the tasks they
        hold run too, the sessions that closed in this drain together. With `leased`, each task is delivered
        to the worker runtime instead, which takes its lease and retries or buries it as in production."""
        handlers = registry(self, [m.handlers for m in worker_modules()])
        worker = self.worker() if leased else None
        ran: list[Task] = []
        while True:
            runnable = [t for t in self._outbox() if t.name not in skip and (only is None or t.name in only)]
            if not runnable and windows and self.close_windows():
                continue
            if not runnable:
                return ran
            task = runnable[0]
            self._outbox().remove(task)
            assert len(ran) < MAX_TASKS, "the flow does not settle"
            if worker is None:
                await handlers[task.name](task)
            else:
                await worker.handle(RecordedDelivery(task))
            ran.append(task)
            self.ran.append(task)

    def worker(self) -> WorkerRuntime:
        """The worker runtime over this flow's handlers: a delivery takes its lease, retries and is buried."""
        cell = self.cell
        return WorkerRuntime(
            cell.bus,
            PipelineService(cell.uow, cell.bus, cell.clock),
            registry(self, [m.handlers for m in worker_modules()]),
            queues=[q.value for q in QueueClass],
            jitter=lambda: 0.0,
        )

    def close_windows(self) -> int:
        """Moves the tasks waiting for an extraction batch window to the outbox; returns how many."""
        rows = self.cell.uow.store.module("pipeline").setdefault("scheduled", [])
        waiting = [row for row in rows if row[2] == DelayReason.BATCH_WINDOW]
        rows[:] = [row for row in rows if row[2] != DelayReason.BATCH_WINDOW]
        self._outbox().extend(task for task, _, _ in waiting)
        return len(waiting)

    def deliveries(self, event_type: str | None = None) -> list[dict[str, Any]]:
        rows = self.cell.uow.store.module("notify").get("deliveries", {}).values()
        return [d.body for d in rows if event_type is None or d.event_type == event_type]

    def _outbox(self) -> list[Task]:
        rows: list[Task] = self.cell.uow.store.module("pipeline").setdefault("outbox", [])
        return rows


def _space(flow: Flow) -> UUID:
    return flow.cell.config.spaces()[0].space_id
