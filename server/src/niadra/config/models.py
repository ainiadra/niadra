"""Configuration documents distributed to cells in the signed snapshot (4.7, 4.10).

The control plane validates every document against these models before accepting a diff,
and the cell validates again before applying. Nothing here holds a secret: credentials
live in the cell vault and are referenced by `secret_ref`.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from niadra.config.claims import ClaimContract
from niadra.config.coordination import CoordinationSettings
from niadra.config.measurement import MeasurementSettings
from niadra.config.object_types import ObjectTypeRegistry
from niadra.config.recording import RecordingSettings
from niadra.config.tool_bindings import ToolBindings
from niadra.domain.ingest.masking import ALL_TYPES, REGULATED_FLOOR, Held
from niadra.domain.privacy.purposes import LegalBasis
from niadra.domain.privacy.retention import MIN_DAYS, TENANT_TUNABLE, DataClass
from niadra.domain.serve.lexicon import Concept, Lexicon, category_concepts
from niadra.domain.serve.relative_time import AnchorEntry, AnchorLexicon
from niadra.domain.vocabulary import (
    Audience,
    EventKind,
    Feature,
    HandleType,
    ReceiptKind,
    Sensitivity,
    SubjectKind,
    Verification,
    Visibility,
)

Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.:-]{0,63}$")]


class Doc(BaseModel):
    # Built on first use, as the contracts (`contracts.common.Model`): the control plane validates every
    # section, a cell only the space documents it holds.
    model_config = ConfigDict(extra="forbid", frozen=True, defer_build=True)


class SourceConfig(Doc):
    source_id: UUID
    name: str
    vendor: str | None = None
    channel: str | None = None
    purposes: frozenset[str] = frozenset()
    audience: Audience
    verification_ceiling: Verification = Verification.V2
    confirms_before_acting: bool = False
    navigation_enabled: bool = True
    trusted_action_ops: frozenset[str] = frozenset()
    critical: bool = Field(default=False, description="Critical sources never join a control group (19.7).")


class PolicyCondition(Doc):
    audience: Audience | None = None
    purpose: str | None = None
    subject_kind: SubjectKind | None = None
    category: str | None = None
    sensitivity: Sensitivity | None = None
    visibility: Visibility | None = None
    min_verification: Verification | None = None
    link_role: str | None = None


class PolicyRule(Doc):
    effect: Literal["allow", "mask", "deny"]
    when: PolicyCondition


class CategoryRule(Doc):
    name: Name
    sensitivity: Sensitivity = Sensitivity.NORMAL
    min_verification: Verification = Verification.V1
    purposes: frozenset[str] = frozenset()
    no_customer_allowed: bool = Field(
        default=False, description="Whether `no_customer` packs may carry it (2.2)."
    )
    mirror: bool = Field(
        default=False,
        description="The mirror rule: what the customer said themselves, through a confirmed handle of their "
        "own, reaches a reader from V1 even when the category asks for more; what the company holds about "
        "them (a balance it stated, a record from a system) keeps the category's level. Never applies to a "
        "sensitive category.",
    )


class Policy(Doc):
    version: str
    categories: list[CategoryRule] = Field(default_factory=list)
    rules: list[PolicyRule] = Field(default_factory=list)
    competes_with: dict[str, list[str]] = Field(
        default_factory=dict,
        description="source_id -> the partners it must never read, by handle id or by the partner's profile "
        "id, which covers the handles the partner gains later (21.5).",
    )
    compatible_purposes: dict[Name, list[Name]] = Field(
        default_factory=dict,
        max_length=50,
        description="Purpose compatibility (LGPD art. 6 I, GDPR art. 6(4)), written by the client's legal "
        "team: an item collected for the key may also be read by an agent working for one of the listed "
        "purposes. Unlisted, an item collected for a purpose is read only for that purpose (13, 6.6). The "
        "purposes are the ones the `purposes` document declares.",
    )


class SlotSpec(Doc):
    name: Name
    type: Literal["string", "date", "number", "enum", "boolean"] = "string"
    survivorship: Literal["latest", "source_priority", "most_frequent"] = "latest"
    source_priority: list[str] = Field(default_factory=list)


class PredicateSpec(Doc):
    name: Name
    namespace: str = "tenant"
    category: Name
    single_valued: bool = False
    enum: list[str] | None = None
    about: Literal["customer", "organization"] = Field(
        default="customer",
        description="`organization`: a fact about the organization a contact acts for (its e-mail, its "
        "business hours), kept as the organization's when the contact has one active link, never as the "
        "contact's own.",
    )
    observes: Name | None = Field(
        default=None,
        description="The field of the object's type this predicate is said about (`due_date`): a fact of it "
        "about an object is shown next to the value the system of record holds, never in its place, and one "
        "that differs opens a data issue of class `divergence`.",
    )


class EpisodeCategory(Doc):
    name: Name
    normalized_as: Name


OperationName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


class DeclaredOperation(Doc):
    """An operation of the space with what it means, in the company's words and language."""

    name: OperationName
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ExtractionSchema(Doc):
    version: str
    language: str = "pt-BR"
    slots: list[SlotSpec] = Field(default_factory=list)
    categories: list[CategoryRule] = Field(default_factory=list)
    episode_categories: list[EpisodeCategory] = Field(default_factory=list)
    predicates: list[PredicateSpec] = Field(default_factory=list)
    intents: list[str] = Field(
        default_factory=lambda: [
            "question",
            "complaint",
            "request",
            "cancellation",
            "purchase",
            "support",
            "other",
        ]
    )
    outcomes: list[str] = Field(
        default_factory=lambda: ["resolved", "unresolved", "escalated", "pending", "abandoned"]
    )
    source_overrides: dict[str, list[PredicateSpec]] = Field(default_factory=dict)
    operations: list[OperationName | DeclaredOperation] = Field(
        default_factory=list,
        max_length=100,
        description="The space's own operations an open item may wait for, beyond the product's catalog: "
        "what its agents record and its systems confirm in `fields.operation` (`file`, `approve`). An item "
        "names one so the action or event that does it closes the item, even the first time it happens. "
        "Each is a name, or `{name, description}` with what it means in the company's words "
        '(`{"name": "file", "description": "protocolar uma peça no processo"}`): the extraction reads the '
        "description to tell which operation an item waits for, and without one it names the operation of "
        "an item in the company's own words far less often.",
    )

    @model_validator(mode="after")
    def _operations_once(self) -> "ExtractionSchema":
        names = self.operation_names()
        if len(set(names)) != len(names):
            raise ValueError("extraction.operations names an operation twice")
        return self

    def operation_names(self) -> list[str]:
        return [o if isinstance(o, str) else o.name for o in self.operations]

    def operation_meanings(self) -> dict[str, str]:
        """What each declared operation means, in the company's words, for the ones that say it."""
        return {o.name: o.description for o in self.operations if isinstance(o, DeclaredOperation)}

    def normalize_category(self, category: str) -> str:
        for item in self.episode_categories:
            if item.name == category:
                return item.normalized_as
        return category


