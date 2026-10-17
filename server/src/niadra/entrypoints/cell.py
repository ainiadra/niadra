"""Composition root of a cell: opens the infrastructure once per process and builds every service.

All cell deployables (ingest, read, admin, workers, insights) share this container; each one only
exercises the services its routes and loops reach. Everything is portable: PostgreSQL, RabbitMQ,
a Redis-compatible cache, S3-protocol storage and an OpenAI-compatible LLM endpoint.
"""

import asyncio
import base64
import hashlib
import logging
import os
import socket
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from importlib.metadata import version as package_version
from uuid import UUID

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from niadra.adapters.inbound.http.bodies import LargeBodies
from niadra.adapters.observability.metrics import PrometheusTelemetry
from niadra.adapters.outbound.clock import SystemClock
from niadra.adapters.outbound.controlplane.client import HttpControlClient
from niadra.adapters.outbound.controlplane.proposals import HttpConfigProposals
from niadra.adapters.outbound.egress import SafeEgress
from niadra.adapters.outbound.keys.kek import load_kek
from niadra.adapters.outbound.keys.kms import KMS_PREFIX, KmsKeyVault, open_kms
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.adapters.outbound.keys.postgres import PgWrappedKeyStore
from niadra.adapters.outbound.llm.chat import ChatCompletionsLlm, ChatSettings
from niadra.adapters.outbound.llm.decisions import DeciderSettings, HttpDecider
from niadra.adapters.outbound.llm.disabled import DisabledLlm
from niadra.adapters.outbound.media_text import LocalMediaText
from niadra.adapters.outbound.models.http import HttpModelsClient
from niadra.adapters.outbound.objects.anchors import LockedAnchors
from niadra.adapters.outbound.objects.destinations import S3ExportDestinations
from niadra.adapters.outbound.objects.s3 import S3ObjectStore
from niadra.adapters.outbound.outbox import OutboxPublisher, PublishingUnitOfWorkFactory
from niadra.adapters.outbound.postgres.intake import PgIntakeLog
from niadra.adapters.outbound.postgres.leases import PgLeaseStore
from niadra.adapters.outbound.postgres.probe import PgPipelineProbe
from niadra.adapters.outbound.postgres.registry import EngineRegistry
from niadra.adapters.outbound.postgres.turns import PgTurnIntakeLog
from niadra.adapters.outbound.postgres.uow import PgUnitOfWorkFactory, make_engine
from niadra.adapters.outbound.prometheus import PrometheusReader
from niadra.adapters.outbound.rabbitmq.agent_state import RabbitAgentStateLog
from niadra.adapters.outbound.rabbitmq.connection import RabbitConnection
from niadra.adapters.outbound.rabbitmq.coordination import RabbitCoordinationFeed, RabbitCoordinationLog
from niadra.adapters.outbound.rabbitmq.receipts import RabbitReceiptSink, RabbitReceiptSource
from niadra.adapters.outbound.rabbitmq.tasks import RabbitTaskBus
from niadra.adapters.outbound.redis import connect as connect_redis
from niadra.adapters.outbound.redis.agent_state import RedisAgentStateStore
from niadra.adapters.outbound.redis.budget import RedisTurnCounters
from niadra.adapters.outbound.redis.cache import RedisCache
from niadra.adapters.outbound.redis.coordinate import RedisCoordination
from niadra.adapters.outbound.redis.doorbell import RedisDoorbell
from niadra.adapters.outbound.redis.erasure import RedisErasureMarks
from niadra.adapters.outbound.redis.hot import RedisHotLayer
from niadra.adapters.outbound.redis.notifications import RedisFeedCache
from niadra.adapters.outbound.redis.refresh import RedisRefreshWants
from niadra.adapters.outbound.redis.shared import RedisSharedObjects
from niadra.adapters.outbound.redis.signals import RedisSignalOverlay
from niadra.adapters.outbound.secrets import PgSecretStore
from niadra.app.admin_audit import AdminAudit
from niadra.app.agent_memory import AgentMemoryService
from niadra.app.agent_state import AgentStateService
from niadra.app.archive import ArchiveReader
from niadra.app.audit import AuditService
from niadra.app.budget import BudgetReadBlock, BudgetService, TurnsTaken
from niadra.app.compile import CompileService
from niadra.app.config import CachedConfigProvider, ConfigService
from niadra.app.configure import AssistService
from niadra.app.control_sync import ControlSync
from niadra.app.coordinate import CoordinateService, CoordinationReadBlock
from niadra.app.data_issues import DataIssuesService
from niadra.app.decide import DecideService
from niadra.app.edge import EdgeService
from niadra.app.experiments import ExperimentsService
from niadra.app.extract import ExtractService, ExtractSettings, LlmBudget
from niadra.app.graph import GraphService
from niadra.app.holds import LegalHoldsService
from niadra.app.identity import IdentityService
from niadra.app.inferences import InferencesService
from niadra.app.ingest import IngestService
from niadra.app.insights import InsightsService
from niadra.app.latency import ContextLatencyService
from niadra.app.measure import MeasureService
from niadra.app.memory import MemoryService
from niadra.app.metering import MeteringService
from niadra.app.notifications import NotificationsService
from niadra.app.notify import NotifyService
from niadra.app.objects import ObjectsService
from niadra.app.observations import ObservationsConsumer
from niadra.app.observed_config import ObservedConfigService
from niadra.app.observed_operations import ObservedOperationsService
from niadra.app.observed_words import ObservedWordsService
from niadra.app.outcomes import OutcomesService
from niadra.app.pipeline import PipelineService
from niadra.app.policy import PolicyService
from niadra.app.privacy import PrivacyService
from niadra.app.purposes import PurposeService
from niadra.app.refresh import RefreshService
from niadra.app.replay import ReplayService
from niadra.app.retrieval import RetrievalCache, RetrievalService
from niadra.app.review import ReviewService
from niadra.app.review_requests import ReviewRequestsService
from niadra.app.serve import ServeService
from niadra.app.sessions import SessionService
from niadra.app.shared_objects import SharedObjectsService
from niadra.app.signals import ConstraintsReadBlock, SignalsService
from niadra.app.state import StateBlock, StateService
from niadra.app.traits import TraitsService
from niadra.app.triggers import TriggersService
from niadra.app.turns import TurnService
from niadra.app.type_drift import TypeDriftService
from niadra.domain.common.errors import NotFound
from niadra.domain.serve.cache_keys import CacheEpoch
from niadra.domain.serve.privacy_guard import PrivacyGuard
from niadra.domain.serve.receipt_buffer import ReceiptBuffer
from niadra.domain.serve.subject_token import SubjectTokens
from niadra.entrypoints.settings import ObjectStorage, Settings
from niadra.ports.erasure import ErasureFence
from niadra.ports.infra import DecisionModel, KeyVault, LlmClient
from niadra.ports.turns import TurnConsumer, TurnConsumers

