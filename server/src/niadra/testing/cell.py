"""A whole cell in memory: the same wiring as production with fakes behind every port.

Used by unit tests. Services receive only the ports they need, so a test builds exactly the service
under test from these parts.
"""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from uuid import UUID

from niadra.adapters.memory.fakes import (
    DeterministicDecider,
    DeterministicModels,
    DictSecrets,
    FixedClock,
    InMemoryBus,
    InMemoryCache,
    InMemoryHotLayer,
    InMemoryObjectStore,
    RecordingEgress,
    ScriptedLlm,
    ServingSpaces,
    StaticConfig,
)
from niadra.adapters.memory.uow import InMemoryUnitOfWork, InMemoryUnitOfWorkFactory
from niadra.adapters.outbound.keys.local import LocalKeyVault
from niadra.config.claims import ClaimContract
from niadra.config.models import (
    AgentMemorySettings,
    CategoryRule,
    EpisodeCategory,
    ExtractionSchema,
    FeatureSettings,
    Policy,
    PolicyCondition,
    PolicyRule,
    PredicateSpec,
    PurposeSettings,
    SourceConfig,
    SpaceConfig,
)
from niadra.domain.common.space import SourcePrincipal, SpaceRef
from niadra.domain.control.credentials import format_key, new_key_id, new_secret
from niadra.domain.edge.keys import ParsedKey, parse_source_key
from niadra.domain.vocabulary import Audience, Environment, Scope, Sensitivity, Verification

SPACE_ID = UUID("0192f5a0-0000-7000-8000-00000000a001")
TENANT_ID = UUID("0192f5a0-0000-7000-8000-00000000b001")
WHATSAPP_SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c001")
VOICE_SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c002")
BILLING_SOURCE = UUID("0192f5a0-0000-7000-8000-00000000c003")


def sample_config(space_id: UUID = SPACE_ID) -> SpaceConfig:
    return SpaceConfig(
        space_id=space_id,
        tenant_id=TENANT_ID,
        region="us-east-2",
        environment="sandbox",
        sources=[
            SourceConfig(
                source_id=WHATSAPP_SOURCE,
                name="whatsapp-bot",
                vendor="A",
                channel="whatsapp",
                purposes=frozenset({"support"}),
                audience=Audience.CUSTOMER_AGENT,
            ),
            SourceConfig(
                source_id=VOICE_SOURCE,
                name="voice-agent",
                vendor="B",
                channel="voice",
                purposes=frozenset({"support"}),
                audience=Audience.CUSTOMER_AGENT,
                verification_ceiling=Verification.V3,
            ),
            SourceConfig(
                source_id=BILLING_SOURCE,
                name="billing-agent",
                channel="erp",
                purposes=frozenset({"billing"}),
                audience=Audience.INTERNAL_AGENT,
                trusted_action_ops=frozenset({"credit"}),
            ),
        ],
        policy=Policy(
            version="p1",
            categories=[
                CategoryRule(name="contact", min_verification=Verification.V1),
                CategoryRule(name="contract", min_verification=Verification.V1),
                CategoryRule(
                    name="billing",
                    min_verification=Verification.V2,
                    purposes=frozenset({"billing", "support"}),
                    no_customer_allowed=True,
                ),
                CategoryRule(
                    name="health", sensitivity=Sensitivity.SENSITIVE, min_verification=Verification.V3
                ),
            ],
            rules=[PolicyRule(effect="deny", when=PolicyCondition(sensitivity=Sensitivity.SENSITIVE))],
        ),
        extraction=ExtractionSchema(
            version="s1",
            categories=[CategoryRule(name="contact"), CategoryRule(name="billing")],
            episode_categories=[
                EpisodeCategory(name="technical_visit", normalized_as="technical_visit"),
                EpisodeCategory(name="invoice", normalized_as="billing"),
            ],
            predicates=[
                PredicateSpec(name="preferred_name", category="contact", single_valued=True),
                PredicateSpec(name="disputed_invoice", category="billing"),
            ],
        ),
        # A bare space: the tests turn on the features, notes, purposes and claim checks they exercise.
        # What a space nobody configured runs with (zero touch) is tested in its own module.
        features=FeatureSettings(enabled=frozenset()),
        agent_memory=AgentMemorySettings(enabled=False),
        purposes=PurposeSettings(),
        claim_contract=ClaimContract(),
    )


