"""From the snapshot to candidates: one per memory item, with policy attributes and its template text.

Memory stays tied to its origin (L6): every candidate carries the handles it came from, which
the policy uses for `competes_with` and the account view uses to tell contacts apart (21.4).
"""

import math
import re
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from niadra.domain.compile.bound_text import WORDS as BOUND_WORDS
from niadra.domain.compile.model import (
    Candidate,
    CompileInput,
    ItemKind,
    MemorySnapshot,
    Section,
)
from niadra.domain.compile.templates import Formatter
from niadra.domain.compile.views import SKELETON_VIEWS
from niadra.domain.coordinate.window import CONTACT_TIME_FACT
from niadra.domain.memory.bound import without_due
from niadra.domain.memory.items import evidence_event_ids, noted_at
from niadra.domain.memory.profile import surviving
from niadra.domain.memory.spoken import fact_spoken_by
from niadra.domain.objects.state import BOOKKEEPING, EVENT_TYPE, LIFECYCLE, last_recorded
from niadra.domain.objects.undeclared import humanized, is_dated, shown
from niadra.domain.objects.written import goes_by, opaque
from niadra.domain.policy import ItemAttrs
from niadra.domain.policy.sensitive import sensitive_topics
from niadra.domain.privacy.purposes import Tags, readers
from niadra.domain.records import AgentAction, BusinessObject, Episode, Fact, OpenItem, ProfileAttr, Trait
from niadra.domain.serve.lexicon import stem
from niadra.domain.serve.turn import analyze
from niadra.domain.vocabulary import (
    ActionStatus,
    Audience,
    FactStatus,
    OpenItemStatus,
    Sensitivity,
    SpokenBy,
    SubjectKind,
)

_NAME_SLOTS = frozenset({"name", "preferred_name", "nickname"})
_OPEN_OUTCOMES = frozenset({"unresolved", "escalated", "pending", "abandoned"})
_UNSAID_OUTCOME = "unknown"
"""An outcome nobody judged (a trivial session, a failed extraction): the line says nothing of it, never the
raw "unknown" a Portuguese block read (juridico-zero, 09/10/2026, final round)."""
# Tail summaries carry no time of their own; they never move `as_of`.
_NO_TIME = datetime.min.replace(tzinfo=UTC)
ACTION_CHARS = 2_000
"""What a pack line shows of an action's result. The result is kept whole (up to an event's text); a longer
one ends its line with "(continua no histórico)" and is read whole in the history and the action's own
record."""


def _kept_by_session(snapshot: MemorySnapshot) -> dict[str, tuple[datetime, bool]]:
    """Session id -> (when its last promise was kept, whether one it left is still open)."""
    still = {str(i.origin.get("session_id")) for i in snapshot.open_items}
    return {session: (at, session in still) for session, at in snapshot.kept_sessions}


def _speakers(snapshot: MemorySnapshot) -> dict[UUID, str]:
    """Who spoke, by name and role, for the contacts an account shows in detail."""
    return {
        handle: " · ".join(p for p in (contact.name, contact.role) if p)
        for contact in snapshot.contacts
        for handle in contact.handle_ids
    }


def _handles(*ids: UUID | None, extra: Iterable[UUID] = ()) -> tuple[UUID, ...]:
    return tuple(sorted({i for i in (*ids, *extra) if i is not None}, key=str))


CONFIRMATION_SCALE = 3.0
CONFIRMATION_CEILING = 0.5
BAND_PENALTY = 0.8


def _confirmation(sessions: int) -> float:
    """How much distinct sessions confirm a fact: `1 + 0.5 * (1 - e^(-n/3))`, saturating at 1.5, so the
    first conversations count most and a history imported with a value repeated in every turn does not pin
    it (the saturating curve is the ai-memory analysis's)."""
    n = max(sessions, 0)
    return 1.0 + CONFIRMATION_CEILING * (1.0 - math.exp(-n / CONFIRMATION_SCALE))


def fact_weight(fact: Fact, *, contradicted: bool = False) -> float:
    """A fact's weight in the pack: its confidence, confirmed by distinct sessions, and a fifth less while the
    similarity band holds it in contradiction with another active fact (once, however many pairs)."""
    weight = fact.confidence * _confirmation(fact.sessions_seen)
    return weight * BAND_PENALTY if contradicted else weight