class WebhookAuth(Doc):
    # `mtls`: the secret at `secret_ref` is the SHA-256 fingerprint of the source's client certificate, and
    # the source sends to the edge's mTLS address, which asks for the certificate and forwards it.
    mode: Literal["bearer", "hmac", "url_token", "mtls"]
    header: str = "authorization"
    algorithm: Literal["sha256", "sha1"] = "sha256"
    signed_payload: Literal["body", "timestamp.body"] = "body"
    timestamp_header: str | None = None
    tolerance_seconds: int = 300
    secret_ref: str


class HandleExpr(Doc):
    type: HandleType
    value: str = Field(description="JMESPath over the raw payload.")
    scope: str | None = None
    subject_kind: SubjectKind | None = None


class ObjectExpr(Doc):
    type: str
    namespace: str
    id: str = Field(description="JMESPath over the raw payload.")


AnchorTerm = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=60)]


class EventAnchor(Doc):
    """A kind of system event a customer names in a question ("depois da vistoria", "since the delivery"):
    the canonical types it covers (a trailing `*` matches a prefix) and the words for it by language. The
    time words around it ("depois de", "antes de", "desde", "after", "before", "since", "después de") are the
    product's; the terms are the space's, editable like the rest of its configuration."""

    name: Name
    canonical_types: list[Annotated[str, StringConstraints(min_length=1, max_length=120)]] = Field(
        min_length=1
    )
    terms: dict[Literal["pt", "en", "es"], list[AnchorTerm]] = Field(min_length=1)