log = logging.getLogger(__name__)

# The software version the config manifest is checked against (its minimum cell version), not the
# release tag: a snapshot that needs a newer cell is refused rather than half-applied (4.7).
CELL_VERSION = package_version("niadra-server")
JWKS_ATTEMPTS = 30


@dataclass(frozen=True, slots=True)
class ControlKeys:
    """The control plane's Ed25519 keys: they sign person tokens and the config snapshot (4.7, 5.1)."""

    by_kid: Mapping[str, bytes]

    @property
    def public_keys(self) -> list[Ed25519PublicKey]:
        return [Ed25519PublicKey.from_public_bytes(raw) for raw in self.by_kid.values()]


@dataclass(slots=True)
class Infra:
    settings: Settings
    clock: SystemClock
    registry: EngineRegistry
    uow: PublishingUnitOfWorkFactory
    keys: KeyVault
    cache: RedisCache
    state: RedisCache
    """Revocations, rate windows and daily limits: kept where nothing is evicted (`state_redis_url`)."""
    hot: RedisHotLayer
    state_redis: Redis
    """The state store's client, the one `state` and `hot` wrap: for the agent features' own scripts on it."""
    rabbit: RabbitConnection
    bus: RabbitTaskBus
    receipts: RabbitReceiptSink
    objects: S3ObjectStore
    anchors: LockedAnchors | None
    models: HttpModelsClient
    secrets: PgSecretStore
    llm: LlmClient
    control_http: httpx.AsyncClient
    control_keys: ControlKeys
    config: CachedConfigProvider
    producer_id: str
    decider: DecisionModel | None = None
    """The decision model behind the decisions port; without one configured its tasks take their conservative
    label."""
    doorbell: RedisDoorbell | None = None
    """Wakes a quiet space's relay for work scheduled or left behind (06, 6.3), in the state store."""