def _repetition(times_seen: int) -> float:
    """A profile attribute's weight: one step per observation, up to ten."""
    return 1.0 + 0.1 * min(times_seen, 10)


REPEATED_SHARE = 0.8
"""A text repeats another when this share of its content words (by stem) is there, and every number."""
NUMBERED_SHARE = 0.6
"""The share when the text names numbers, all of them said already: "O agente informou cobertura por sete
dias, conforme a cláusula 91511" repeats "o agente respondeu que a cobertura é por sete dias, cláusula
91511" in other reporting words."""
_NUMBER = re.compile(r"\d+")


def repeats(said: str, again: str) -> bool:
    """`again` says nothing `said` does not: every number of it is in `said`, and `REPEATED_SHARE` of its
    content words (stems, without stop words or time words), `NUMBERED_SHARE` when it names numbers.
    "Protocolo 28206 registrado" repeats "O agente registrou a ocorrência e fornece o protocolo 28206";
    "Crédito de R$ 40 lançado" does not repeat "O agente lançou um crédito na fatura": the amount is new."""
    numbers = set(_NUMBER.findall(again))
    if not numbers <= set(_NUMBER.findall(said)):
        return False
    words = [stem(w) for w in analyze(again).topic if not w.isdigit()]
    if not words:
        return True
    known = {stem(w) for w in analyze(said).topic}
    share = NUMBERED_SHARE if numbers else REPEATED_SHARE
    return sum(w in known for w in words) >= share * len(words)


def trait_text(trait: Trait, fmt: Formatter) -> str:
    label = fmt.value(trait.name)
    return label if trait.value in ("", "true") else f"{label}: {fmt.value(trait.value)}"


def _item_weight(status: OpenItemStatus) -> float:
    """An overdue promise first, then an open one; one already kept says less, and never takes an open
    one's room (juridico-sandbox, 03/10/2026: kept promises filled the pack while open ones due in 2 to 5 days
    were left out)."""
    if status is OpenItemStatus.OVERDUE:
        return 1.5
    if status is OpenItemStatus.RESOLVED:
        return 0.4
    return 1.0


def object_text(
    obj: BusinessObject,
    fmt: Formatter,
    private: Collection[str] = (),
    order: Sequence[str] = (),
    *,
    labels: Mapping[str, str] | None = None,
    dates: Collection[str] = (),
    names: tuple[Sequence[str], Collection[str], Collection[str]] = ((), (), ()),
    values: Mapping[str, str] | None = None,
    parent: str | None = None,
    declared: Collection[str] = frozenset(),
) -> str:
    """An object as a line: its type, what it goes by and every plain field it holds. The fields its type
    holds `private` (personal data, a sensitive category, access rules) never reach the text. With the type's
    `order` (`CompileSettings.field_order`), the fields it tracks come first, then the rest as it declares
    them, then any other alphabetically. A field goes by its label (`labels`), a date field is written as the
    space writes dates (`dates`), and the object goes by its first written key (`names`, "Número CNJ
    1022345-...") instead of its opaque id when it holds one; a field that only repeats the object's id, or is
    an opaque id itself, says nothing ("legal case 49facdcd-... · last_movement_on: 2026-10-05" in
    juridico-sandbox, 05/10/2026). No count caps the fields: the view's token budget (`domain.compile.views`,
    chosen by `knapsack.select`) decides what of the pack a model reads, a line at a time.

    An object with no written key goes by its type and the name of the object it belongs to (`parent`,
    "hearing de Número CNJ ..."), never its opaque id; with neither, the id is all a reader has to name it
    by. What a system event says about itself (`BOOKKEEPING`: the event type, the operation it confirms) is
    left out as a field unless the type declares it one, the state reads in the space's word for it, and a
    yes or no in its words: "hearing 3dedea7b-... · Relatório pronto: True · last_event_type:
    hearing.report_prepared · operation: confirm_hearing · state: realizada" (juridico-sandbox, 05/10/2026).
    What the system last recorded still follows the name, in words (`last_recorded`: "dispute [Recibo 23058]
    · fee refunded"): it is how a reader knows the refund went through.

    A field the type does not name (`declared`: its fields, values, derived fields and time axes) is said too,
    by its name made readable, unless it may be personal data or free text (`domain.objects.undeclared`):
    the company's system sends it and its agents read it, with no configuration. A type nobody declared nor
    inferred names nothing, so every field it holds goes by that rule."""
    labels = labels or {}
    values = values or {}
    rank = {name: i for i, name in enumerate(order)}
    known, shaped, natural = names
    named = goes_by(obj.state, [n for n in known if n not in private], shaped, natural)
    kind = fmt.value(obj.key.type)
    if named:
        head = f"{kind} [{labels.get(named[0], named[0])} {named[1]}]"
    elif parent:
        head = f"{kind} {fmt.word('of_parent', name=parent)}"
    else:
        head = f"{kind} {obj.key.external_id}"
    recorded = None if EVENT_TYPE in rank else last_recorded(obj.key.type, obj.state)
    fields = [recorded] if recorded else []
    for k, v in sorted(obj.state.items(), key=lambda kv: (rank.get(kv[0], len(rank)), kv[0])):
        if (
            not isinstance(v, int | float | str)
            or v == ""
            or k in private
            or (k in BOOKKEEPING and k not in rank)
            or v == obj.key.external_id
            or opaque(v)
            or (named is not None and k == named[0])
        ):
            continue
        undeclared = k != LIFECYCLE and k not in declared
        if undeclared and not shown(k, v, declared):
            continue
        said = values.get(v, v) if isinstance(v, str) and (undeclared or k == LIFECYCLE) else v
        # An undeclared field that reads as a date is written as the space writes dates, worded or not: with
        # its observed word it still read "Disponibilizado em: 2026-10-05" (juridico-zero, 07/10/2026).
        dated = k in dates or (undeclared and is_dated(v))
        if undeclared and k not in labels:
            fields.append(f"{humanized(k)}: {_field_text(said, dated, fmt)}")
        else:
            fields.append(f"{_field_name(k, labels, dates, fmt)}: {_field_text(said, dated, fmt)}")
    return " · ".join([head, *fields])


