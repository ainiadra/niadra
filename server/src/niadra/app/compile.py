"""Use cases of the context compiler (5.10): compile on memory change, seed the cache after a failover,
compile on demand.

The worker writes packs and manifests in the database, then publishes the new generation and
the packs to the cache (T0). A cache write can be lost without harm: the read path falls back
to the stored pack (T1) and asks for a reseed (06, 3).
"""

import json
import logging
from collections import OrderedDict
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from niadra.app.object_memory import account_instructions, gather
from niadra.app.pipeline import fence
from niadra.app.typed_state import plain_for_reader
from niadra.config.models import SourceConfig, SpaceConfig
from niadra.domain.common.errors import InvalidInput
from niadra.domain.compile import (
    COMPILER_VERSION,
    CompiledPack,
    CompileInput,
    CompileSettings,
    MemorySnapshot,
    StoredPack,
    Variant,
    compile_pack,
    pack_key,
    profile_target,
)
from niadra.domain.compile.model import AccountContact, slot_budget
from niadra.domain.compile.templates import opening_text
from niadra.domain.compile.views import view_spec
from niadra.domain.identity.provenance import reader_view
from niadra.domain.ingest.hot import pack_envelope, unpack_envelope
from niadra.domain.measure.trim import ContextTrim, active_units
from niadra.domain.measure.views import LEARNED_ARM, ViewBudgets
from niadra.domain.measure.views import arm as views_arm
from niadra.domain.memory.bound import OBJECTS_CEILING
from niadra.domain.memory.items import ended_newest_first, kept_sessions, recently_kept
from niadra.domain.memory.summaries import tail_summaries
from niadra.domain.objects.state import current_actions
from niadra.domain.policy import effective_verification
from niadra.domain.policy.audience import conversation_audience
from niadra.domain.policy.fields import reader_of
from niadra.domain.records import BusinessObject, Fact, OpenItem, ProfileAttr, ProfileRecord, SessionRecord
from niadra.domain.serve.cache_keys import CacheEpoch, gen_key, t0_key, wanted_key
from niadra.domain.tasks import Task, TaskName
from niadra.domain.traits.rules import released_to
from niadra.domain.vocabulary import Audience, Feature, OpenItemStatus, SubjectKind, Verification
from niadra.ports.infra import Clock, ConfigProvider, HotLayer, KeyValueCache, KeyVault
from niadra.ports.signals import NoObjectInstructions, ObjectInstructions
from niadra.ports.uow import UnitOfWork, UnitOfWorkFactory

log = logging.getLogger(__name__)

TAIL_LEVELS = ("short", "long")
DEFAULT_TIMEZONE = "America/Sao_Paulo"
MAX_ACCOUNT_CONTACTS = 20
"""Contacts an account shows in detail (docs/concepts/accounts); the rest are counted."""
MAX_CONTACT_MEMORY = 500
"""Contacts whose instructions and open items an account reads, seen last first: every one in practice; past
it the block says how many were not read. Only `MAX_ACCOUNT_CONTACTS` are shown in detail (their episodes)."""
CONTACT_EPISODES = 3
CONTACT_WINDOW = timedelta(days=90)
CONTACT_NAME_SLOTS = frozenset({"name", "preferred_name", "nickname"})
MAX_VARIANTS = 32
OBJECT_EAGER_WINDOW = timedelta(days=7)
OBJECT_EAGER_PER_VIEW = 3
MAX_WANTED = 32
ACCOUNT_BLOCKS = 64
"""Organizations whose block a read process keeps for the on-demand compilations of their contacts' packs,
the least recently read leaving first."""
ACCOUNT_BLOCK_FOR = timedelta(seconds=60)
"""How long a kept block serves within one generation of its organization: what moves its memory compiles
it again (a contact's turn, an object of it, an erasure, which also moves its privacy version), and this
bounds what a compilation that has not landed yet leaves out."""
T0_TTL_S = 72 * 3600
"""Compiled context stays in the cache 72 hours at most (docs/concepts/privacy, retention table); past it a
read takes the stored pack (T1) and writes it back."""
PACK_TTL_S = 24 * 3600
"""A pack itself stays a day, like a conversation's pin: packs are most of the cache's memory (94% on
27/09/2026, about 7.7 KB each against 112 bytes for a generation), and one not read for a day comes back
from T1 in one row read. The generation, watermarks and wanted variants keep `T0_TTL_S`."""
# After a compilation covers a handle's turns, the hot layer only needs to outlive the next reads (5.4).
HOT_KEEP = timedelta(hours=24)
_NO_AS_OF = datetime(1970, 1, 1, tzinfo=UTC)

type SnapshotSink = Callable[[UUID, ProfileRecord, int, MemorySnapshot], None]
"""Told of every memory an on-demand compilation opened, with the generation read before it: the read
process's retrieval cache builds its entry from it instead of opening the rows again."""


@dataclass(frozen=True, slots=True)
class _AccountBlock:
    """An organization's block as one on-demand compilation read it, for the next ones of its contacts."""

    generation: int
    privacy_version: int
    membership_version: int
    handle_ids: tuple[UUID, ...]
    config: SpaceConfig
    loaded_at: datetime
    block: MemorySnapshot