@dataclass(slots=True)
class CellContainer:
    """What routes (`service("name")`) and worker handlers (`c.<name>`) reach."""

    clock: SystemClock
    config: CachedConfigProvider
    edge: EdgeService
    identity: IdentityService
    sessions: SessionService
    ingest: IngestService
    pipeline: PipelineService
    extract: ExtractService
    memory: MemoryService
    objects: ObjectsService
    traits: TraitsService
    compile: CompileService
    serve: ServeService
    policy: PolicyService
    audit: AuditService
    measure: MeasureService
    privacy: PrivacyService
    purposes: PurposeService
    triggers: TriggersService
    notify: NotifyService
    review: ReviewService
    configure: AssistService
    metering: MeteringService
    observed_config: ObservedConfigService
    latency: ContextLatencyService
    insights: InsightsService
    graph: GraphService
    admin_audit: AdminAudit
    control_sync: ControlSync
    agent_memory: AgentMemoryService
    large_bodies: LargeBodies
    # The agent features' services (4.4.1).
    turns: TurnService
    replay: ReplayService
    data_issues: DataIssuesService
    notifications: NotificationsService
    state: StateService
    observations: ObservationsConsumer
    agent_state: AgentStateService
    shared: SharedObjectsService
    refresh: RefreshService
    type_drift: TypeDriftService
    coordinate: CoordinateService
    signals: SignalsService
    outcomes: OutcomesService
    experiments: ExperimentsService
    budget: BudgetService
    inferences: InferencesService
    review_requests: ReviewRequestsService
    legal_holds: LegalHoldsService