def principal(
    source_id: UUID = WHATSAPP_SOURCE,
    config: SpaceConfig | None = None,
    scopes: frozenset[Scope] | None = None,
) -> SourcePrincipal:
    config = config or sample_config()
    source = config.source(source_id)
    assert source is not None
    return SourcePrincipal(
        space=SpaceRef(config.space_id, config.tenant_id, config.region, Environment(config.environment)),
        source_id=source_id,
        key_id=f"k_{source.name}",
        scopes=scopes or frozenset(Scope) - {Scope.ADMIN, Scope.ANALYTICS},
        audience=source.audience,
        verification_ceiling=source.verification_ceiling,
        purposes=source.purposes,
        vendor=source.vendor,
        confirms_before_acting=source.confirms_before_acting,
        trusted_action_ops=source.trusted_action_ops,
    )


def mint_source_key(environment: Environment, region: str, space_slug: str) -> tuple[str, ParsedKey]:
    """A new key as the control plane issues it, parsed as the edge reads it."""
    token = format_key(environment, region, space_slug, new_key_id(), new_secret())
    return token, parse_source_key(token)


@dataclass
class Cell:
    uow: InMemoryUnitOfWorkFactory = field(default_factory=InMemoryUnitOfWorkFactory)
    clock: FixedClock = field(default_factory=FixedClock)
    keys: LocalKeyVault = field(default_factory=lambda: LocalKeyVault(b"k" * 32))
    secrets: DictSecrets = field(default_factory=DictSecrets)
    bus: InMemoryBus = field(default_factory=InMemoryBus)
    hot: InMemoryHotLayer = field(default_factory=InMemoryHotLayer)
    cache: InMemoryCache = field(default_factory=InMemoryCache)
    objects: InMemoryObjectStore = field(default_factory=InMemoryObjectStore)
    llm: ScriptedLlm = field(default_factory=ScriptedLlm)
    models: DeterministicModels = field(default_factory=DeterministicModels)
    decider: DeterministicDecider = field(default_factory=DeterministicDecider)
    egress: RecordingEgress = field(default_factory=RecordingEgress)
    config: StaticConfig = field(default_factory=lambda: StaticConfig(sample_config()))
    databases: ServingSpaces = field(default_factory=ServingSpaces)

    def __post_init__(self) -> None:
        # The store dates what it writes by the cell's clock, so a retention by age reads test time.
        self.objects.now = self.clock.now


class PatchedUnitOfWorkFactory:
    """Replaces some repositories of the in-memory unit of work with test doubles.

    Lets a module's tests fake another module's port, e.g. `memory=FakeMemory(facts)`, without
    depending on that module's in-memory implementation. A repository of the cell database, like
    `health=FakeHealth()`, is patched into the cell transactions.
    """

    CELL_REPOS = frozenset({"config", "keys", "health", "watermarks", "dead", "directory"})

    def __init__(self, base: InMemoryUnitOfWorkFactory, **repos: object) -> None:
        self._base = base
        self._space = {k: v for k, v in repos.items() if k not in self.CELL_REPOS}
        self._cell = {k: v for k, v in repos.items() if k in self.CELL_REPOS}

    def __call__(self, space_id: UUID):  # type: ignore[no-untyped-def]
        return self._patched(self._base(space_id), self._space)

    def system(self, space_id: UUID):  # type: ignore[no-untyped-def]
        return self._patched(self._base.system(space_id), self._space)

    def cell(self):  # type: ignore[no-untyped-def]
        return self._patched(self._base.cell(), self._cell)

    @staticmethod
    @asynccontextmanager
    async def _patched(
        base_cm: AbstractAsyncContextManager[InMemoryUnitOfWork], repos: dict[str, object]
    ) -> AsyncIterator[InMemoryUnitOfWork]:
        async with base_cm as uow:
            uow._repos.update(repos)
            yield uow

    @property
    def store(self):  # type: ignore[no-untyped-def]
        return self._base.store