async def load_snapshot(
    uow: UnitOfWork,
    profile: ProfileRecord,
    now: datetime | None = None,
    *,
    timezone: str = DEFAULT_TIMEZONE,
    closed: Collection[OpenItemStatus] = (),
    read_at: datetime | None = None,
) -> MemorySnapshot:
    """P0: one read of the profile's current memory through the owning modules' ports. With `now`, facts
    whose declared validity ended are left out (10, D.1); history with `show_expired` passes None.

    `read_at`, when the rows are replayed from a recording (`app.history_rows`): the moment they were read.
    The windows the reads take (a contact's recent conversations) are asked for as they were then, and
    narrowed to now here, so a replay asks for the same calls whatever the time.

    `profile` may be a reader's view (`reader_view`, 5.3): its rows are read by the view's handles, and
    what the profile keeps as a whole (the traits, the tail summaries) is narrowed to them too: a trait
    with evidence from a hidden handle stays out. The stored summaries cover the confirmed handles only
    (`MemoryService._summaries`), so a view that holds or hides an unconfirmed link gets them rebuilt, in
    `timezone`, from the episodes the view holds.

    With `closed` (`resolved`), `kept_items` holds every item of those statuses, the contacts' too
    for an organization, instead of the promises kept lately: history asked for them (`item_statuses`)."""
    handles = profile.handle_ids
    hidden = profile.hidden_handle_ids
    episodes = tuple(await uow.memory.episodes_for(handles))
    summaries: list[tuple[str, str]] = []
    if hidden or profile.provisional_handle_ids:
        if episodes:
            rebuilt = tail_summaries(episodes, timezone)
            summaries = [(level, rebuilt[level]) for level in TAIL_LEVELS if rebuilt.get(level)]
    else:
        for level in TAIL_LEVELS:
            if text := await uow.memory.tail_summary(profile.profile_id, level):
                summaries.append((level, text))
    traits = await uow.traits.active_for(profile.profile_id)
    if hidden and traits:
        tainted = {t.trait_id for t in await uow.traits.traits_with_evidence_from(hidden)}
        traits = [t for t in traits if t.trait_id not in tainted]
    facts = tuple(f for f in await uow.memory.facts_for(handles) if now is None or f.valid_at_time(now))
    # Every object of the profile up to the runaway guard, one more to tell it was passed: the 50 newest it
    # once read left a client's older cases, and their instructions, out of the pack.
    objects = await uow.objects.objects_for(handles, limit=OBJECTS_CEILING + 1)
    snapshot = MemorySnapshot(
        profile=profile,
        facts=facts,
        band_contradicted=await band_contradicted(uow, handles, facts),
        episodes=episodes,
        open_items=tuple(
            await uow.memory.open_items_for(handles, (OpenItemStatus.OPEN, OpenItemStatus.OVERDUE))
        ),
        # A promise kept in the last days, beside the record that kept it; only for a pack of now.
        kept_items=(kept := await _kept(uow, handles, now, closed))[0],
        kept_sessions=kept[1],
        attrs=tuple(await uow.memory.attrs_for(handles)),
        traits=tuple(traits),
        objects=tuple(objects[:OBJECTS_CEILING]),
        objects_unread=max(0, len(objects) - OBJECTS_CEILING),
        actions=tuple(current_actions(await uow.objects.actions_for(handles))),
        summaries=tuple(summaries),
    )
    if profile.kind.is_organization:
        snapshot = await _with_contacts(uow, snapshot, now, closed=closed, read_at=read_at)
    return snapshot


async def _kept(
    uow: UnitOfWork, handles: Sequence[UUID], now: datetime | None, closed: Collection[OpenItemStatus]
) -> tuple[tuple[OpenItem, ...], tuple[tuple[str, datetime], ...]]:
    """The items past open a snapshot carries: every one of the `closed` statuses; else the promises kept
    lately, and only for a pack of now. Beside them, the sessions whose promises were resolved, from the
    same read."""
    if closed:
        statuses = tuple(sorted(closed, key=lambda s: s.value))
        found = await uow.memory.open_items_for(handles, statuses)
        return tuple(ended_newest_first(found)), kept_sessions(found)
    if now is None:
        return (), ()
    resolved = await uow.memory.open_items_for(handles, (OpenItemStatus.RESOLVED,))
    return tuple(recently_kept(resolved, now)), kept_sessions(resolved)


def for_reader(
    config: SpaceConfig, snapshot: MemorySnapshot, variant: Variant, now: datetime
) -> MemorySnapshot:
    """The objects a variant's reader gets (24.2.11): without the fields its access masks or denies, nor the
    values only a source its licence bars observed. A space without typed state keeps its objects as they
    are."""
    source = next((s for s in config.sources if s.source_id == variant.source_id), None)
    if source is None or Feature.STATE not in config.features.enabled or not snapshot.objects:
        return snapshot
    if not config.object_types.guards_reads():
        return snapshot
    reader = reader_of(source.name, variant.purposes)
    names = {s.source_id: s.name for s in config.sources}
    commercial = config.object_types.commercial_purposes
    objects = tuple(
        replace(obj, state=plain_for_reader(obj, declared, reader, commercial, names, now))
        if (declared := config.object_types.get(obj.key.type)) is not None
        else obj
        for obj in snapshot.objects
    )
    return replace(snapshot, objects=objects)


async def band_contradicted(
    uow: UnitOfWork, handles: Sequence[UUID], facts: Sequence[Fact]
) -> frozenset[UUID]:
    """The facts a live band edge holds in contradiction: both ends among `facts`, so a pair a person
    settled (one side retracted or corrected) no longer weighs on the other."""
    if len(facts) < 2:
        return frozenset()
    live = {f.fact_id for f in facts}
    return frozenset(
        side
        for r in await uow.memory.relations_for(handles, origin="band")
        if r.a.id in live and r.b.id in live
        for side in (r.a.id, r.b.id)
    )


async def account_contacts(uow: UnitOfWork, account: ProfileRecord) -> list[tuple[ProfileRecord, str]]:
    """The people with an active link to any handle of the account, most recently seen first, each once
    with the role of its link (21.2, 21.4)."""
    own = set(account.handle_ids)
    links = [
        link for link in await uow.identity.active_links(account.handle_ids) if link.org_handle_id in own
    ]
    if not links:
        return []
    person_handles = sorted({link.person_handle_id for link in links})
    members = await uow.identity.memberships(person_handles)
    seen = {h.handle_id: h.last_seen for h in await uow.identity.get_handles(person_handles)}
    latest: dict[UUID, tuple[datetime, str]] = {}
    for link in links:
        profile_id = members.get(link.person_handle_id)
        if profile_id is None or profile_id == account.profile_id:
            continue
        at = seen.get(link.person_handle_id, _NO_AS_OF)
        if profile_id not in latest or at > latest[profile_id][0]:
            latest[profile_id] = (at, link.role)
    # One statement for every contact: an account of 500 contacts read its people one by one.
    people = {p.profile_id: p for p in await uow.identity.get_profiles(sorted(latest, key=str))}
    found: list[tuple[datetime, ProfileRecord, str]] = []
    for profile_id, (at, role) in latest.items():
        person = people.get(profile_id)
        if person is not None and not person.kind.is_organization:
            found.append((at, person, role))
    found.sort(key=lambda row: (row[0], row[1].profile_id), reverse=True)
    return [(person, role) for _, person, role in found]