def object_name(
    obj: BusinessObject,
    private: Collection[str],
    labels: Mapping[str, str],
    names: tuple[Sequence[str], Collection[str], Collection[str]],
) -> str | None:
    """What an object goes by on a line, its written key with the key's label ("Número CNJ 0801234-..."),
    None when it has none."""
    known, shaped, natural = names
    named = goes_by(obj.state, [n for n in known if n not in private], shaped, natural)
    return f"{labels.get(named[0], named[0])} {named[1]}" if named else None


def _field_name(name: str, labels: Mapping[str, str], dates: Collection[str], fmt: Formatter) -> str:
    """What a line calls a field: the state in the space's word, its label, a date with no label that says the
    object is due (`due_on`) as the object's due date ("vence em"), else its own name."""
    if name == LIFECYCLE:
        return fmt.word("lifecycle")
    if name in labels:
        return labels[name]
    if name in dates and "due" in name.split("_"):
        return fmt.word("due_date")
    return name


def _field_text(value: int | float | str, dated: bool, fmt: Formatter) -> str:
    if isinstance(value, bool):
        return fmt.word("yes" if value else "no")
    if isinstance(value, float):
        return f"{value:.2f}"
    if dated and isinstance(value, str):
        try:
            return (
                fmt.timestamp(datetime.fromisoformat(value))
                if "T" in value
                else fmt.calendar_day(date.fromisoformat(value))
            )
        except ValueError:
            return value
    return str(value)


def memory_year(inp: CompileInput) -> int | None:
    """The year, in the space's timezone, of the latest thing the memory holds: the times `CandidateBuilder`
    dates its items by, so a date of that year is written without it (tpl-12). None for an empty memory."""
    found: list[datetime] = []
    for snapshot in (inp.subject, *((inp.account,) if inp.account is not None else ())):
        found += [a.observed_at for a in snapshot.attrs]
        found += [min(f.last_confirmed_at, f.created_at) for f in snapshot.facts]
        found += [t.last_evidence_at for t in snapshot.traits]
        found += [e.ended_at for e in snapshot.episodes]
        found += [noted_at(i) for i in (*snapshot.open_items, *snapshot.kept_items)]
        found += [a.occurred_at for a in snapshot.actions]
        found += [o.as_of for o in snapshot.objects]
    if not found:
        return None
    return max(found).astimezone(ZoneInfo(inp.settings.timezone)).year