@asynccontextmanager
async def open_infra(settings: Settings, role: str) -> AsyncIterator[Infra]:
    """Opens every connection of one process and closes them in reverse order on exit."""
    async with AsyncExitStack() as stack:
        clock = SystemClock()
        # The cell database, as the system role; each space database gets a small pool of its own,
        # opened on first use by the registry (10).
        cell_engine = make_engine(
            settings.servers.cell_url("system"),
            pool_size=1,
            max_overflow=2,
            pool_timeout=5,
            # The same name in every process, so PgBouncer never renames a shared server connection.
            application_name="niadra-cell",
        )
        stack.push_async_callback(cell_engine.dispose)
        registry = EngineRegistry(settings.servers, cell_engine, settings.pools)
        stack.push_async_callback(registry.close)
        await registry.refresh()

        # Data keys and secrets live in the cell database, never in a space's (5.14.1).
        keys, secrets = await open_keys(settings, cell_engine, stack)

        redis, state_redis = open_redis(settings, stack)
        purged_with_cache = (state_redis,) if state_redis is not redis else ()

        rabbit = RabbitConnection(settings.rabbitmq_url)
        await rabbit.open()
        stack.push_async_callback(rabbit.close)
        bus = RabbitTaskBus(rabbit)

        objects = await stack.enter_async_context(open_object_store(settings.object_storage))
        anchors = None
        if settings.audit_bucket:
            locked = replace(settings.object_storage, bucket=settings.audit_bucket, public_endpoint_url=None)
            anchors = LockedAnchors(await stack.enter_async_context(open_object_store(locked)))
        models = HttpModelsClient(settings.models_url)
        stack.push_async_callback(models.aclose)

        inner = PgUnitOfWorkFactory(keys, registry, cell_engine)
        doorbell = RedisDoorbell(state_redis, store="state" if purged_with_cache else "cache")
        publisher = OutboxPublisher(bus, inner, doorbell=doorbell)
        stack.push_async_callback(publisher.drain)
        uow = PublishingUnitOfWorkFactory(inner, publisher)

        # Before the model client: the budget reads each space's contract ceiling from it.
        config = CachedConfigProvider(uow, settings.cell_id)
        await config.refresh()
        if isinstance(keys, KmsKeyVault):
            # A space whose contract names the customer's own KMS key gets its data keys under it (10, D.1).
            keys.use_space_keys(lambda space_id: _customer_key(config, space_id))

        llm: LlmClient = DisabledLlm()
        budget = LlmBudget(uow, clock, telemetry=PrometheusTelemetry(), config=config)
        decider: DecisionModel | None = None
        chat = chat_settings(settings)
        if settings.llm_api_key and chat is not None:
            chat_llm = ChatCompletionsLlm(settings.llm_api_key, budget, chat)
            stack.push_async_callback(chat_llm.aclose)
            llm = chat_llm
        else:
            log.warning(
                "no LLM endpoint (NIADRA_LLM_API_KEY, NIADRA_LLM_BASE_URL, NIADRA_LLM_MODEL): extraction and "
                "the assistant stay queued until one is set"
            )
        decisions = decider_settings(settings)
        if settings.llm_api_key and decisions is not None:
            http_decider = HttpDecider(settings.llm_api_key, budget, decisions)
            stack.push_async_callback(http_decider.aclose)
            decider = http_decider
        else:
            log.warning(
                "no decision model (NIADRA_LLM_API_KEY, NIADRA_DECISIONS_BASE_URL, NIADRA_DECISIONS_MODEL): "
                "the decisions take their conservative label"
            )

        control_http = httpx.AsyncClient(base_url=settings.control_url, timeout=httpx.Timeout(10.0))
        stack.push_async_callback(control_http.aclose)
        control_keys = await fetch_control_keys(control_http)

        yield Infra(
            settings=settings,
            clock=clock,
            registry=registry,
            uow=uow,
            keys=keys,
            cache=RedisCache(redis, also_purged=purged_with_cache),
            state=RedisCache(state_redis, store="state" if purged_with_cache else "cache"),
            hot=RedisHotLayer(state_redis, clock),
            state_redis=state_redis,
            rabbit=rabbit,
            bus=bus,
            receipts=RabbitReceiptSink(rabbit),
            objects=objects,
            anchors=anchors,
            models=models,
            secrets=secrets,
            llm=llm,
            control_http=control_http,
            control_keys=control_keys,
            config=config,
            producer_id=f"{role}:{socket.gethostname()}:{os.getpid()}",
            decider=decider,
            doorbell=doorbell,
        )


def chat_settings(settings: Settings) -> ChatSettings | None:
    if not (settings.llm_base_url and settings.llm_model):
        return None
    return ChatSettings(
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        provider_order=settings.llm_provider_order,
        zero_retention=settings.llm_zero_retention,
        include_usage=settings.llm_include_usage,
    )


def decider_settings(settings: Settings) -> DeciderSettings | None:
    if not (settings.decisions_base_url and settings.decisions_model):
        return None
    return DeciderSettings(
        base_url=settings.decisions_base_url,
        model=settings.decisions_model,
        zero_retention=settings.llm_zero_retention,
    )