class SynonymGroup(Doc):
    """Words and phrases a customer uses for one thing ("senha", "passcode", "código de acceso"), by
    language, and the episode categories that thing is evidence of. The lexical slot channel and the count
    line read a turn and a memory line through these groups, without a model."""

    name: Name
    terms: dict[Literal["pt", "en", "es"], list[AnchorTerm]] = Field(min_length=1)
    categories: list[Name] = Field(default_factory=list)


class MappingRule(Doc):
    match: str = Field(description="JMESPath that must be truthy for this rule to apply.")
    kind: EventKind = EventKind.SYSTEM_EVENT
    canonical_type: str | None = None
    canonical_type_expr: str | None = None
    idempotency_key: str
    occurred_at: str
    channel: str = "system"
    conversation_id: str | None = None
    text: str | None = None
    operation: str | None = None
    handles: list[HandleExpr] = Field(default_factory=list)
    object_refs: list[ObjectExpr] = Field(default_factory=list)
    fields: dict[str, str] = Field(default_factory=dict)
    anchor_terms: list[AnchorTerm] = Field(
        default_factory=list,
        description="Words a customer uses for the event this rule maps (`vistoria`, `inspection`): a "
        'question such as "after the inspection" is then read against the customer\'s latest event of '
        "`canonical_type`. Added to the space's `event_anchors`.",
    )

    @model_validator(mode="after")
    def _anchor_terms_name_one_type(self) -> "MappingRule":
        if self.anchor_terms and not self.canonical_type:
            raise ValueError("`anchor_terms` needs a fixed `canonical_type`")
        return self


class Mapping(Doc):
    """A versioned webhook/batch template (5.2): the raw payload is always kept, so it can be remapped."""

    source_id: UUID
    version: str
    auth: WebhookAuth | None = None
    verification_challenge: str | None = Field(
        default=None, description="Query param echoed on GET challenges."
    )
    rules: list[MappingRule]


class TaskView(Doc):
    """A task view (`task:<name>`), or the space's own `custom` view, one per space."""

    name: Annotated[str, StringConstraints(pattern=r"^(task:[a-z0-9_]{1,40}|custom)$")]
    object_types: list[str] = Field(default_factory=list)
    purposes: frozenset[str] = frozenset()
    budget_tokens: int = Field(default=900, ge=100, le=4000)
    template: str | None = Field(
        default=None,
        max_length=2000,
        description="The layout of the customer block: text with section names in braces (`{facts}`, "
        "`{pending}`, `{episodes}`...), nothing else; `{{` and `}}` write a brace. The opening line and "
        "the tags around the block stay.",
    )
    include_agent_memory: bool = Field(
        default=False,
        description="Appends up to 200 tokens of the agent's own notes tagged with the view's object types, "
        "for agents whose prompt cannot change (10, C.3).",
    )


class TraitRuleConfig(Doc):
    rule_id: Name
    catalog: Literal[
        "recurring_complaint",
        "broken_company_promise",
        "asked_to_cancel",
        "preferred_channel",
        "does_not_answer_calls",
        "escalated_to_human",
        "declining_sentiment",
        "overdue_invoices",
    ]
    version: str
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)
    purposes: frozenset[str] = frozenset()


class TriggerRuleConfig(Doc):
    rule_id: Name
    condition: Literal[
        "promise.overdue",
        "open_item.due",
        "trait.present",
        "sentiment.drop",
        "identity.ambiguous",
        "fact.contradicted_by_system_event",
        "recontact",
        "object.timer_due",
        "context_use.repetition_rate",
        "source.silent",
        "source.gap",
    ]
    version: str
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)
    endpoint_id: str
    window_hours: int = Field(default=24, ge=1, le=24 * 90)
    late_policy: Literal["deliver_marked", "suppress"] = "deliver_marked"