async def _with_contacts(
    uow: UnitOfWork,
    snapshot: MemorySnapshot,
    now: datetime | None,
    *,
    closed: Collection[OpenItemStatus] = (),
    read_at: datetime | None = None,
) -> MemorySnapshot:
    """An account's memory with what its contacts said lately: the recent episodes of the
    `MAX_ACCOUNT_CONTACTS` contacts seen last, each named by who spoke and in which role; the open and kept
    items and the instructions of up to `MAX_CONTACT_MEMORY` of them; and how many contacts the account has.
    Other contacts' items are masked unless the reader may see them."""
    contacts = await account_contacts(uow, snapshot.profile)
    if not contacts:
        return snapshot
    # Each contact as a reader by the account sees them: without the rows of their unconfirmed links (5.3).
    detailed = [(reader_view(person), role) for person, role in contacts[:MAX_ACCOUNT_CONTACTS]]
    handles = [h for person, _ in detailed for h in person.handle_ids]
    since = (now or datetime.now(UTC)) - CONTACT_WINDOW
    # Rows replayed from a recording ask for the window as it was read: newest first, the conversations
    # still in the window now are the first ones of that read, so keeping them answers as a read of now.
    asked = since if read_at is None else min(since, read_at - CONTACT_WINDOW)
    episodes = await uow.memory.episodes_for(
        handles, since=asked, limit=MAX_ACCOUNT_CONTACTS * CONTACT_EPISODES
    )
    per_contact: dict[UUID, int] = {}
    owner = {h: person.profile_id for person, _ in detailed for h in person.handle_ids}
    kept = []
    for episode in episodes:  # newest first: each contact's latest few
        if episode.ended_at < since:
            continue
        who = owner.get(episode.origin_handle_id)
        if who is not None and per_contact.get(who, 0) < CONTACT_EPISODES:
            per_contact[who] = per_contact.get(who, 0) + 1
            kept.append(episode)
    # What a contact promised or asked stays the account's however many others were seen since (an
    # attendant's promise left the account's timeline once 20 newer contacts were linked, 06/10/2026).
    remembered = [(reader_view(person), role) for person, role in contacts[:MAX_CONTACT_MEMORY]]
    every = [h for person, _ in remembered for h in person.handle_ids]
    items = await uow.memory.open_items_for(every, (OpenItemStatus.OPEN, OpenItemStatus.OVERDUE))
    done = (await _kept(uow, every, now, closed))[0] if closed else ()
    attrs = await uow.memory.attrs_for(handles)
    named = [f for f in await uow.memory.facts_for(handles) if f.predicate in CONTACT_NAME_SLOTS]
    return replace(
        snapshot,
        episodes=(*snapshot.episodes, *kept),
        open_items=(*snapshot.open_items, *items),
        kept_items=(*snapshot.kept_items, *done),
        contacts=tuple(
            AccountContact(person.handle_ids, _contact_name(attrs, named, person.handle_ids), role)
            for person, role in detailed
        ),
        contacts_total=len(contacts),
        instruction_speakers=tuple((h, role) for person, role in remembered for h in person.handle_ids),
        contacts_unread=max(0, len(contacts) - MAX_CONTACT_MEMORY),
    )


def _contact_name(
    attrs: Sequence[ProfileAttr], facts: Sequence[Fact], handle_ids: Sequence[UUID]
) -> str | None:
    """How the contact is called: the profile slot when there is one, else the fact that says it."""
    own = set(handle_ids)
    slots = [a for a in attrs if a.origin_handle_id in own and a.slot in CONTACT_NAME_SLOTS]
    if slots:
        return max(slots, key=lambda a: (a.observed_at, a.times_seen)).value
    said = [f for f in facts if f.origin_handle_id in own]
    return max(said, key=lambda f: f.last_confirmed_at).value if said else None


def reader_variants(
    config: SpaceConfig,
    trims: Sequence[ContextTrim],
    profile: ProfileRecord,
    readers: Sequence[str],
    learned: ViewBudgets | None = None,
) -> list[str]:
    """The eager set of each reader through a handle a claim just joined (5.3), compiled at the join so
    the reader's first read finds its packs; a handle confirmed or split away since reads as everyone."""
    handles = [UUID(h) for h in readers if UUID(h) in profile.provisional_handle_ids]
    plain = default_variants(config, trims, profile.kind, learned)
    return [replace(v, reader_handle_id=h).key for h in handles for v in plain]


def variant_readers(variant: Variant) -> tuple[UUID, ...]:
    """The handles a variant's reader reads through, for its view of the profile (5.3)."""
    return (variant.reader_handle_id,) if variant.reader_handle_id is not None else ()


def compile_settings(config: SpaceConfig) -> CompileSettings:
    trait_purposes: dict[str, frozenset[str]] = {}
    trait_audiences: dict[str, frozenset[Audience]] = {}
    for rule in config.trait_rules:
        purposes, audiences = released_to(rule.catalog, rule.purposes)
        for name in (rule.catalog, rule.rule_id):
            if purposes:
                trait_purposes[name] = purposes
            if audiences:
                trait_audiences[name] = audiences
    return CompileSettings(
        policy=config.policy,
        timezone=config.settings.timezone,
        locale=config.settings.locale,
        b0_text=opening_text(config.settings.b0_text, config.settings.locale),
        floors=config.settings.target_model_floors,
        source_names={s.source_id: s.name for s in config.sources},
        trait_purposes=trait_purposes,
        trait_audiences=trait_audiences,
        task_views=config.views,
        extractor_version=config.extraction.version,
        salt_seed=str(config.space_id),
        slot_rules=slot_rules(config),
        compatible_purposes={k: frozenset(v) for k, v in config.policy.compatible_purposes.items()},
        private_fields=config.object_types.private_fields(),
        field_order=config.object_types.line_order(),
        content_fields=config.object_types.content_fields(),
        field_labels=config.object_types.field_labels(),
        value_words=config.object_types.value_words(),
        date_fields=config.object_types.date_fields(),
        name_fields=config.object_types.name_fields(),
        parent_fields=config.object_types.parent_fields(),
        declared_names=config.object_types.declared_names(),
    )