def open_redis(settings: Settings, stack: AsyncExitStack) -> tuple[Redis, Redis]:
    """The evicting cache store and the state store; the same client when no state store is set."""
    redis = connect_redis(settings.redis_url)
    stack.push_async_callback(redis.aclose)
    if not settings.state_redis_url or settings.state_redis_url == settings.redis_url:
        return redis, redis
    state = connect_redis(settings.state_redis_url)
    stack.push_async_callback(state.aclose)
    return redis, state


async def open_keys(
    settings: Settings, cell_engine: AsyncEngine, stack: AsyncExitStack
) -> tuple[KeyVault, PgSecretStore]:
    """The master key in KMS when configured; else the local KEK (5.14.1)."""
    if settings.kms_key_id:
        kms = await stack.enter_async_context(open_kms(settings.region, settings.kms_endpoint))
        store = PgWrappedKeyStore(cell_engine, KMS_PREFIX + settings.kms_key_id)
        vault, cell_keys = await KmsKeyVault.open(kms, settings.kms_key_id, store)
        return vault, PgSecretStore(cell_engine, cell_keys.secrets_key, KMS_PREFIX + settings.kms_key_id)
    kek = load_kek(settings.kek_source)
    kek_id = hashlib.sha256(kek).hexdigest()[:16]
    return LocalKeyVault(kek, PgWrappedKeyStore(cell_engine, kek_id)), PgSecretStore.from_kek(
        cell_engine, kek, kek_id
    )


