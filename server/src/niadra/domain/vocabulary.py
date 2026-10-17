"""The contract vocabulary (architecture 7.6).

Every enum value here travels through the public API, the database and the SDKs,
so values are ASCII English and never change meaning within `/v1`.
"""

from enum import StrEnum


class EventKind(StrEnum):
    MESSAGE = "message"
    SYSTEM_EVENT = "system_event"
    ACTION = "action"


class Speaker(StrEnum):
    CUSTOMER = "customer"
    AI_AGENT = "ai_agent"
    HUMAN_AGENT = "human_agent"
    SYSTEM = "system"


class SpokenBy(StrEnum):
    """Whose words a memory item is, from the turns it came from (the mirror rule, 5.12)."""

    CUSTOMER = "customer"
    """Only the customer's turns."""
    MIXED = "mixed"
    """The customer spoke and the company answered, stating nothing it holds about them."""
    COMPANY = "company"
    """The company alone, or it stated what it holds about the customer (a balance, an amount, a code)."""


class Visibility(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"


class SubjectKind(StrEnum):
    PERSON = "person"
    ACCOUNT = "account"
    PARTNER = "partner"
    STAFF = "staff"
    """A person who works for the company, named by a system id of the company's own systems:
    a conversation with them is internal. Never a customer's identifier: a phone, an e-mail or a
    document always names a customer."""

    @property
    def is_organization(self) -> bool:
        return self in (SubjectKind.ACCOUNT, SubjectKind.PARTNER)


class HandleType(StrEnum):
    PHONE_E164 = "phone_e164"
    WA_ID = "wa_id"
    WA_JID = "wa_jid"
    WA_LID = "wa_lid"
    WA_BSUID = "wa_bsuid"
    EMAIL = "email"
    GOV_ID_HMAC = "gov_id_hmac"
    APP_USER_ID = "app_user_id"
    SYSTEM_ID = "system_id"
    ORG_REGISTRY_HMAC = "org_registry_hmac"
    EMAIL_DOMAIN = "email_domain"
    ANON_ID = "anon_id"


class HandleStatus(StrEnum):
    ACTIVE = "active"
    AMBIGUOUS = "ambiguous"
    BLOCKED = "blocked"
    SUSPECTED_RECYCLED = "suspected_recycled"


class AssertionMethod(StrEnum):
    EXPLICIT_IDENTIFY = "explicit_identify"
    OTP = "otp"
    LOGIN = "login"
    SYSTEM_IMPORT = "system_import"
    SAME_EVENT = "same_event"
    CO_OCCURRENCE = "co_occurrence"
    CHANNEL_ROTATION = "channel_rotation"
    ACCEPTED_SUGGESTION = "accepted_suggestion"
    EXTERNAL_RESOLVER = "external_resolver"
    DECLARED = "declared"


class FactStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    QUARANTINED = "quarantined"
    RETRACTED = "retracted"


class OpenItemStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    OVERDUE = "overdue"


class OpenItemKind(StrEnum):
    """What an open item is: a promise someone made, a dispute the customer raised (a charge contested, a
    delivery that came damaged) or a request they made. Each closes the same way, by what was done."""

    PROMISE = "promise"
    DISPUTE = "dispute"
    REQUEST = "request"


class MemoryAudience(StrEnum):
    """Who may read a row bound to an object besides the person it is stored under: `shared` reaches every
    participant of the object, a customer-facing agent's read included; `internal` reaches the internal
    readers only. Computed by rule when the row is written, never by a model."""

    SHARED = "shared"
    INTERNAL = "internal"


HANDOFF_OUTCOME = "handoff.outcome"
"""The operation of the action a handoff's outcome records in the subject's memory."""


class ActionStatus(StrEnum):
    DECLARED = "declared"
    CONFIRMED = "confirmed"
    DIVERGENT = "divergent"
    QUARANTINED = "quarantined"


class RelationType(StrEnum):
    SUPERSEDES = "supersedes"
    CONTRADICTS = "contradicts"


class Verification(StrEnum):
    """Session verification levels (2.2). `no_customer` sits outside the V0-V4 scale."""

    V0 = "V0"
    V1 = "V1"
    V2 = "V2"
    V3 = "V3"
    V4 = "V4"
    NO_CUSTOMER = "no_customer"

    @property
    def rank(self) -> int:
        return -1 if self is Verification.NO_CUSTOMER else int(self.value[1])


class Audience(StrEnum):
    CUSTOMER_AGENT = "customer_agent"
    INTERNAL_AGENT = "internal_agent"
    HUMAN = "human"
    REVIEWER = "reviewer"
    ANALYST = "analyst"
    TRIGGER_TARGET = "trigger_target"
    EXPORT = "export"


class ExclusionReason(StrEnum):
    POLICY = "policy"
    VERIFICATION = "verification"
    BUDGET = "budget"
    QUARANTINE = "quarantine"


class Scope(StrEnum):
    TRACK = "track"
    ACT = "act"
    CONTEXT = "context"
    SEARCH = "search"
    IDENTIFY = "identify"
    ADMIN = "admin"
    ANALYTICS = "analytics"
    AGENT_MEMORY = "agent_memory"  # read the agent's own notes (10, C.6)
    AGENT_MEMORY_WRITE = "agent_memory:write"
    # The agent core wave (7.7): turn records and replay, ownership and effects, state pushed by a
    # system of record, the agent's working state.
    REPLAY = "replay"
    COORDINATE = "coordinate"
    STATE_PUSH = "state:push"
    AGENT_STATE = "agent_state"
    # A system of record that knows who works for whom (a CRM, an HR system) links people to organizations
    # and ends those links (21.2), without the admin scope; never a customer-facing source.
    IDENTITY_LINK = "identity:link"


class Environment(StrEnum):
    SANDBOX = "sandbox"
    PRODUCTION = "production"


class Sensitivity(StrEnum):
    NORMAL = "normal"
    RESTRICTED = "restricted"
    SENSITIVE = "sensitive"


class DeliveryPath(StrEnum):
    """Which read tier answered a `context()` call (5.11)."""

    T0 = "t0"
    T1 = "t1"
    T2 = "t2"
    T3 = "t3"
    T4 = "t4"
    HOLDOUT = "holdout"
    NOT_MODIFIED = "not_modified"


class ReceiptKind(StrEnum):
    READ = "read"
    HISTORY = "history"
    ANALYTICS = "analytics"
    ADMIN = "admin"
    HOLDOUT = "holdout"
    DENIED = "denied"  # a read the policy refused: the attempt is on the chain too (5.13)
    AGENT_MEMORY = "agent_memory"  # a read of the agent's own notes: the block or a search (10, C.4)
    QUALITY = "quality"  # a read of a turn record or of a replay case (24.5)


class ReceiptSurface(StrEnum):
    PACK = "pack"  # the pinned context pack itself; live and delta travel outside it (5.11)
    LIVE = "live"
    DELTA = "delta"
    SLOTS = "slots"  # the per-turn slots (11, 3.3), outside the pinned pack like delta
    SEARCH = "search"
    TIMELINE = "timeline"
    OPEN = "open"
    OBJECT = "object"
    CONSOLE = "console"  # a governance action in the Console or through an admin key (5.22)
    AGENT_BLOCK = "agent_block"  # the agent memory block that goes into the prompt
    AGENT_SEARCH = "agent_search"  # the search_agent_memory tool
    # The agent core wave (24.5): the blocks `include` adds to a context read, and its own routes' reads.
    STATE = "state"
    CONSTRAINTS = "constraints"
    COORDINATION = "coordination"
    TURN = "turn"
    AGENT_STATE = "agent_state"
    BUDGET = "budget"


class Feature(StrEnum):
    """The agent features of a space (7.7, 24.8): every one is on by default, so a space works with no
    configuration; the space's `features` document lists fewer to turn some off, and
    `GET /v1/sdk/profile` announces what is on."""

    TURNS = "turns"
    STATE = "state"
    AGENT_STATE = "agent_state"
    SIGNALS = "signals"
    CLAIMS = "claims"
    COORDINATION = "coordination"
    MEASUREMENT = "measurement"
    NOTIFICATIONS = "notifications"
    LEGAL_HOLDS = "legal_holds"