def slot_rules(config: SpaceConfig) -> dict[str, tuple[str, dict[UUID, int]]]:
    """The survivorship of each slot the schema declares, with source priorities by source id: a priority
    names sources by name or id, and a name no source has any more is skipped."""
    by_name = {s.name: s.source_id for s in config.sources}
    rules: dict[str, tuple[str, dict[UUID, int]]] = {}
    for slot in config.extraction.slots:
        ranks: dict[UUID, int] = {}
        for entry in slot.source_priority:
            source_id = by_name.get(entry) or _as_uuid(entry)
            if source_id is not None and source_id not in ranks:
                ranks[source_id] = len(ranks)
        rules[slot.name] = (slot.survivorship, ranks)
    return rules


def _as_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


def trim_for(config: SpaceConfig, source_id: UUID, view: str, trims: Sequence[ContextTrim]) -> frozenset[str]:
    """The units a read by this source in this view leaves out (15.8); the read path and the compiler
    both ask here, so the key a source reads is the key the compiler keeps warm."""
    settings = config.settings.context_trim
    source = config.source(source_id)
    trim = next((t for t in trims if t.source_id == source_id and t.view == view), None)
    return active_units(
        trim,
        enabled=settings.enabled,
        opted_out=source_id in settings.opt_out_sources,
        critical=source is not None and source.critical,
    )


def reader_variant(
    config: SpaceConfig,
    *,
    source_id: UUID,
    audience: Audience,
    purposes: frozenset[str],
    view: str,
    level: Verification,
    trims: Sequence[ContextTrim] = (),
    learned: ViewBudgets | None = None,
) -> Variant:
    """The pack a plain read by this source gets. The read path and the warm-up of a new conversation
    both build it here, so the key the compiler keeps warm is the key the first read asks for. `learned`
    are the space's learned views when the customer is in their arm (13, 6.3)."""
    slotted = slot_budget(view) > 0
    return Variant(
        # A reserve for the per-turn slots, and a skeleton body in chat and voice (11, 3.3); brief and full
        # have no slots and keep their body.
        slotted=slotted,
        view=view,
        verification=level,
        audience=audience,
        purposes=purposes,
        # Only sources the policy singles out get their own pack; the rest share the audience class.
        source_id=source_id if str(source_id) in config.policy.competes_with else None,
        # What this source never used across enough deliveries of this view stays out (15.8).
        trim=trim_for(config, source_id, view, trims),
        # The skeleton this source was learned to use, for a read without a target model (13, 6.3).
        budgets=learned.ceilings_for(source_id, "neutral", view) if learned is not None and slotted else (),
    )


def learned_for(config: SpaceConfig, learned: ViewBudgets | None, profile_id: UUID) -> ViewBudgets | None:
    """The learned views a read of this customer compiles with: none for a customer of the default arm,
    who keeps the view's own budgets to measure the learned ones against (13, 6.3)."""
    if learned is None:
        return None
    return learned if views_arm(config.space_id, profile_id) == LEARNED_ARM else None


def usual_view(channel: str | None, kind: SubjectKind) -> str:
    """A source's everyday view; an account or partner read gets its subject view instead of chat (21.4)."""
    view = "voice" if channel == "voice" else "chat"
    return kind.value if kind.is_organization and view == "chat" else view


def session_variants(
    config: SpaceConfig,
    session: SessionRecord,
    profile: ProfileRecord,
    proven: Verification | None,
    trims: Sequence[ContextTrim] = (),
    learned: ViewBudgets | None = None,
) -> list[Variant]:
    """What the agent of a conversation that just opened will read first (5.11): its source's usual
    view at V0, the SDKs' default, and at the level the conversation has proven so far, within the
    source's ceiling; an internal agent reads at `no_customer`."""
    source = config.source(session.source_id)
    if source is None:
        return []
    # Who the conversation is with decides what the key will read in it.
    conversation = session.conversation_id is not None and session.task_id is None
    audience = (
        conversation_audience(source.audience, profile.kind, conversation=conversation) or source.audience
    )
    if audience is Audience.INTERNAL_AGENT:
        levels = {Verification.NO_CUSTOMER}
    else:
        reached = proven if proven is not None and proven is not Verification.NO_CUSTOMER else Verification.V0
        top = effective_verification(
            reached, ceiling=source.verification_ceiling, proven=reached, audience=audience
        ).effective
        levels = {Verification.V0, top}
    view = usual_view(source.channel, profile.kind)
    return [
        reader_variant(
            config,
            source_id=source.source_id,
            audience=audience,
            purposes=source.purposes,
            view=view,
            level=level,
            trims=trims,
            learned=learned,
        )
        for level in sorted(levels, key=lambda lv: lv.rank)
    ]


async def _about_variants(
    uow: UnitOfWork,
    config: SpaceConfig,
    trims: Sequence[ContextTrim],
    profile: ProfileRecord,
    link_ids: Sequence[str],
    learned: ViewBudgets | None,
) -> list[str]:
    """The `about` packs a link just made will be read with (P6): each source's usual view at the levels
    the eager set keeps, through the organization's handle the link names, so a linked contact's first read
    finds its pack compiled. Task views are asked for by internal agents, which read on demand."""
    found: list[str] = []
    own = set(profile.handle_ids)
    for raw in link_ids:
        link = await uow.identity.get_link(UUID(raw))
        if link is None or link.valid_to is not None or link.person_handle_id not in own:
            continue
        for variant in default_variants(config, trims, profile.kind, learned):
            if variant.view.startswith("task:"):
                continue
            about = replace(
                variant,
                about_handle_id=link.org_handle_id,
                link_role=link.role,
                can_see_contacts=link.can_see_contacts,
            )
            found.append(about.key)
    return found