class WebhookEndpoint(Doc):
    endpoint_id: str
    url: str
    source_id: UUID = Field(
        description="The endpoint belongs to a source, whose audience limits the payload."
    )
    event_types: list[str] = Field(
        description="Event types for a `standard` endpoint; receipt kinds (`read`, `history`, `denied`, "
        "`admin`, `analytics`, `holdout`) or `*` for a SIEM stream."
    )
    secret_refs: list[str] = Field(min_length=1, max_length=2, description="Two during rotation (7.5).")
    enabled: bool = True
    format: Literal["standard", "ocsf", "hec"] = Field(
        default="standard",
        description="`standard`: Standard Webhooks events (7.5). `ocsf` and `hec`: the receipts stream to "
        "a SIEM, as OCSF API Activity events one per line, or wrapped for an HTTP Event Collector (5.13).",
    )
    authorization_ref: str | None = Field(
        default=None,
        description="A secret sent as the Authorization header, e.g. `Splunk <token>` for a collector.",
    )

    @field_validator("url")
    @classmethod
    def _https_only(cls, url: str) -> str:
        if not url.startswith("https://"):
            raise ValueError("webhook endpoints must use https")
        return url

    @model_validator(mode="after")
    def _siem_kinds(self) -> "WebhookEndpoint":
        if self.format != "standard":
            unknown = set(self.event_types) - {"*"} - {k.value for k in ReceiptKind}
            if unknown:
                raise ValueError("a SIEM stream takes receipt kinds or `*` as its event types")
        return self


class ExportSchedule(Doc):
    schedule_id: str
    destination_kind: Literal["s3", "gcs", "azure"]
    bucket: str
    prefix: str = ""
    region: str
    endpoint_url: str | None = Field(
        default=None,
        description="S3-protocol endpoint of the customer's store; empty means the AWS endpoint of `region`.",
    )
    tables: list[str]
    interval_hours: int = Field(default=24, ge=1)
    pseudonymize: bool = False
    credential_ref: str

    @field_validator("endpoint_url")
    @classmethod
    def _https_endpoint(cls, url: str | None) -> str | None:
        if url is not None and not url.startswith("https://"):
            raise ValueError("export destinations must use https")
        return url


class ReviewSampling(Doc):
    base_rate: float = Field(default=0.01, ge=0, le=0.1)
    boosted_rate: float = Field(default=0.1, ge=0, le=1)
    boost_on: frozenset[str] = frozenset(
        {
            "new_source",
            "new_extractor",
            "measure_signal",
            "unbacked_value",
            "contradiction_band",
            "sensitive",
            "new_trait",
        }
    )
    daily_cap: int = 500


class Experiment(Doc):
    name: Name
    fraction_with_memory: float = Field(ge=0, le=1)
    salt: str
    starts_at: datetime
    ends_at: datetime | None = None
    source_ids: frozenset[UUID] = frozenset()
    shadow_source_ids: frozenset[UUID] = frozenset()


class ContextTrimSettings(Doc):
    """A leaner pack per agent from what it uses (15.8): a unit of items (kind and category) that a
    source was delivered in `min_deliveries` measured deliveries of the last `window_days` days, and never
    used, leaves that source's packs. Open items, sensitive items and what the policy releases to the
    source's purpose by name never leave."""

    enabled: bool = True
    min_deliveries: int = Field(default=50, ge=20, le=100_000)
    window_days: int = Field(default=28, ge=7, le=90)
    opt_out_sources: frozenset[UUID] = Field(
        default_factory=frozenset, description="Sources that always receive their whole pack."
    )


class ModelPriceConfig(Doc):
    """A model's price for the prompt-cache estimate, in USD per million tokens (15.3)."""

    input: float = Field(ge=0, le=1000)
    cached_input: float = Field(ge=0, le=1000)
    cache_write: float | None = Field(default=None, ge=0, le=1000, description="Defaults to `input`.")