class CandidateBuilder:
    def __init__(self, inp: CompileInput, fmt: Formatter) -> None:
        self._inp = inp
        self._fmt = fmt
        self._subject_kind = inp.subject.profile.kind
        self._subject_profile = inp.subject.profile.profile_id
        # What each conversation was about: an open item passes the policy of its conversation too.
        episodes = [*inp.subject.episodes, *(inp.account.episodes if inp.account else ())]
        self._session_topics = {str(e.session_id): e.normalized_category for e in episodes}
        self._episode_topics = {e.episode_id: e.normalized_category for e in episodes}
        # Where each fact was first said: the channel of the conversation it came from.
        self._channels = {e.conversation_id: e.channel for e in episodes if e.conversation_id}
        # A skeleton leaves a fact's origin to the manifest (11, 6; tpl-12): who said it, on which channel and
        # when cost six tokens a line and no answer of the A/B read them.
        self._skeleton = inp.variant.slotted and inp.variant.view in SKELETON_VIEWS
        # The mirror rule (5.12): only rows of the person's confirmed handles are their own words, and only
        # for a reader through no unconfirmed link; a view that still holds one is such a reader's.
        profile = inp.subject.profile
        self._confirmed = frozenset(profile.handle_ids) - frozenset(profile.provisional_handle_ids)
        self._mirror_reader = inp.variant.reader_handle_id is None and not profile.provisional_handle_ids
        self._session_spoken = {str(e.session_id): e.spoken_by for e in inp.subject.episodes}

    def build(self) -> list[Candidate]:
        subject = self._inp.subject
        # An account read on its own carries its contacts' recent episodes and open items.
        contacts = frozenset(h for c in subject.contacts for h in c.handle_ids)
        found = [
            *self._attrs(subject.attrs),
            *self._facts(subject.facts, Section.FACTS, contradicted=subject.band_contradicted),
            *self._traits(subject.traits),
            *self._episodes(
                subject.episodes,
                Section.EPISODES,
                contacts=contacts,
                speakers=_speakers(subject),
                kept=_kept_by_session(subject),
            ),
            *self._open_items(subject.open_items, Section.PENDING, other=contacts),
            *self._open_items(subject.kept_items, Section.PENDING, other=contacts),
            *self._actions(subject.actions),
            *self._objects(subject.objects, Section.OBJECTS),
            *self._summaries(subject.summaries),
        ]
        if subject.contacts_total:
            found.append(self._contact_count(subject, Section.EPISODES))
        if self._inp.account is not None:
            found.extend(self._account(self._inp.account))
        return sorted(found, key=lambda c: c.item_id)

    def _attrs(self, attrs: Sequence[ProfileAttr]) -> Iterator[Candidate]:
        for slot, attr in sorted(surviving(attrs, self._inp.settings.slot_rules).items()):
            label = self._fmt.value(slot)
            text = attr.value if slot in _NAME_SLOTS else f"{label}: {attr.value}"
            yield Candidate(
                # Names sort first so the customer line opens with how to address them.
                item_id=f"attr:{0 if slot in _NAME_SLOTS else 1}:{slot}",
                kind=ItemKind.ATTR,
                section=Section.CUSTOMER,
                attrs=self._item_attrs("attr", "profile", handles=_handles(attr.origin_handle_id)),
                at=attr.observed_at,
                text=text,
                masked_text=f"{label}: {self._fmt.word('masked')}",
                weight=_repetition(attr.times_seen),
                stable=True,
            )

    def _facts(
        self,
        facts: Sequence[Fact],
        section: Section,
        *,
        other: frozenset[UUID] = frozenset(),
        contradicted: frozenset[UUID] = frozenset(),
    ) -> Iterator[Candidate]:
        for fact in facts:
            label = self._fmt.value(fact.predicate)
            # Valid time may lie ahead ("I move on the 30th"); a fact was observed at most when it was
            # written, so it never dates the pack, or ages everything else, from the future.
            seen = min(fact.last_confirmed_at, fact.created_at)
            yield Candidate(
                item_id=f"fact:{fact.fact_id}",
                kind=ItemKind.FACT,
                section=section,
                attrs=self._item_attrs(
                    "fact",
                    fact.category,
                    sensitivity=fact.sensitivity,
                    quarantined=fact.status is FactStatus.QUARANTINED,
                    handles=_handles(
                        fact.origin_handle_id, fact.about_handle_id, extra=fact.mention_handle_ids
                    ),
                    other_contact=fact.origin_handle_id in other,
                    purposes=self._collected_for(fact.purposes),
                    spoken_by=(spoken := fact_spoken_by(fact.speaker)),
                    own_words=self._own_words(
                        spoken, fact.origin_handle_id, fact.about_handle_id, fact.value
                    ),
                ),
                at=seen,
                text=self._contact_time(fact.value)
                if fact.predicate == CONTACT_TIME_FACT and section is Section.FACTS
                else f"{label}: {fact.value}"
                if self._skeleton
                else f"{label}: {fact.value} · {self._origin(fact)}",
                masked_text=self._masked(label, fact.origin_handle_id in other, seen),
                weight=fact_weight(fact, contradicted=fact.fact_id in contradicted),
                stable=section is Section.FACTS,
                origin_events=tuple(sorted(str(e) for e in fact.evidence_event_ids)),
                predicate=fact.predicate,
                object_ref=fact.object_ref,
            )

    def _contact_time(self, value: str) -> str:
        """The subject's contact time as their contact preference line: how the team reaches them, never a
        limit on what may be said to them (the compiler pins it beside the account's preferences)."""
        return f"{BOUND_WORDS[self._fmt.lang]['contact_any']}: {value}"

    def _traits(self, traits: Sequence[Trait], section: Section | None = None) -> Iterator[Candidate]:
        for trait in traits:
            stable = trait.layer.lower() in ("b1", "stable")
            label = self._fmt.value(trait.name)
            text = trait_text(trait, self._fmt)
            if not stable:
                text = f"{text} · {self._fmt.word('last_evidence')} {self._fmt.date(trait.last_evidence_at)}"
            target = section or (Section.STABLE_PATTERNS if stable else Section.VOLATILE_PATTERNS)
            yield Candidate(
                item_id=f"trait:{trait.trait_id}",
                kind=ItemKind.TRAIT,
                section=target,
                attrs=self._item_attrs(
                    "trait",
                    "trait",
                    purposes=self._inp.settings.trait_purposes.get(trait.name),
                    audiences=self._inp.settings.trait_audiences.get(trait.name),
                    topics=self._trait_topics(trait),
                ),
                at=trait.last_evidence_at,
                text=text,
                masked_text=self._masked(label, False, trait.last_evidence_at),
                weight=trait.confidence,
                stable=stable and target is Section.STABLE_PATTERNS,
                origin_events=tuple(sorted(str(ref) for kind, ref in trait.evidence if kind == "event")),
            )

    def _trait_topics(self, trait: Trait) -> tuple[str, ...]:
        """An account's pattern sums what several contacts said: it passes the policy of every category
        its conversations were filed under, so it takes the most restrictive of them (21.4)."""
        if not self._subject_kind.is_organization:
            return ()
        found = {self._episode_topics.get(ref) for kind, ref in trait.evidence if kind == "episode"}
        return tuple(sorted(t for t in found if t))

    def _episodes(
        self,
        episodes: Sequence[Episode],
        section: Section,
        *,
        contacts: frozenset[UUID],
        speakers: Mapping[UUID, str] | None = None,
        kept: Mapping[str, tuple[datetime, bool]] | None = None,
    ) -> Iterator[Candidate]:
        for episode in episodes:
            other = episode.origin_handle_id in contacts
            parts = [self._fmt.date(episode.started_at), episode.channel]
            if speaker := (speakers or {}).get(episode.origin_handle_id):
                parts.append(speaker)
            waiting = episode.outcome in _OPEN_OUTCOMES
            parts.append(episode.summary)
            # The summary was written when the conversation ended: a promise it left, kept since, would still
            # read as pending, and a model follows the summary. The items win: the line says they were kept.
            done = (kept or {}).get(str(episode.session_id)) if waiting else None
            if done is not None and not done[1]:
                waiting = False
                parts.append(self._fmt.word("promises_kept", date=self._fmt.date(done[0])))
            else:
                if episode.outcome != _UNSAID_OUTCOME:
                    parts.append(self._fmt.value(episode.outcome))
                if done is not None:
                    parts.append(self._fmt.word("partly_kept", date=self._fmt.date(done[0])))
            # A skeleton's solution goes only when it says more than the summary (17, 6.10).
            if episode.resolution and not (self._skeleton and repeats(episode.summary, episode.resolution)):
                parts.append(f"{self._fmt.word('solution')}: {episode.resolution}")
            yield Candidate(
                item_id=f"episode:{episode.episode_id}",
                kind=ItemKind.EPISODE,
                section=section,
                attrs=self._item_attrs(
                    "episode",
                    episode.normalized_category,
                    handles=_handles(episode.origin_handle_id, episode.about_handle_id),
                    other_contact=other,
                    purposes=self._collected_for(episode.purposes),
                    spoken_by=episode.spoken_by,
                    own_words=self._own_words(
                        episode.spoken_by,
                        episode.origin_handle_id,
                        episode.about_handle_id,
                        episode.summary,
                        episode.resolution,
                    ),
                ),
                at=episode.ended_at,
                text=" · ".join(parts),
                masked_text=self._masked(
                    self._fmt.term(episode.normalized_category), other, episode.started_at
                ),
                weight=1.3 if waiting else 1.0,
            )

    def _open_items(
        self, items: Sequence[OpenItem], section: Section, *, other: frozenset[UUID] = frozenset()
    ) -> Iterator[Candidate]:
        for item in items:
            # Its own due day is said beside it, never left inside its words (`without_due`).
            parts = [
                without_due(item.description, self._fmt.local_date(item.due_at))
                if item.due_at
                else item.description
            ]
            if item.status is OpenItemStatus.RESOLVED and item.closed_at is not None:
                parts.append(self._fmt.word("kept_on", date=self._fmt.date(item.closed_at)))
            elif item.due_at:
                parts.append(f"{self._fmt.word('due')} {self._fmt.date(item.due_at)}")
            if item.owner != "company":
                # A promise is the company's unless it says otherwise (tpl-12): only the customer's own
                # pending items are marked.
                parts.append(self._fmt.word("on_customer"))
            if item.status is OpenItemStatus.OVERDUE:
                parts.append(self._fmt.word("overdue"))
            yield Candidate(
                item_id=f"open_item:{item.item_id}",
                kind=ItemKind.OPEN_ITEM,
                section=section,
                attrs=self._item_attrs(
                    "open_item",
                    "open_item",
                    handles=_handles(item.origin_handle_id, item.about_handle_id),
                    other_contact=item.origin_handle_id in other,
                    topics=self._topics_of(item),
                    purposes=self._collected_for(item.purposes),
                    # A promise answers what was said in its conversation: it is as much the customer's own
                    # words as that conversation is.
                    spoken_by=(spoken_in := self._session_spoken.get(str(item.origin.get("session_id")))),
                    own_words=self._own_words(
                        spoken_in, item.origin_handle_id, item.about_handle_id, item.description
                    ),
                ),
                # When it was said, not when the server wrote it: an imported history is written at once.
                at=noted_at(item),
                text=" · ".join(parts),
                masked_text=self._masked(
                    self._fmt.section(Section.PENDING), item.origin_handle_id in other, noted_at(item)
                ),
                weight=_item_weight(item.status),
                # A promise already kept has no deadline left to meet: it never takes the due-soon boost.
                due_at=item.due_at if item.status is not OpenItemStatus.RESOLVED else None,
                object_type=item.object.type if item.object else None,
                origin_events=tuple(sorted(str(e) for e in evidence_event_ids(item))),
                object_ref=item.object,
                masked_is_header=True,
            )

    def _result(self, result: str) -> str:
        if len(result) <= ACTION_CHARS:
            return result
        return f"{result[:ACTION_CHARS]}… ({self._fmt.word('continues')})"

    def _actions(self, actions: Sequence[AgentAction]) -> Iterator[Candidate]:
        for action in actions:
            parts = [self._result(action.result) if action.result else self._fmt.value(action.operation)]
            if name := self._inp.settings.source_names.get(action.source_id):
                parts.append(name)
            parts.append(self._fmt.timestamp(action.occurred_at))
            if action.status is not ActionStatus.DECLARED:
                # What an agent says it did is the default (tpl-12); the line says only that the system
                # confirmed it, or that it diverges.
                parts.append(self._fmt.value(action.status.value))
            yield Candidate(
                item_id=f"action:{action.action_id}",
                kind=ItemKind.ACTION,
                section=Section.ACTIONS,
                attrs=self._item_attrs(
                    "action", "action", quarantined=action.status is ActionStatus.QUARANTINED
                ),
                at=action.occurred_at,
                text=" · ".join(parts),
                masked_text=self._masked(self._fmt.value(action.operation), False, action.occurred_at),
                weight=1.2 if action.status is ActionStatus.DIVERGENT else 1.0,
                origin_events=(str(action.event_id),),
                object_id=action.object_id,
            )

    def _all_objects(self) -> list[BusinessObject]:
        account = self._inp.account
        return [*self._inp.subject.objects, *(account.objects if account is not None else ())]

    def _objects(self, objects: Sequence[BusinessObject], section: Section) -> Iterator[Candidate]:
        settings = self._inp.settings
        named = {
            (obj.key.type, obj.key.external_id): name
            for obj in self._all_objects()
            if (
                name := object_name(
                    obj,
                    settings.private_fields.get(obj.key.type, frozenset()),
                    settings.field_labels.get(obj.key.type) or {},
                    settings.name_fields.get(obj.key.type, ((), frozenset(), frozenset())),
                )
            )
        }
        for obj in objects:
            # When the system of record last changed it, first, as a conversation line is dated: without it
            # an agent cannot tell what the customer said before or after a visit, a delivery or a new card.
            settings = self._inp.settings
            # A content field (a court's text) reaches a model only inside the state block's envelope.
            private = settings.private_fields.get(obj.key.type, frozenset()) | settings.content_fields.get(
                obj.key.type, frozenset()
            )
            order = settings.field_order.get(obj.key.type, ())
            said = object_text(
                obj,
                self._fmt,
                private,
                order,
                labels=settings.field_labels.get(obj.key.type),
                values=settings.value_words.get(obj.key.type),
                dates=settings.date_fields.get(obj.key.type, frozenset()),
                names=settings.name_fields.get(obj.key.type, ((), frozenset(), frozenset())),
                parent=next(
                    (
                        named[(kind, str(obj.state[via]))]
                        for via, kind in settings.parent_fields.get(obj.key.type, ())
                        if (kind, str(obj.state.get(via))) in named
                    ),
                    None,
                ),
                declared=settings.declared_names.get(obj.key.type, frozenset()),
            )
            text = f"{self._fmt.date(obj.as_of)} · {said}"
            yield Candidate(
                item_id=f"object:{obj.object_id}",
                kind=ItemKind.OBJECT,
                section=section,
                attrs=self._item_attrs("object", obj.key.type, handles=_handles(obj.origin_handle_id)),
                at=obj.as_of,
                text=text,
                masked_text=self._masked(self._fmt.value(obj.key.type), False, obj.as_of),
                object_type=obj.key.type,
                object_id=obj.object_id,
                object_ref=obj.key,
            )

    def _summaries(self, summaries: Sequence[tuple[str, str]]) -> Iterator[Candidate]:
        # A summary quotes every recent conversation, so it passes the policy of each of their categories.
        covered = tuple(sorted({e.normalized_category for e in self._inp.subject.episodes}))
        for level, text in sorted(summaries):
            yield Candidate(
                item_id=f"summary:{level}",
                kind=ItemKind.SUMMARY,
                section=Section.SUMMARY,
                attrs=self._item_attrs("summary", "history", topics=covered),
                at=_NO_TIME,
                text=text,
                masked_text=text,
                weight=0.8,
                stable=True,
            )

    def _account(self, account: MemorySnapshot) -> Iterator[Candidate]:
        # Items the account holds that came from other contacts than the reader's person (21.4).
        own = set(account.profile.handle_ids) | set(self._inp.person_handle_ids)
        contacts = frozenset(
            h
            for h in (
                *(f.origin_handle_id for f in account.facts),
                *(e.origin_handle_id for e in account.episodes),
                *(i.origin_handle_id for i in account.open_items),
            )
            if h not in own
        )
        speakers = _speakers(account)
        saved, self._subject_kind = self._subject_kind, account.profile.kind
        saved_profile, self._subject_profile = self._subject_profile, account.profile.profile_id
        try:
            yield from self._facts(
                account.facts, Section.ACCOUNT, other=contacts, contradicted=account.band_contradicted
            )
            yield from self._traits(account.traits, Section.ACCOUNT)
            yield from self._episodes(
                account.episodes,
                Section.ACCOUNT,
                contacts=contacts,
                speakers=speakers,
                kept=_kept_by_session(account),
            )
            yield from self._open_items(account.open_items, Section.ACCOUNT, other=contacts)
            yield from self._objects(account.objects, Section.ACCOUNT)
            if account.contacts_total:
                yield self._contact_count(account, Section.ACCOUNT)
        finally:
            self._subject_kind = saved
            self._subject_profile = saved_profile

    def _contact_count(self, account: MemorySnapshot, section: Section) -> Candidate:
        text = f"{account.contacts_total} {self._fmt.word('contacts')}"
        if account.contacts_total > len(account.contacts):
            text += f" · {len(account.contacts)} {self._fmt.word('in_detail')}"
        if account.contacts_unread:
            text += " · " + self._fmt.word("contacts_unread").format(count=account.contacts_unread)
        return Candidate(
            item_id="account:contacts",
            kind=ItemKind.SUMMARY,
            section=section,
            attrs=self._item_attrs("summary", "history"),
            at=_NO_TIME,
            text=text,
            masked_text=text,
            weight=0.5,
            stable=True,
        )

    def _origin(self, fact: Fact) -> str:
        """Who said it first, on which channel and when it became true (L6). The first observation's,
        so a fact seen again keeps its bytes and the stable layer stays cacheable."""
        channel = self._channels.get(fact.origin_conversation_id or "")
        parts = [self._fmt.word(f"said_by_{fact.speaker.value}")]
        if channel:
            parts.append(channel)
        parts.append(self._fmt.date(fact.valid_at))
        return " · ".join(parts)

    def _collected_for(self, tags: Tags | None) -> frozenset[str]:
        """The purposes that may read an item: those it was collected for and the compatible ones
        (13, 6.6). Empty for an untagged item, as every item is while the space declares no purposes."""
        return readers(tags, self._inp.settings.compatible_purposes)

    def _own_words(
        self,
        spoken_by: SpokenBy | None,
        origin_handle_id: UUID,
        about_handle_id: UUID | None,
        *texts: str | None,
    ) -> bool:
        """Whether an item is the customer's own words about themselves for this reader (the mirror rule,
        5.12): said by them, or in their conversation where the company stated nothing it holds; through a
        confirmed handle of the person the pack is about, not about an organization they speak for; read
        through no unconfirmed link; and naming nothing the product holds sensitive (health, beliefs),
        whatever category it was filed under."""
        return (
            spoken_by in (SpokenBy.CUSTOMER, SpokenBy.MIXED)
            and self._mirror_reader
            and self._subject_kind is SubjectKind.PERSON
            and origin_handle_id in self._confirmed
            and about_handle_id is None
            and not any(sensitive_topics(t) for t in texts if t)
        )

    def _topics_of(self, item: OpenItem) -> tuple[str, ...]:
        topic = self._session_topics.get(str(item.origin.get("session_id")))
        return (topic,) if topic else ()

    def _masked(self, label: str, other_contact: bool, at: datetime) -> str:
        if other_contact:
            return f"{self._fmt.word('other_contact')} · {label} · {self._fmt.date(at)}"
        return f"{label}: {self._fmt.word('masked')}"

    def _item_attrs(
        self,
        kind: str,
        category: str,
        *,
        sensitivity: Sensitivity = Sensitivity.NORMAL,
        handles: tuple[UUID, ...] = (),
        purposes: frozenset[str] | None = None,
        other_contact: bool = False,
        quarantined: bool = False,
        topics: tuple[str, ...] = (),
        audiences: frozenset[Audience] | None = None,
        spoken_by: SpokenBy | None = None,
        own_words: bool = False,
    ) -> ItemAttrs:
        return ItemAttrs(
            kind=kind,
            category=category,
            sensitivity=sensitivity,
            subject_kind=self._subject_kind,
            quarantined=quarantined,
            handle_ids=handles,
            other_contact=other_contact,
            purposes=purposes or frozenset(),
            topics=topics,
            audiences=audiences or frozenset(),
            profile_id=self._subject_profile,
            spoken_by=spoken_by,
            own_words=own_words,
        )


def policy_attrs(inp: CompileInput) -> dict[str, ItemAttrs]:
    """What the policy sees of every item, by the compiler's item id; the Console reads memory under the
    same attributes as the compiler, so a governance read and an agent read can never disagree (5.22)."""
    fmt = Formatter(inp.settings.timezone, inp.settings.locale)
    return {c.item_id: c.attrs for c in CandidateBuilder(inp, fmt).build()}