def default_variants(
    config: SpaceConfig,
    trims: Sequence[ContextTrim] = (),
    kind: SubjectKind = SubjectKind.PERSON,
    learned: ViewBudgets | None = None,
) -> list[Variant]:
    """The eager set (5.10): each source's usual view, at V0 (what a conversation that proved nothing
    reads, and the SDKs' default) and V1 for customer-facing agents, at `no_customer` for internal
    ones, plus every task view the space declares for the internal agents it serves; each with the
    source's trim when it has one."""
    found: dict[str, Variant] = {}

    def add(source: SourceConfig, view: str, level: Verification) -> None:
        variant = reader_variant(
            config,
            source_id=source.source_id,
            audience=source.audience,
            purposes=source.purposes,
            view=view,
            level=level,
            trims=trims,
            learned=learned,
        )
        found.setdefault(variant.key, variant)

    for source in config.sources:
        view = usual_view(source.channel, kind)
        if source.audience is Audience.INTERNAL_AGENT:
            add(source, view, Verification.NO_CUSTOMER)
            for task_view in config.views:
                if not task_view.name.startswith("task:"):
                    continue  # the `custom` view is asked for by name, not kept warm for every agent
                if not task_view.purposes or task_view.purposes & source.purposes:
                    add(source, task_view.name, Verification.NO_CUSTOMER)
        else:
            add(source, view, Verification.V0)
            add(source, view, Verification.V1)
    return [found[k] for k in sorted(found)]


def object_variants(
    config: SpaceConfig, objects: Sequence[BusinessObject], now: datetime, trims: Sequence[ContextTrim] = ()
) -> list[Variant]:
    """Packs centered on the profile's recent objects, for the internal agents whose task views cover their
    type: the object an agent works on is the one that just changed, so its pack is ready before
    the agent asks. The newest few per view, of the last week."""
    recent = sorted(
        (o for o in objects if now - o.as_of <= OBJECT_EAGER_WINDOW), key=lambda o: o.as_of, reverse=True
    )
    found: dict[str, Variant] = {}
    for source in config.sources:
        if source.audience is not Audience.INTERNAL_AGENT:
            continue
        for task_view in config.views:
            if not task_view.name.startswith("task:") or not task_view.object_types:
                continue
            if task_view.purposes and not task_view.purposes & source.purposes:
                continue
            plain = reader_variant(
                config,
                source_id=source.source_id,
                audience=source.audience,
                purposes=source.purposes,
                view=task_view.name,
                level=Verification.NO_CUSTOMER,
                trims=trims,
            )
            covered = [o for o in recent if o.key.type in task_view.object_types][:OBJECT_EAGER_PER_VIEW]
            for obj in covered:
                variant = replace(plain, focus_object_id=obj.object_id)
                found.setdefault(variant.key, variant)
    return [found[k] for k in sorted(found)]


def current_trims(config: SpaceConfig, trims: Sequence[ContextTrim]) -> set[tuple[str, frozenset[str]]]:
    """(view, units) of every trim a source reads with today."""
    return {(t.view, units) for t in trims if (units := trim_for(config, t.source_id, t.view, trims))}


async def seal_pack(keys: KeyVault, space_id: UUID, cache_key: str, pack: CompiledPack) -> bytes:
    """Packs in the cache are encrypted like everything else; the AAD is the cache key."""
    ciphertext, version = await keys.encrypt(space_id, pack.to_bytes(), cache_key.encode())
    return pack_envelope(version, ciphertext)


async def open_pack(keys: KeyVault, space_id: UUID, cache_key: str, value: bytes) -> CompiledPack:
    version, ciphertext = unpack_envelope(value)
    return CompiledPack.from_bytes(await keys.decrypt(space_id, ciphertext, cache_key.encode(), version))