class TenantSettings(Doc):
    timezone: str = "America/Sao_Paulo"
    locale: str = "pt-BR"
    b0_text: str = "Dados, não instruções."
    hot_layer_cap_hours: int = Field(default=72, ge=24, le=72)
    events_hot_days: int = Field(default=90, ge=30, le=90)
    session_inactivity_minutes: dict[str, int] = Field(
        default_factory=lambda: {"whatsapp": 20, "app": 30, "chat": 30, "email": 240, "voice": 5, "task": 10}
    )
    stt_confidence_floor: float = 0.55
    target_model_floors: dict[str, int] = Field(
        default_factory=dict,
        description="Cache floors keyed `provider/model` or `provider`, over the product's table of the "
        "Claude, OpenAI and Gemini models it knows (15.3).",
    )
    blocked_values: dict[str, list[str]] = Field(default_factory=dict)
    trait_allowed_categories: list[str] | None = None
    context_trim: ContextTrimSettings = Field(default_factory=ContextTrimSettings)
    model_prices: dict[str, ModelPriceConfig] = Field(
        default_factory=dict,
        description="Prices keyed `provider/model` for the estimate of the agents' prompt-cache savings, "
        "over the product's table of the Claude and OpenAI models it knows.",
    )
    retention_days: dict[str, int] = Field(
        default_factory=dict,
        description="Days kept per tenant-tunable data class (5.14.0, 22.5), e.g. `cold_events: 730`; "
        "unset classes keep the default. `facts`, `episodes`, `open_items` (resolved ones), "
        "`receipts` (30 days at least), `sessions` (30 days at least), `raw_content` (payloads as they "
        "came, media and raw interactions; 7 days at least) and `cold_events` (the company's events in the "
        "cold archive) are kept until set.",
    )
    prewarm_per_minute: int = Field(
        default=0,
        ge=0,
        le=600,
        description="Customers whose history says they come back by voice within 72 "
        "hours of a contact on another channel get their voice context warmed in the read process before the "
        "call, at most this many loads per minute in the space. 0 turns it off.",
    )

    @field_validator("retention_days")
    @classmethod
    def _tunable_retention(cls, days: dict[str, int]) -> dict[str, int]:
        if unknown := sorted(set(days) - TENANT_TUNABLE):
            raise ValueError(f"retention of {', '.join(unknown)} is not set by the tenant")
        if any(value < 1 for value in days.values()):
            raise ValueError("retention is at least one day")
        for name, value in days.items():
            if value < (floor := MIN_DAYS.get(DataClass(name), 1)):
                raise ValueError(f"retention of {name} is at least {floor} days")
        return days


LLM_BUDGET_METRIC = "llm_daily_micros"
"""A tenant quota named `llm_daily_micros:<purpose>` (period `day`) sets that purpose's daily AI spend
ceiling in every space of the tenant, in millionths of a US dollar: the contract's number."""


class SpaceLimits(Doc):
    """What the tenant's contract sets for a space; the control plane fills it from the tenant's quotas."""

    llm_daily_micros: dict[str, Annotated[int, Field(ge=0)]] = Field(
        default_factory=dict, description="Daily AI spend ceiling per purpose (`extraction`, `assistant`)."
    )


class AgentMemorySettings(Doc):
    """The agents' own working notes (10, C). On by default, so a space works with no configuration; a space
    turns it off by a diff."""

    enabled: bool = True
    writes: Literal["agent", "human_only"] = Field(
        default="agent",
        description="`human_only`: an agent's `remember` becomes a proposal a person approves.",
    )


MediaType = Literal[
    "application/pdf",
    "image/jpeg",
    "image/png",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
]


class MediaSettings(Doc):
    """Text taken from files and images inside the cell, before the redactor (10, D.1). Empty: none."""

    extract_text_from: list[MediaType] = Field(
        default_factory=list,
        description="The types whose text is read, told by the file's bytes: PDF (its text layer, and OCR "
        "for scanned pages), JPEG and PNG (OCR), DOCX, XLSX and PPTX. Macro-enabled Office files are never "
        "read.",
    )