def build_container(infra: Infra) -> CellContainer:
    s, uow, clock, config = infra.settings, infra.uow, infra.clock, infra.config
    epoch = CacheEpoch()
    admin_audit = AdminAudit(infra.receipts, clock, infra.producer_id)
    # The `DataIssues` port (`ports.data_issues`): a module that finds a problem in the company's data opens
    # an issue for its owner inside its own transaction.
    data_issues = DataIssuesService(uow, clock, config, admin_audit, infra.cache)
    identity = IdentityService(uow, clock, config, leases=PgLeaseStore(infra.registry))
    sessions = SessionService(uow, clock, config)
    decide = DecideService(uow, infra.models, clock, decider=infra.decider)
    telemetry = PrometheusTelemetry()
    retrieval = RetrievalService(
        uow,
        config,
        clock,
        RetrievalCache(s.retrieval_cache_mib << 20, telemetry=telemetry),
        infra.models if s.semantic_channel == "models" else None,
        deadline_s=s.semantic_deadline_ms / 1000,
        telemetry=telemetry,
    )
    compile_ = CompileService(
        uow, infra.keys, infra.cache, clock, config, hot=infra.hot, epoch=epoch, on_snapshot=retrieval.offer
    )
    metering = MeteringService(uow, clock, config)
    serve = ServeService(
        uow,
        infra.cache,
        infra.hot,
        infra.keys,
        infra.receipts,
        compile_,
        infra.models,
        clock,
        config,
        infra.producer_id,
        tokens=SubjectTokens(s.subject_token_secret),
        epoch=epoch,
        guard=PrivacyGuard(),
        buffer=ReceiptBuffer(),
        databases=infra.registry,
        retrieval=retrieval,
        telemetry=telemetry,
    )
    config_service = ConfigService(uow, infra.control_keys.public_keys, s.cell_id, CELL_VERSION)
    edge = EdgeService(
        uow, infra.state, config, clock, infra.control_keys.by_kid, s.key_pepper, databases=infra.registry
    )
    # What each module takes from the applied turns (`ports.turns`), set below:
    # `observations` for the objects, `interactions` and `claims` for the signals, `effects` for coordination.
    turn_consumers: dict[str, TurnConsumer] = {}
    # What an erasure fences before its first row goes and purges once it is done (`ports.erasure`): each
    # module that writes a subject's data outside its transaction adds its own below.
    erasure_fences: list[ErasureFence] = []
    # What the tools of the applied turns showed of the objects (24.3.5), and the shared objects they brought
    # into the working set, whose hot copy sits in the cache store (24.3.12).
    # The state store never evicts: the agents' working state lives there, and so do the marks an erasure
    # leaves, with its time, for the writers that were on their way. Every module's fence shares them
    # (`ports.erasure`).
    agent_store = RedisAgentStateStore(infra.state_redis)
    erasure_marks = RedisErasureMarks(infra.state_redis)
    observations = ObservationsConsumer(uow, infra.keys, config, erasures=(erasure_marks, clock))
    turn_consumers["observations"] = observations
    # Reasons to fetch an object again admit requests for the company's resolver worker in the state store,
    # against the coordination's paid budgets (24.3.5).
    refresh = RefreshService(uow, RedisRefreshWants(infra.state_redis), clock, config)
    shared = SharedObjectsService(
        uow,
        RedisSharedObjects(infra.cache.client, state=infra.state_redis),
        infra.keys,
        clock,
        config,
        observations,
        infra.objects,
        refresh=refresh,
    )
    # A watch's revalidation no fresh value answered goes back to the objects module.
    refresh.watches = shared
    # Typed state (24.3): copies of the typed objects in the cache store, under the cache epoch.
    state = StateService(
        uow, infra.cache, infra.keys, clock, config, epoch=epoch, shared=shared, refresh=refresh
    )
    serve.add_block(StateBlock(state, serve.guarded))
    # Drift of the declared types, by a deriving tool's fingerprint or by the traffic, and the types never
    # declared, back to the company as data issues (24.2.1).
    type_drift = TypeDriftService(uow, clock, config, data_issues, infra.cache)
    shared.drift = type_drift
    # The agents' working state (24.3.9): decided on the state store, copied for the reads in the cache store,
    # made durable through the confirmed queue and its batch writer.
    agent_state = AgentStateService(
        uow,
        agent_store,
        infra.cache,
        RabbitAgentStateLog(infra.rabbit),
        infra.keys,
        clock,
        config,
        marks=erasure_marks,
        epoch=epoch,
    )
    erasure_fences += [agent_state, observations]
    # The scripts decide in the state store, where nothing is evicted (24.3.4); the vault holds the keys
    # gateways share, and the hot layer the customer's last message a channel's service window runs from.
    coordinate = CoordinateService(
        uow,
        RedisCoordination(infra.state_redis),
        RabbitCoordinationLog(infra.rabbit),
        infra.keys,
        clock,
        config,
        marks=erasure_marks,
        feed=RabbitCoordinationFeed(infra.rabbit),
        secrets=infra.secrets,
        hot=infra.hot,
        handle_cache=infra.cache,
        views=serve,
        working_set=shared,
    )
    serve.add_block(CoordinationReadBlock(coordinate))
    # The effects turn records report, after the fact (the coordination spec, 7.6).
    turn_consumers["effects"] = coordinate
    # An erasure fences the subject off in the state store and the writer's queue, then purges both (24.5.5).
    erasure_fences.append(coordinate)
    signals = SignalsService(
        uow,
        infra.keys,
        infra.cache,
        RedisSignalOverlay(infra.state_redis),
        infra.objects,
        clock,
        config,
        identity=identity,
        working_set=shared,
    )
    serve.add_block(ConstraintsReadBlock(signals))
    # What people said must or must not be done about an object, for the packs of its participants.
    compile_.instructions = signals
    # The subject's interests in the `now` view come from the signals' cache and overlay (24.3.13).
    state.interests = signals
    # The context budget: the applied turns' counts in the state store, beside the signals.
    budget = BudgetService(uow, RedisTurnCounters(infra.state_redis), infra.keys, config)
    serve.add_block(BudgetReadBlock(budget))
    turn_consumers["interactions"] = TurnsTaken(signals, budget)
    # The objects module tells it, inside each write, which objects of a measured type moved (24.7).
    outcomes = OutcomesService(uow, signals, clock, config, infra.objects)
    legal_holds = LegalHoldsService(uow, infra.keys, clock)
    # A handoff's outcome feeds the subject's signals (24.3.4).
    coordinate.interactions = signals
    # Built once every module above set what it takes from the applied turns.
    turns = TurnService(
        uow,
        PgTurnIntakeLog(infra.registry),
        infra.keys,
        infra.objects,
        clock,
        config,
        admin_audit,
        consumers=TurnConsumers(**turn_consumers),
        workers=infra.bus,
        doorbell=infra.doorbell,
        holds=legal_holds,
        models=infra.models,
        issues=data_issues,
    )
    # An erasure fences off the target's conversations: turns still in the intake, or arriving after, and
    # feed events naming the target never come back (24.5.5).
    erasure_fences.append(turns)
    # A failure a replay attributes to data opens an issue; a changed configuration enters the change log.
    replay = ReplayService(uow, turns, infra.objects, clock, config, admin_audit, issues=data_issues)
    config_service.history = replay
    ingest = IngestService(
        uow,
        identity,
        sessions,
        infra.keys,
        infra.hot,
        clock,
        config,
        infra.objects,
        infra.secrets,
        media=LocalMediaText(s.models_url),
        models=infra.models,
        intake=PgIntakeLog(infra.registry),
        cache=infra.cache,
        workers=infra.bus,
        doorbell=infra.doorbell,
        issues=data_issues,
    )
    # An erasure stores what was acknowledged first, and takes the target's events from batches set aside.
    erasure_fences.append(ingest)
    # A handoff's outcome and a commitment reach the subject's memory as coordination's own events (24.3.4).
    coordinate.events = ingest
    container = CellContainer(
        clock=clock,
        large_bodies=LargeBodies(infra.settings.large_bodies, infra.settings.large_body_bytes),
        config=config,
        edge=edge,
        identity=identity,
        sessions=sessions,
        ingest=ingest,
        pipeline=PipelineService(
            uow,
            infra.bus,
            clock,
            probe=PgPipelineProbe(infra.registry),
            leases=PgLeaseStore(infra.registry),
        ),
        extract=ExtractService(
            uow,
            infra.llm,
            decide,
            clock,
            config,
            ExtractSettings(budget_spread_s=s.budget_spread_s, batch_window_s=s.extract_batch_window_s),
            observed=ObservedOperationsService(uow, infra.cache, infra.llm, decide, clock),
            issues=data_issues,
        ),
        memory=MemoryService(
            uow,
            infra.models,
            decide,
            clock,
            config,
            telemetry=PrometheusTelemetry(),
            instructions=signals,
            issues=data_issues,
        ),
        objects=ObjectsService(
            uow,
            clock,
            config,
            telemetry=PrometheusTelemetry(),
            decide=decide,
            archive=ArchiveReader(infra.objects, infra.keys, config),
            outcomes=outcomes,
            refresh=refresh,
            drift=type_drift,
            derivations=state,
            instructions=signals,
            keys=infra.keys,
        ),
        traits=TraitsService(uow, clock, config),
        compile=compile_,
        serve=serve,
        policy=PolicyService(uow, config, live=serve),
        audit=AuditService(
            uow,
            RabbitReceiptSource(infra.rabbit),
            infra.objects,
            clock,
            telemetry=PrometheusTelemetry(),
            anchors=infra.anchors,
            anchor_retention=timedelta(days=s.audit_retention_days),
            config=config,
        ),
        measure=MeasureService(uow, clock, config, telemetry=PrometheusTelemetry(), instructions=signals),
        privacy=PrivacyService(
            uow,
            infra.keys,
            infra.objects,
            infra.hot,
            clock,
            config,
            secrets=infra.secrets,
            destinations=S3ExportDestinations(),
            cache=infra.cache,
            intake=ingest,
            fences=erasure_fences,
            instructions=signals,
        ),
        purposes=PurposeService(uow, clock, config),
        triggers=TriggersService(uow, clock, config),
        notify=NotifyService(uow, SafeEgress(), infra.secrets, clock, config),
        review=ReviewService(uow, clock, config),
        configure=AssistService(
            uow,
            infra.llm,
            clock,
            config,
            HttpConfigProposals(infra.control_http, s.cell_id, s.cell_token),
            objects=infra.objects,
        ),
        metering=metering,
        observed_config=ObservedConfigService(
            uow,
            config,
            clock,
            records=HttpConfigProposals(infra.control_http, s.cell_id, s.cell_token),
            operations=ObservedOperationsService(uow, infra.cache, infra.llm, decide, clock),
            words=ObservedWordsService(infra.llm),
            issues=data_issues,
        ),
        latency=ContextLatencyService(
            PrometheusReader(s.prometheus_url) if s.prometheus_url else None, clock
        ),
        insights=InsightsService(
            uow,
            infra.keys,
            clock,
            config,
            infra.receipts,
            infra.models,
            infra.state,
            # Each receipt producer numbers its own sequence (5.11); insights is a producer of its own.
            producer_id=f"{infra.producer_id}/insights",
        ),
        graph=GraphService(uow, infra.keys, clock, config, instructions=signals),
        admin_audit=admin_audit,
        agent_memory=AgentMemoryService(
            uow, config, clock, infra.models, admin_audit, infra.keys, llm=infra.llm
        ),
        control_sync=ControlSync(
            HttpControlClient(s.control_url, s.cell_token, infra.control_http),
            config_service,
            edge,
            infra.control_keys.public_keys,
            clock,
            s.cell_id,
            usage=metering,
            spaces=lambda: [space.space_id for space in config.spaces()],
            audit=admin_audit.record_system,
            access=config_service,
            space_tenants=lambda: [(space.space_id, space.tenant_id) for space in config.spaces()],
        ),
        turns=turns,
        replay=replay,
        data_issues=data_issues,
        # The feed's warm copy lives on the cache store, next to the packs it is read with.
        notifications=NotificationsService(uow, RedisFeedCache(infra.cache.client)),
        state=state,
        observations=observations,
        agent_state=agent_state,
        shared=shared,
        refresh=refresh,
        type_drift=type_drift,
        coordinate=coordinate,
        signals=signals,
        outcomes=outcomes,
        experiments=ExperimentsService(uow, clock, config),
        budget=budget,
        inferences=InferencesService(uow, signals),
        review_requests=ReviewRequestsService(uow, infra.keys, clock, config),
        legal_holds=legal_holds,
    )
    # The day's audit anchor carries the kept tier's index with the receipts' root (24.5.4).
    container.audit.turn_index = turns
    # A paid refresh is admitted against its budget before the resolution worker gets it (24.2.12, 24.3.4).
    container.objects.admission = container.coordinate
    return container


def _customer_key(config: CachedConfigProvider, space_id: UUID) -> str | None:
    try:
        return config.space(space_id).kms_key_arn
    except NotFound:
        return None


def open_object_store(storage: ObjectStorage) -> AbstractAsyncContextManager[S3ObjectStore]:
    return S3ObjectStore.connect(
        bucket=storage.bucket,
        region=storage.region,
        endpoint_url=storage.endpoint_url,
        public_endpoint_url=storage.public_endpoint_url,
        access_key_id=storage.access_key_id,
        secret_access_key=storage.secret_access_key,
    )


async def fetch_control_keys(client: httpx.AsyncClient) -> ControlKeys:
    """The cell trusts the keys published by its control plane on the private network at boot."""
    delay = 1.0
    for attempt in range(1, JWKS_ATTEMPTS + 1):
        try:
            response = await client.get("/.well-known/jwks.json")
            response.raise_for_status()
            return ControlKeys({key["kid"]: _unb64(key["x"]) for key in response.json()["keys"]})
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            if attempt == JWKS_ATTEMPTS:
                raise RuntimeError("the control plane's keys are unreachable") from exc
            log.warning("control plane keys not available yet", extra={"attempt": attempt})
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10.0)
    raise AssertionError("unreachable")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