class CompileService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        keys: KeyVault,
        cache: KeyValueCache,
        clock: Clock,
        config: ConfigProvider,
        *,
        hot: HotLayer | None = None,
        epoch: CacheEpoch | None = None,
        on_snapshot: SnapshotSink | None = None,
    ) -> None:
        self._uow = uow
        self._keys = keys
        self._cache = cache
        self._clock = clock
        self._config = config
        self._hot = hot
        self._epoch = epoch or CacheEpoch()
        self._on_snapshot = on_snapshot
        self.instructions: ObjectInstructions = NoObjectInstructions()
        """The signals module, once wired: the instructions people said about the objects compiled."""
        self._account_blocks: OrderedDict[tuple[UUID, UUID], _AccountBlock] = OrderedDict()

    async def compile(self, task: Task) -> None:
        """The `compile` task: coalesced per profile, so one run covers every request made before it (5.5)."""
        assert task.profile_id is not None, "compile is a per-profile task"
        space_id, profile_id = task.space_id, task.profile_id
        config = self._config.space(space_id)
        if session_id := task.payload.get("session_id"):
            # A warm-up never compiles inline: it publishes what is stored and asks the coalesced
            # compilation for the rest, so an opening conversation adds one short read to the workers.
            await self._warm(task, config, UUID(session_id))
            return
        settings = compile_settings(config)
        wanted = await self._wanted(space_id, profile_id)
        packs: list[CompiledPack] = []
        # Reads in one transaction, compiles with no connection held, writes in another: compiling ten
        # variants is tens of milliseconds of CPU, and a server connection held that long is one of the
        # two a space database gets (06, 6.3). The profile's lease keeps another compilation or an erasure
        # out in between, and the write checks it is still held.
        async with self._uow(space_id) as uow:
            seen = await uow.pipeline.begin_work(space_id, profile_id, TaskName.COMPILE)
            profile = await uow.identity.get_profile(profile_id)
            generation = 0
            inputs: list[tuple[CompileInput, int]] = []
            if profile is not None:
                target = profile_target(profile_id)
                timezone = config.settings.timezone
                # What a confirmed reader sees; a reader through an unconfirmed link gets a view of its own.
                confirmed = reader_view(profile)
                snapshot = await load_snapshot(uow, confirmed, self._clock.now(), timezone=timezone)
                snapshot = await self._with_object_memory(uow, config, snapshot)
                snapshots = {confirmed.handle_ids: snapshot}
                stored_packs = await uow.compile.packs_for(target)
                stored = [s.pack_key.split("/", 1)[1] for s in stored_packs]
                last_read = {s.pack_key.split("/", 1)[1]: s.last_read_at for s in stored_packs}
                trims = await uow.measure.trims()
                learned = await _learned(uow, config, profile_id)
                eager_objects = [
                    v.key for v in object_variants(config, snapshot.objects, self._clock.now(), trims)
                ]
                readers = reader_variants(config, trims, profile, task.payload.get("readers", []), learned)
                linked = await _about_variants(
                    uow, config, trims, profile, task.payload.get("links", []), learned
                )
                variants = self._variants(
                    config,
                    stored,
                    [*eager_objects, *readers, *linked],
                    wanted,
                    trims,
                    profile.kind,
                    last_read=last_read,
                    provisional=frozenset(profile.provisional_handle_ids),
                    learned=learned,
                )
                generation = await uow.compile.current_generation(target) + 1
                accounts: dict[UUID, tuple[MemorySnapshot, int] | None] = {}
                for variant in variants:
                    view = reader_view(profile, variant_readers(variant))
                    if view.handle_ids not in snapshots:
                        loaded = await load_snapshot(uow, view, self._clock.now(), timezone=timezone)
                        snapshots[view.handle_ids] = await self._with_object_memory(uow, config, loaded)
                    snapshot = for_reader(config, snapshots[view.handle_ids], variant, self._clock.now())
                    if variant.about_handle_id is None:
                        inputs.append((CompileInput(snapshot, variant, settings), 0))
                        continue
                    # An `about` pack, compiled ahead of the read (P6): only while the link it reads
                    # through holds, as the read path checks it, with the organization's block and its
                    # generation, so the read knows when the organization changed since.
                    account = await self._account(uow, config, view, variant, accounts)
                    if account is not None:
                        block, account_generation = account
                        one = CompileInput(snapshot, variant, settings, block, tuple(view.handle_ids))
                        inputs.append((one, account_generation))
        if profile is not None:
            packs = [
                replace(
                    compile_pack(one),
                    generation=generation,
                    account_generation=account_generation,
                    account_privacy_version=one.account.profile.privacy_version if one.account else 0,
                )
                for one, account_generation in inputs
            ]
            async with self._uow(space_id) as uow:
                await fence(uow)
                # One write for every variant; one past the cap leaves storage, so it is never served
                # stale.
                await uow.compile.save_manifests({p.manifest_hash: p.manifest for p in packs})
                await uow.compile.save_bundle(target, generation, [_stored(profile_id, p) for p in packs])
        if profile is not None and packs:
            await self._publish(space_id, profile_id, generation, packs)
        # `done` moves once the generation is published, not when the packs commit: ingest status reads it
        # as "a read made now sees this memory" (7.1). A crash in between leaves the request pending, as a
        # crash inside the transaction did, and the redelivered task compiles again.
        async with self._uow(space_id) as uow:
            if await uow.pipeline.finish_work(space_id, profile_id, TaskName.COMPILE, seen):
                await uow.pipeline.enqueue([Task(TaskName.COMPILE, space_id, profile_id=profile_id)])
            if profile is not None and profile.kind.is_organization and packs:
                # Its people's `about` packs carry its block: the ones seen last compile them again, so
                # their next read finds the block current (P6); any other reads it on demand, as the
                # organization's newer generation tells it to.
                for person, _ in (await account_contacts(uow, profile))[:MAX_ACCOUNT_CONTACTS]:
                    await uow.pipeline.request_work(
                        Task(TaskName.COMPILE, space_id, {"reason": "account"}, profile_id=person.profile_id)
                    )
        if profile is not None and packs:
            await self._shorten_hot(space_id, profile, packs)

    async def _account(
        self,
        uow: UnitOfWork,
        config: SpaceConfig,
        person: ProfileRecord,
        variant: Variant,
        accounts: dict[UUID, "tuple[MemorySnapshot, int] | None"],
    ) -> "tuple[MemorySnapshot, int] | None":
        """The organization block of an `about` variant, read once per organization in a compilation: None
        when the person no longer holds an active link to it with the variant's role and visibility."""
        assert variant.about_handle_id is not None
        org = await uow.identity.profile_of(variant.about_handle_id)
        if org is None:
            return None
        people = set(person.handle_ids)
        orgs = set(org.handle_ids)
        holds = any(
            link.person_handle_id in people
            and link.org_handle_id in orgs
            and link.role == variant.link_role
            and link.can_see_contacts == variant.can_see_contacts
            for link in await uow.identity.active_links(sorted(people))
        )
        if not holds:
            return None
        if org.profile_id not in accounts:
            account_generation = await uow.compile.current_generation(profile_target(org.profile_id))
            timezone = config.settings.timezone
            block = await load_snapshot(uow, reader_view(org), self._clock.now(), timezone=timezone)
            block = await self._with_object_memory(uow, config, block)
            accounts[org.profile_id] = (block, account_generation)
        return accounts[org.profile_id]

    async def _warm(self, task: Task, config: SpaceConfig, session_id: UUID) -> None:
        """A conversation just opened (5.11): its reader's packs go to T0 before the agent's first read.
        Packs stored for the current generation are only published; a missing or stale one joins the
        profile's wanted variants and the coalesced compilation, which makes it eager."""
        assert task.profile_id is not None
        async with self._uow(task.space_id) as uow:
            session = await uow.sessions.get_session(session_id)
            profile = await uow.identity.get_profile(task.profile_id)
            if session is None or profile is None:
                return
            proven = await uow.sessions.proven_level(
                conversation_id=session.conversation_id, task_id=session.task_id, at=self._clock.now()
            )
            learned = await _learned(uow, config, profile.profile_id)
            readers = session_variants(config, session, profile, proven, await uow.measure.trims(), learned)
            target = profile_target(profile.profile_id)
            generation = await uow.compile.current_generation(target)
            stored = {s.pack_key: s for s in await uow.compile.packs_for(target)}
        ready: list[CompiledPack] = []
        missing: list[str] = []
        for variant in readers:
            found = stored.get(pack_key(profile.profile_id, variant.key))
            pack = _current(found, generation, profile, config)
            if pack is None:
                missing.append(variant.key)
            else:
                ready.append(pack)
        if ready:
            await self._publish(task.space_id, profile.profile_id, generation, ready)
        if missing:
            await self._add_wanted(task.space_id, profile.profile_id, missing)
            async with self._uow(task.space_id) as uow:
                await uow.pipeline.request_work(
                    Task(TaskName.COMPILE, task.space_id, {"reason": "warm"}, profile_id=profile.profile_id)
                )

    async def seed_generation(self, task: Task) -> None:
        """The `seed_gen` task: after a cache failover, rewrite the generation and T0 from `packs` (06, 3)."""
        assert task.profile_id is not None, "seed_gen is a per-profile task"
        async with self._uow(task.space_id) as uow:
            stored = await uow.compile.packs_for(profile_target(task.profile_id))
        if not stored:
            return
        generation = max(s.generation for s in stored)
        packs = [
            replace(CompiledPack.from_bytes(s.body), generation=s.generation)
            for s in stored
            if s.generation == generation
        ]
        await self._publish(task.space_id, task.profile_id, generation, packs)

    async def compile_on_demand(
        self,
        uow: UnitOfWork,
        profile: ProfileRecord,
        variant: Variant,
        *,
        account: ProfileRecord | None = None,
        person_handle_ids: tuple[UUID, ...] = (),
        generation: int | None = None,
        account_generation: int | None = None,
    ) -> CompiledPack:
        """T2 (5.11): the same passes on the read path, inside the caller's transaction. The pack is
        stored nowhere; its manifest is, content-addressed, so the receipt's manifest hash resolves for
        lineage and context-use measurement like any stored pack's (5.13, 19.2). `generation` and
        `account_generation`, when given, were read before the memory, in this transaction or an earlier one.

        The organization's block of an `about` pack is the same for all its contacts: it is read once per
        generation of the organization (`_account_block`), not once per contact. Each first read of a
        contact of an account of 500 read the account's memory again, 22 statements (09/10/2026)."""
        assert uow.space_id is not None, "on-demand compilation runs inside a space"
        config = self._config.space(uow.space_id)
        now = self._clock.now()
        timezone = config.settings.timezone
        # The reader's view (5.3); a read path that already narrowed the profile passes the view itself.
        profile = reader_view(profile, variant_readers(variant))
        # The generation before the memory: a pack may say it is older than what it holds, never newer,
        # or a compilation that lands in between would leave it looking current. A read that looked them up
        # already, before this, passes them.
        if generation is None:
            generation = await uow.compile.current_generation(profile_target(profile.profile_id))
        if account is None:
            account_generation = 0
        elif account_generation is None:
            account_generation = await uow.compile.current_generation(profile_target(account.profile_id))
        snapshot = await self._with_object_memory(
            uow, config, await load_snapshot(uow, profile, now, timezone=timezone)
        )
        account_snapshot = (
            await self._account_block(uow, config, account, account_generation)
            if account is not None
            else None
        )
        snapshot = for_reader(config, snapshot, variant, now)
        pack = compile_pack(
            CompileInput(snapshot, variant, compile_settings(config), account_snapshot, person_handle_ids)
        )
        await uow.compile.save_manifest(pack.manifest_hash, pack.manifest)
        if self._on_snapshot is not None:
            self._on_snapshot(uow.space_id, profile, generation, snapshot)
        return replace(
            pack,
            generation=generation,
            account_generation=account_generation,
            account_privacy_version=account.privacy_version if account is not None else 0,
        )

    async def _account_block(
        self, uow: UnitOfWork, config: SpaceConfig, account: ProfileRecord, generation: int
    ) -> MemorySnapshot:
        """The organization's block for an on-demand compilation: the one kept for its generation, privacy
        version, members and configuration, else read and kept. The stored `about` packs carry the block of
        the generation they were compiled at, and a read serves them until the next lands; a pack compiled
        on demand from a kept block is as current."""
        assert uow.space_id is not None
        now = self._clock.now()
        key = (uow.space_id, account.profile_id)
        kept = self._account_blocks.get(key)
        if (
            kept is not None
            and kept.generation == generation
            and kept.privacy_version == account.privacy_version
            and kept.membership_version == account.membership_version
            and kept.handle_ids == account.handle_ids
            and kept.config is config
            and now - kept.loaded_at < ACCOUNT_BLOCK_FOR
        ):
            self._account_blocks.move_to_end(key)
            return kept.block
        loaded = await load_snapshot(uow, reader_view(account), now, timezone=config.settings.timezone)
        block = await self._with_object_memory(uow, config, loaded)
        self._account_blocks[key] = _AccountBlock(
            generation,
            account.privacy_version,
            account.membership_version,
            account.handle_ids,
            config,
            now,
            block,
        )
        self._account_blocks.move_to_end(key)
        while len(self._account_blocks) > ACCOUNT_BLOCKS:
            self._account_blocks.popitem(last=False)
        return block

    async def _with_object_memory(
        self, uow: UnitOfWork, config: SpaceConfig, snapshot: MemorySnapshot
    ) -> MemorySnapshot:
        """The snapshot with what the owner and participants of its objects said about them, and, for an
        organization, what its contacts said must or must not be done with it: four
        statements, one more for an organization's instructions, none for a profile with neither."""
        if snapshot.objects:
            found = await gather(uow, config, snapshot.objects, self.instructions, now=self._clock.now())
            snapshot = replace(snapshot, object_memory=found)
        if snapshot.profile.kind.is_organization:
            told = await account_instructions(uow, config, snapshot, self.instructions)
            snapshot = replace(snapshot, instructions=told)
        return snapshot

    def _variants(
        self,
        config: SpaceConfig,
        stored: list[str],
        requested: list[str],
        wanted: list[str],
        trims: Sequence[ContextTrim] = (),
        kind: SubjectKind = SubjectKind.PERSON,
        *,
        last_read: dict[str, datetime | None] | None = None,
        provisional: frozenset[UUID] = frozenset(),
        learned: ViewBudgets | None = None,
    ) -> list[Variant]:
        """The defaults always, then the other variants by their last read, newest first, up to the cap:
        the least read leaves first, and a default one is never cut. A reader's own variant is
        kept only while its handle is still an unconfirmed link of the profile (5.3)."""
        defaults = {v.key for v in default_variants(config, trims, kind, learned)}
        found = {v.key: v for v in default_variants(config, trims, kind, learned)}
        current = current_trims(config, trims)
        ceilings = learned.all_ceilings() if learned is not None else {()}
        for key in [*stored, *requested, *wanted]:
            try:
                variant = Variant.parse(key)
            except (ValueError, KeyError):
                log.warning("skipping an unreadable variant key")
                continue
            if variant.trim and (variant.view, variant.trim) not in current:
                continue  # a trim no source reads with any more: its pack is not kept warm
            if variant.budgets and variant.budgets not in ceilings:
                continue  # ceilings no reader learned any more (13, 6.3): not kept warm
            if variant.reader_handle_id is not None and variant.reader_handle_id not in provisional:
                continue  # confirmed or split away since: that reader reads as everyone else now
            found.setdefault(variant.key, variant)
        reads = last_read or {}
        requested_now = set(requested)
        never = datetime.min.replace(tzinfo=UTC)

        def rank(key: str) -> tuple[int, int, float, str]:
            read = reads.get(key)
            # Defaults first; then what was just asked for; then the most recently read; ties by key.
            return (
                0 if key in defaults else 1,
                0 if key in requested_now else 1,
                -(read or never).timestamp(),
                key,
            )

        valid = []
        for key in sorted(found, key=rank):
            try:
                view_spec(found[key].view, config.views)
            except InvalidInput:
                continue  # a task view the tenant removed since the pack was stored
            valid.append(found[key])
        return sorted(valid[:MAX_VARIANTS], key=lambda v: v.key)

    async def _publish(
        self, space_id: UUID, profile_id: UUID, generation: int, packs: list[CompiledPack]
    ) -> None:
        epoch = self._epoch.value
        generation_key = gen_key(epoch, space_id, profile_id)
        try:
            await self._cache.bump_generation(generation_key, generation, T0_TTL_S)
            for pack in packs:
                key = t0_key(epoch, space_id, profile_id, pack.variant)
                value = await seal_pack(self._keys, space_id, key, pack)
                await self._cache.put_if_generation(key, generation_key, value, generation, PACK_TTL_S)
        except (ConnectionError, TimeoutError, OSError):
            # Availability fails open (L10): the stored pack answers as T1 until the next compilation.
            log.warning("could not publish packs to the cache")

    async def _shorten_hot(self, space_id: UUID, profile: ProfileRecord, packs: list[CompiledPack]) -> None:
        covered = max((p.as_of for p in packs if p.as_of is not None), default=None)
        if self._hot is None or covered is None:
            return
        covered_ms = int(covered.timestamp() * 1000)
        for handle_id in profile.handle_ids:
            try:
                await self._hot.shorten(space_id, handle_id, covered_ms, int(HOT_KEEP.total_seconds() * 1000))
            except (ConnectionError, TimeoutError, OSError):
                log.warning("could not shorten the hot layer")
                return

    async def _add_wanted(self, space_id: UUID, profile_id: UUID, keys: Sequence[str]) -> None:
        """The same list the read path fills after an on-demand compilation (5.10: lazy, then eager)."""
        wanted = await self._wanted(space_id, profile_id)
        merged = [*wanted, *(k for k in keys if k not in wanted)][-MAX_WANTED:]
        if merged == wanted:
            return
        try:
            await self._cache.set(
                wanted_key(self._epoch.value, space_id, profile_id), json.dumps(merged).encode(), T0_TTL_S
            )
        except (ConnectionError, TimeoutError, OSError):
            log.warning("could not record the wanted variants")

    async def _wanted(self, space_id: UUID, profile_id: UUID) -> list[str]:
        try:
            raw = await self._cache.get(wanted_key(self._epoch.value, space_id, profile_id))
        except (ConnectionError, TimeoutError, OSError):
            return []
        return list(json.loads(raw)) if raw else []