class IngestMaskingSettings(Doc):
    """What ingestion holds back from text before anything is stored (12-AI-MEMORY, WP-A): card numbers
    (the last four digits stay), card security codes, passwords and credentials inside URLs. All four by
    default; the security team may drop a type, except on the Regulado plan, whose floor always holds."""

    types: list[Held] = Field(
        default_factory=lambda: sorted(ALL_TYPES),
        max_length=len(ALL_TYPES),
        description="Any of `card`, `cvv`, `password`, `secret`. On the Regulado plan `card`, `cvv` and "
        "`password` are masked whatever this list says.",
    )

    @field_validator("types")
    @classmethod
    def _distinct(cls, types: list[Held]) -> list[Held]:
        return sorted(set(types))


GuidanceText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
Topic = Annotated[str, StringConstraints(min_length=1, max_length=120)]


class SourceGuidance(Doc):
    """What one source's conversations add to the space's guidance: its instructions come after the
    space's, and its topics join the space's lists."""

    instructions: GuidanceText | None = None
    include: list[Topic] = Field(default_factory=list, max_length=20)
    exclude: list[Topic] = Field(default_factory=list, max_length=20)


class ExtractionGuidance(Doc):
    """The company's words on what extraction should focus on and leave out (5.7). They narrow and focus
    the extraction; they never override its rules: evidence for every item, the space's predicates, and
    sensitive data only under a sensitive predicate."""

    instructions: GuidanceText | None = None
    include: list[Topic] = Field(
        default_factory=list,
        max_length=20,
        description="Topics to capture with care, e.g. `delivery windows`.",
    )
    exclude: list[Topic] = Field(
        default_factory=list, max_length=20, description="Topics to leave out, e.g. `small talk`."
    )
    sources: dict[str, SourceGuidance] = Field(
        default_factory=dict,
        max_length=50,
        description="By source id or source name, like the schema's `source_overrides`.",
    )


class PurposeSpec(Doc):
    """One purpose the company processes customer data for, with its legal basis and how long it keeps
    what it collects for it."""

    name: Name
    legal_basis: LegalBasis
    retention_days: int | None = Field(
        default=None,
        ge=1,
        le=36_500,
        description="Days an item collected for this purpose is kept after it was last confirmed (a fact), "
        "ended (a conversation, a value) or closed (a promise). Empty: the purpose sets no limit.",
    )
    description: Annotated[str, StringConstraints(max_length=500)] | None = None


class PurposeSettings(Doc):
    """Why each memory item is collected, on which legal basis, and for how long (13, 6.6). Empty: items
    carry no purpose and reads, retention and the access package ignore purposes.

    An item is tagged when written, by the first rule that names a purpose: its category (a fact's category
    or its conversation's normalized category), the canonical types of the system events it came from, the
    collecting source's `purposes`, then `default`. Only purposes declared here count."""

    version: str = "0"
    purposes: list[PurposeSpec] = Field(default_factory=list, max_length=50)
    categories: dict[Name, list[Name]] = Field(
        default_factory=dict,
        max_length=500,
        description="Fact category or conversation category -> the purposes it is collected for.",
    )
    event_types: dict[Annotated[str, StringConstraints(min_length=1, max_length=120)], list[Name]] = Field(
        default_factory=dict,
        max_length=500,
        description="Canonical event type (a trailing `*` matches a prefix) -> the purposes of what it "
        "brings: facts and promises whose evidence includes such an event.",
    )
    default: list[Name] = Field(
        default_factory=list,
        max_length=10,
        description="Purposes of an item no other rule names. Empty leaves such items without a purpose, "
        "readable by every reader; the Console counts them so the map can be completed.",
    )

    @model_validator(mode="after")
    def _declared(self) -> "PurposeSettings":
        names = [p.name for p in self.purposes]
        if len(names) != len(set(names)):
            raise ValueError("a purpose is declared twice")
        named = {p for purposes in (*self.categories.values(), *self.event_types.values()) for p in purposes}
        if unknown := sorted((named | set(self.default)) - set(names)):
            raise ValueError(f"undeclared purposes: {', '.join(unknown)}")
        return self