async def _learned(uow: UnitOfWork, config: SpaceConfig, profile_id: UUID) -> ViewBudgets | None:
    """The learned views this customer's packs compile with."""
    return learned_for(config, await uow.measure.view_budgets(), profile_id)


def _stored(profile_id: UUID, pack: CompiledPack) -> StoredPack:
    return StoredPack(
        pack_key=pack_key(profile_id, pack.variant),
        target=profile_target(profile_id),
        generation=pack.generation,
        compiler_version=COMPILER_VERSION,
        privacy_version=pack.privacy_version,
        body=pack.to_bytes(),
        pack_hash=pack.pack_hash,
        manifest_hash=pack.manifest_hash,
        as_of=pack.as_of or _NO_AS_OF,
    )


def _current(
    stored: StoredPack | None, generation: int, profile: ProfileRecord, config: SpaceConfig
) -> CompiledPack | None:
    """A stored pack the read path would accept as it is: same generation, compiler, policy and a
    privacy version at least the profile's."""
    if (
        stored is None
        or stored.generation != generation
        or stored.compiler_version != COMPILER_VERSION
        or stored.privacy_version < profile.privacy_version
    ):
        return None
    pack = replace(CompiledPack.from_bytes(stored.body), generation=stored.generation)
    return pack if pack.policy_version == config.policy.version else None