class FeatureSettings(Doc):
    """The agent features the space has on (7.7, 24.8): their routes and hooks answer only for a feature
    listed here. Every feature is on by default, so a space works with no configuration; a space lists fewer
    to turn some off."""

    enabled: frozenset[Feature] = frozenset(Feature)


class SpaceConfig(Doc):
    """Everything a cell needs to serve one space."""

    space_id: UUID
    tenant_id: UUID
    region: str
    environment: Literal["sandbox", "production"]
    settings: TenantSettings = Field(default_factory=TenantSettings)
    sources: list[SourceConfig] = Field(default_factory=list)
    policy: Policy = Field(default_factory=lambda: _starter().STARTER_POLICY.model_copy(deep=True))
    extraction: ExtractionSchema = Field(
        default_factory=lambda: _starter().STARTER_EXTRACTION.model_copy(deep=True)
    )
    mappings: list[Mapping] = Field(default_factory=list)
    views: list[TaskView] = Field(default_factory=list)
    trait_rules: list[TraitRuleConfig] = Field(default_factory=list)
    trigger_rules: list[TriggerRuleConfig] = Field(default_factory=list)
    webhook_endpoints: list[WebhookEndpoint] = Field(default_factory=list)
    export_schedules: list[ExportSchedule] = Field(default_factory=list)
    review_sampling: ReviewSampling = Field(default_factory=ReviewSampling)
    experiments: list[Experiment] = Field(default_factory=list)
    limits: SpaceLimits | None = Field(
        default=None,
        description="Set by the contract, never by a diff.",
    )
    agent_memory: AgentMemorySettings = Field(default_factory=AgentMemorySettings)
    media: MediaSettings = Field(default_factory=MediaSettings)
    event_anchors: list[EventAnchor] = Field(
        default_factory=lambda: [a.model_copy(deep=True) for a in _starter().STARTER_ANCHORS],
        description="The system events a question can be anchored on, with the words for each.",
    )
    synonyms: list[SynonymGroup] = Field(
        default_factory=lambda: [g.model_copy(deep=True) for g in _starter().STARTER_SYNONYMS],
        description="Words that mean the same thing to this space's customers, by language.",
    )
    extraction_guidance: ExtractionGuidance = Field(default_factory=ExtractionGuidance)
    ingest_masking: IngestMaskingSettings = Field(default_factory=IngestMaskingSettings)
    purposes: PurposeSettings = Field(
        default_factory=lambda: _starter().STARTER_PURPOSES.model_copy(deep=True),
        description="Without a declaration: one purpose, `service`, on the contract basis, kept 730 days.",
    )
    kms_key_arn: str | None = Field(
        default=None,
        description="The customer's key in KMS, set by the contract, never by a diff.",
    )
    tier: Literal["t1", "t2"] | None = Field(
        default=None,
        description="The database tier the cell creates the space's database with, set by placement: t1 "
        "on an instance of its own, t2 on a cell dedicated to the tenant. Absent for t0, the "
        "shared instance.",
    )
    instance: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9-]{0,39}$",
        description="The cell's database instance, by name, that holds the space's database; absent for "
        "the cell's own instance (`primary`).",
    )
    # The agent features' sections (7.7), each defaulted.
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    recording: RecordingSettings = Field(default_factory=RecordingSettings)
    object_types: ObjectTypeRegistry = Field(default_factory=ObjectTypeRegistry)
    tool_bindings: ToolBindings = Field(default_factory=ToolBindings)
    coordination: CoordinationSettings = Field(default_factory=CoordinationSettings)
    claim_contract: ClaimContract = Field(
        default_factory=lambda: _starter().STARTER_CLAIM_CONTRACT.model_copy(deep=True),
        description="Without a declaration: dates and amounts checked against the turn record and counted.",
    )
    measurement: MeasurementSettings = Field(default_factory=MeasurementSettings)

    @model_validator(mode="before")
    @classmethod
    def _source_purposes(cls, data: Any) -> Any:
        """What each source reads for when nobody said (zero touch): a source that declares no
        purposes reads for `DEFAULT_SOURCE_PURPOSES`; one that declares its purposes reads for exactly those,
        whatever purposes the space keeps. A marketing source never reads what was collected to serve the
        customer because the space has not declared its purposes yet."""
        if not isinstance(data, dict) or not data.get("sources"):
            return data
        default = _starter().DEFAULT_SOURCE_PURPOSES
        sources = []
        for raw in data["sources"]:
            source = raw.model_dump() if isinstance(raw, SourceConfig) else dict(raw)
            sources.append({**source, "purposes": frozenset(source.get("purposes") or ()) or default})
        return {**data, "sources": sources}

    def source(self, source_id: UUID) -> SourceConfig | None:
        return next((s for s in self.sources if s.source_id == source_id), None)

    def masking_types(self) -> frozenset[Held]:
        """What ingestion masks in this space: the settings, never below the Regulado plan's floor (a cell
        dedicated to the tenant, tier t2)."""
        chosen = frozenset(self.ingest_masking.types)
        return chosen | REGULATED_FLOOR if self.tier == "t2" else chosen

    def mapping_for(self, source_id: UUID) -> Mapping | None:
        return next((m for m in self.mappings if m.source_id == source_id), None)

    def endpoint(self, endpoint_id: str) -> WebhookEndpoint | None:
        return next((e for e in self.webhook_endpoints if e.endpoint_id == endpoint_id), None)

    def anchor_lexicon(self) -> AnchorLexicon:
        """The space's anchors, then one per mapped canonical type whose rule declares its own words."""
        entries = [
            AnchorEntry(
                a.name,
                tuple(a.canonical_types),
                tuple(term for language in sorted(a.terms) for term in a.terms[language]),
            )
            for a in self.event_anchors
        ]
        mapped: dict[str, list[str]] = {}
        for mapping in self.mappings:
            for rule in mapping.rules:
                if rule.anchor_terms and rule.canonical_type:
                    mapped.setdefault(rule.canonical_type, []).extend(rule.anchor_terms)
        entries += [
            AnchorEntry(f"mapped:{event_type}", (event_type,), tuple(dict.fromkeys(terms)))
            for event_type, terms in sorted(mapped.items())
        ]
        return AnchorLexicon.build(entries)

    def lexicon(self) -> Lexicon:
        """The space's synonym groups, then a concept for each of its episode categories no group points
        to; compiled once per distinct list, and looked up once per configuration document."""
        if (hit := _LEXICONS.get(id(self))) is not None and hit[0] is self:
            return hit[1]
        concepts = [
            Concept(g.name, tuple(t for lang in sorted(g.terms) for t in g.terms[lang]), tuple(g.categories))
            for g in self.synonyms
        ]
        names = [c.normalized_as for c in self.extraction.episode_categories]
        lexicon = Lexicon.build([*concepts, *category_concepts(names, concepts)])
        # The entry holds the document, so its id is not reused while the entry lives.
        _LEXICONS[id(self)] = (self, lexicon)
        while len(_LEXICONS) > _LEXICONS_MAX:
            _LEXICONS.pop(next(iter(_LEXICONS)))
        return lexicon


_LEXICONS: dict[int, tuple[SpaceConfig, Lexicon]] = {}
_LEXICONS_MAX = 256


class SnapshotManifest(Doc):
    """Signed with Ed25519 by the control plane; documents are addressed by content hash."""

    cell_id: str
    version: int
    min_cell_version: str | None = None
    created_at: datetime
    spaces: dict[str, str] = Field(description="space_id -> sha256 of the SpaceConfig document.")
    removed: dict[str, str] = Field(
        default_factory=dict,
        description="space_id -> when it was deleted (ISO 8601): the cell drops these spaces' databases, "
        "and tells them apart from spaces that are only missing from a snapshot.",
    )
    moved: dict[str, str] = Field(
        default_factory=dict,
        description="space_id -> the cell it moved to: requests for it answer 421 here, and the "
        "SDKs retry on a fresh connection, which the edge routes to the new cell. Absent when empty.",
    )


def _starter() -> Any:
    # Imported on first use: the starter documents are built from the models of this module.
    from niadra.config import starter

    return starter
