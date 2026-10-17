"""Hard validation of the LLM answer (5.7): what fails a rule is dropped, never repaired by guessing.

- evidence must be turns of the batch's sessions, so hallucinated provenance is impossible by construction,
  and the turns an entry rests on say which session it belongs to (the session of its latest turn);
- cited ids must be among the aliases that were sent;
- enums are closed;
- a voice turn under the STT floor produces nothing;
- a turn the decision models flagged as injection can only produce quarantined facts, and no items,
  slots or values;
- a value must be written in a turn of the session, and nothing there may say it is a document, a card or a
  bank account or a secret; a redacted one comes back only when it was a postal code;
- a number of three digits or more that a turn said and nothing the model wrote keeps is added to the
  summary as said;
- a matter is settled once for the batch: the same words as a recent matter or as an earlier session's take
  its key (`matter.settle`);
- then every text the answer carries goes through `withhold`: a document, card or account number, a secret
  or misfiled sensitive content never reaches memory, whatever field the model put it in.
"""

import json
import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from niadra.domain.coordinate.window import CONTACT_TIME_FACT, InvalidWindow, Window, hours_words
from niadra.domain.extract.deadlines import with_deadlines, written_day
from niadra.domain.extract.matter import matter_key, matter_label, settle
from niadra.domain.extract.prompt import HOURS_CHANNELS, BatchMaterial, Prompt, SessionMaterial
from niadra.domain.extract.result import (
    AgentClaim,
    AgentQuestion,
    EpisodeDraft,
    ExtractionResult,
    FactCommand,
    FactOp,
    HoursDraft,
    InstructionDraft,
    MemoryRef,
    OpenItemDraft,
    QuestionType,
    SlotDraft,
    ValueDraft,
)
from niadra.domain.extract.rules import OBSERVATION, ExtractionRules
from niadra.domain.extract.withhold import Topics, held_numbers, marker_for, withhold_result
from niadra.domain.ingest.masking import language
from niadra.domain.memory.items import evidence_event_ids, told_done, written_amounts
from niadra.domain.memory.values import TOKEN, ValueType, locate, never_a_value, value_key
from niadra.domain.objects.operations import canonical_operation
from niadra.domain.objects.written import normalized, writes
from niadra.domain.policy.sensitive import sensitive_topics
from niadra.domain.records import EventRecord, ObjectKey, OpenItem
from niadra.domain.vocabulary import EventKind, OpenItemKind, Speaker, Visibility

FALLBACK_CATEGORY = "uncategorized"
AGENT_SPEAKERS = frozenset({Speaker.AI_AGENT, Speaker.HUMAN_AGENT})
VOICE_CHANNELS = frozenset({"voice"})
MAX_WRITTEN_VALUE = 64
STRENGTHS = frozenset({"must", "must_not"})
_ATTR = re.compile(r"^[a-z][a-z0-9_]{0,39}\.[a-z][a-z0-9_]{0,63}$")
# Redacted spans a value may carry back: a postal code settles a pickup, a document number never does.
_PLACEHOLDER = re.compile(r"\[([a-z_]+)_\d+\]")
RESTORABLE_IN_VALUES = frozenset({"postal_code"})


class InvalidExtractionError(ValueError):
    """The answer is not a JSON object with an entry for every session; the call is retried, then falls
    back (5.7)."""


def validate(
    raw: str,
    prompt: Prompt,
    batch: BatchMaterial,
    rules: ExtractionRules,
    topics: Topics = sensitive_topics,
) -> dict[UUID, ExtractionResult]:
    """Each session's result, keyed by its session id."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidExtractionError("the answer is not JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), list):
        raise InvalidExtractionError("the answer has no sessions")
    results = _Validator(prompt, batch, rules).run(data)
    held = held_numbers((e.text for e in batch.events if e.text), prompt.redactor.withheld())
    lang = language(rules.language)
    return {sid: withhold_result(r, rules, held, lang, topics) for sid, r in results.items()}


@dataclass
class _Parts:
    """What the answer gave one session, before it becomes the session's result."""

    facts: list[FactCommand] = field(default_factory=list)
    items: list[OpenItemDraft] = field(default_factory=list)
    resolved: list[MemoryRef] = field(default_factory=list)
    slots: list[SlotDraft] = field(default_factory=list)
    questions: list[AgentQuestion] = field(default_factory=list)
    claims: list[AgentClaim] = field(default_factory=list)
    values: list[ValueDraft] = field(default_factory=list)
    instructions: list[InstructionDraft] = field(default_factory=list)
    hours: list[HoursDraft] = field(default_factory=list)
    hours_fact: int | None = None
    """Where in `facts` the contact time fact made from these hours is (`_hours_fact`), not the model's."""
    dropped: int = 0
    unscoped: int = 0


class _Validator:
    def __init__(self, prompt: Prompt, batch: BatchMaterial, rules: ExtractionRules) -> None:
        self._p = prompt
        self._batch = batch
        self._rules = rules
        self._tz = ZoneInfo(rules.timezone)
        self._lang = language(rules.language)
        self._recent_matters = batch.recent_matters
        self._parts = {s.session.session_id: _Parts() for s in batch.sessions}
        self._channels = {s.session.session_id: s.session.channel for s in batch.sessions}
        self._last = batch.sessions[-1].session.session_id
        self.dropped = 0

    def run(self, data: dict[str, Any]) -> dict[UUID, ExtractionResult]:
        episodes = self._episodes(data["sessions"], self._matters(data.get("matters")))
        self._each(data.get("facts"), self._fact)
        self._each(data.get("items"), self._item)
        self._each(data.get("slots"), self._slot)
        self._each(data.get("asks"), self._question)
        self._each(data.get("claims"), self._claim)
        self._each(data.get("values"), self._values)
        self._each(data.get("instructions"), self._instruction)
        self._each(data.get("hours"), self._hours)
        # What these sessions resolved is resolved by the end of the batch: the last session carries it.
        self._parts[self._last].resolved += self._each(data.get("resolve"), self._resolved, place=False)
        results = {}
        for material in self._batch.sessions:
            sid = material.session.session_id
            parts, episode = self._parts[sid], episodes[sid]
            # A deadline the customer stated becomes an item with its date even when the model left it out.
            items = with_deadlines(
                with_amounts(parts.items, self._p.aliases.events.values()),
                self._deadline_turns(material),
                material.ended_at,
                self._rules.timezone,
                episode.matter_label,
                known_dues=[i.due_at for i in self._batch.open_items if i.due_at is not None],
            )
            episode = self._with_numbers(episode, material, parts)
            results[sid] = ExtractionResult(
                episode=episode,
                facts=tuple(parts.facts),
                open_items=tuple(items),
                resolved_items=tuple(dict.fromkeys(parts.resolved)),
                profile=tuple(parts.slots),
                agent_questions=tuple(parts.questions),
                agent_claims=tuple(parts.claims),
                values=tuple(_first_per_key(parts.values)),
                dropped=parts.dropped,
                instructions=tuple(parts.instructions),
                instruction_unscoped=parts.unscoped,
                object_unresolved=dict(self._p.aliases.unresolved) if sid == self._last else {},
                hours=tuple(parts.hours),
            )
        return results

    def _matters(self, raw: Any) -> dict[str, tuple[str | None, str | None]]:
        """Each session's (key, label), settled in the batch's order: a key with the words of a recent matter,
        or of an earlier session's, takes that key, so a count by matter finds every one of them."""
        given: dict[str, tuple[str | None, str | None]] = {}
        for entry in raw if isinstance(raw, list) else []:
            if not isinstance(entry, dict):
                continue
            key, label = matter_key(entry.get("key")), matter_label(entry.get("label"))
            for alias in _refs(entry.get("sessions")):
                if alias in self._p.aliases.sessions and alias not in given:
                    given[alias] = (key, label)
        known = list(self._recent_matters)
        settled: dict[str, tuple[str | None, str | None]] = {}
        for alias in self._p.aliases.sessions:
            key, label = settle(*given.get(alias, (None, None)), known)
            settled[alias] = (key, label)
            if key is not None and all(key != k for k, _ in known):
                known.append((key, label or ""))
        return settled

    def _episodes(
        self, raw: list[Any], matters: dict[str, tuple[str | None, str | None]]
    ) -> dict[UUID, EpisodeDraft]:
        given = {
            str(entry.get("id")): entry
            for entry in raw
            if isinstance(entry, dict) and str(entry.get("id")) in self._p.aliases.sessions
        }
        if set(given) != set(self._p.aliases.sessions):
            raise InvalidExtractionError("the answer misses a session")
        return {
            material.session.session_id: self._episode(given[alias], material, matters[alias])
            for alias, material in self._p.aliases.sessions.items()
        }

    def _each[T](self, raw: Any, parse: Callable[[Any], T | None], *, place: bool = True) -> list[T]:
        """Parses every entry; one that fails a rule is dropped and counted on the batch's last session, the
        session an entry without a turn of its own is filed under."""
        if not isinstance(raw, list):
            return []
        out: list[T] = []
        for entry in raw:
            try:
                parsed = parse(entry)
            except (KeyError, TypeError, ValueError, AttributeError):
                parsed = None
            if parsed is None:
                self._parts[self._last].dropped += 1
            elif not place:
                out.append(parsed)
        return out

    def _episode(
        self, raw: dict[str, Any], material: SessionMaterial, matter: tuple[str | None, str | None]
    ) -> EpisodeDraft:
        summary = self._text(raw.get("summary")) or ""
        resolution = self._text(raw.get("resolution"))
        key, label = matter
        return EpisodeDraft(
            summary=summary,
            intent=self._closed(raw.get("intent"), self._rules.intents, "other", material),
            category=self._closed(
                raw.get("category"), self._rules.episode_categories, FALLBACK_CATEGORY, material
            ),
            outcome=self._closed(raw.get("outcome"), self._rules.outcomes, "unresolved", material),
            resolution=resolution,
            sentiment=_sentiment(raw.get("sentiment")),
            matter=key,
            matter_label=label if key else None,
        )

    def _with_numbers(self, episode: EpisodeDraft, material: SessionMaterial, parts: _Parts) -> EpisodeDraft:
        """The numbers of three digits or more the session's turns said that nothing the model wrote for it
        keeps, added to the summary as said: an agent later answers from the summary alone, and a model
        without reasoning sometimes leaves "the slip ending 94843" as "a slip". What memory never keeps is
        withheld after this, like any text."""
        kept = " ".join(
            [
                episode.summary,
                episode.resolution or "",
                *(f.value for f in parts.facts),
                *(i.description for i in parts.items),
                *(v.raw for v in parts.values),
            ]
        )
        have = {_digits(t) for t in TOKEN.findall(kept)}
        # Never a number the PII model took out of the prompt (a phone, an e-mail, a document), nor one that
        # is never a value (a mobile number, a number with a document's or a card's check digit).
        redacted = {_digits(v) for v in self._p.redactor.redacted()}
        missing: list[str] = []
        for event in sorted(material.events, key=lambda e: (e.occurred_at, e.event_id)):
            if not _may_settle(event, self._p):
                continue
            for token in TOKEN.findall(event.text or ""):
                token = token.rstrip(".,:;/-")
                digits = _digits(token)
                if any(digits in r for r in redacted) or never_a_value(token):
                    continue
                if digits not in have and token not in missing:
                    missing.append(token)
        if not missing:
            return episode
        said = f"{_NUMBERS_SAID.get(self._lang, _NUMBERS_SAID['en'])}: {', '.join(missing)}."
        summary = f"{episode.summary.rstrip()} {said}" if episode.summary else said
        return replace(episode, summary=summary)

    def _deadline_turns(self, material: SessionMaterial) -> list[EventRecord]:
        """The customer's turns a deadline may come from: said by them, trusted, and heard well enough."""
        mine = {e.event_id for e in material.events}
        return [
            e
            for e in sorted(self._p.aliases.events.values(), key=lambda e: (e.occurred_at, e.event_id))
            if e.event_id in mine and e.speaker is Speaker.CUSTOMER and _may_settle(e, self._p)
        ]

    def _home(self, turns: Sequence[EventRecord]) -> _Parts:
        """The session an entry belongs to: the one of its latest turn."""
        latest = max(turns, key=lambda e: (e.occurred_at, e.event_id))
        return self._parts[self._p.aliases.session_of[latest.event_id]]

    def _fact(self, raw: dict[str, Any]) -> bool | None:
        op = FactOp(raw["op"])
        predicate = self._rules.predicate(raw["predicate"])
        refs = _refs(raw.get("refs"))
        evidence = self._evidence(refs)
        if predicate is None or not evidence:
            return None
        cited = self._fact_refs([r for r in refs if r.startswith("f")])
        if cited is None:
            return None
        target = None
        contradicts = cited
        if op is FactOp.RETIRE:
            same = [f for r in refs if (f := self._p.aliases.facts.get(r)) and f.predicate == predicate.name]
            fact = same[0] if same else None
            if fact is None:
                return None
            target = MemoryRef(fact.origin_handle_id, fact.fact_id)
            contradicts = [c for c in cited if c.id != fact.fact_id]
            value: str | None = fact.value
        else:
            value = self._text(raw.get("value"))
            if predicate.enum is not None and value not in predicate.enum:
                return None
        if not value:
            return None
        object_ref = self._object(refs)
        if predicate.observes and any(e.speaker is not Speaker.CUSTOMER for e in evidence):
            # A field's value is what the person said, beside the record: an agent's word is a claim, and kept
            # as a fact it would read as the person's.
            return None
        if predicate.name == OBSERVATION or predicate.observes:
            # What was said about an object is the object's or nothing: without an id in refs, the object
            # its turns are tagged with by the company's system (the first rule of resolution), when one.
            object_ref = object_ref or self._tagged(evidence)
            if object_ref is None:
                return None
        about = self._about(refs, evidence)
        if predicate.organization:
            # The organization's own fact, never the contact's: kept only when the contact acts for one.
            about = about or self._only_organization()
            if about is None:
                return None
        first = min(evidence, key=lambda e: (e.occurred_at, e.event_id))
        self._home(evidence).facts.append(
            FactCommand(
                op=op,
                predicate=predicate.name,
                namespace=predicate.namespace,
                evidence_event_ids=tuple(e.event_id for e in evidence),
                value=value,
                category=predicate.category,
                sensitivity=predicate.sensitivity,
                speaker=first.speaker,
                certainty=_unit(raw.get("certainty"), 1.0),
                valid_at=self._time(raw.get("since")) or max(e.occurred_at for e in evidence),
                target=target,
                contradicts=tuple(contradicts),
                about_handle_id=about,
                object_ref=object_ref,
                quarantined=any(e.event_id in self._p.flagged for e in evidence),
                from_voice=self._voice(evidence),
            )
        )
        return True

    def _item(self, raw: dict[str, Any]) -> bool | None:
        refs = _refs(raw.get("refs"))
        evidence = self._evidence(refs)
        description = self._text(raw.get("text"))
        owner = raw.get("owner")
        if not evidence or not description or owner not in ("company", "customer"):
            return None
        if any(e.event_id in self._p.flagged for e in evidence):
            return None
        # Without an id in refs, the one object its turns are tagged with or write the key of: the client who
        # names the case before the agent promises a call about it.
        obj = self._object(refs) or self._named(evidence)
        due = self._time(raw.get("due")) or written_day(
            [e.text or "" for e in evidence], min(e.occurred_at for e in evidence), self._rules.timezone
        )
        if due is not None and unbacked_day(due, evidence, self._rules.timezone):
            due = None
        relayed = not self._rules.staff and any(e.speaker in AGENT_SPEAKERS for e in evidence)
        self._home(evidence).items.append(
            OpenItemDraft(
                description=description,
                owner=owner,
                evidence_event_ids=tuple(e.event_id for e in evidence),
                due_at=due,
                object=obj,
                expected_operation=canonical_operation(self._text(raw.get("operation"))),
                about_handle_id=self._about(refs, evidence),
                kind=OpenItemKind(raw["kind"]) if raw.get("kind") in _KINDS else OpenItemKind.PROMISE,
                grounded=obj is None or obj in self._spoken_of(evidence),
                relayed=relayed,
                agent_day=relayed and due is not None and agents_day(due, evidence, self._rules.timezone),
            )
        )
        return True

    def _hours(self, raw: dict[str, Any]) -> bool | None:
        """Only the hours a customer said, in turns that may settle memory: a channel the answer may name, two
        clock times that make a window or a lift, weekdays from 1 to 7. Nothing an agent said, nothing
        inferred."""
        evidence = self._evidence(_refs(raw.get("refs")))
        if not evidence or any(
            e.speaker is not Speaker.CUSTOMER or not _may_settle(e, self._p) for e in evidence
        ):
            return None
        channel = raw.get("channel")
        if channel not in HOURS_CHANNELS:
            return None
        given = raw.get("days")
        days: list[Any] = given if isinstance(given, list) else []
        lift = raw.get("lift") is True
        before, after = raw.get("not_before"), raw.get("not_after")
        try:
            window = None if lift else Window.said(before, after, self._rules.timezone, days)
            if lift and (before is not None or after is not None):
                return None
            if any(not isinstance(d, int) or isinstance(d, bool) or not 1 <= d <= 7 for d in days):
                return None
        except InvalidWindow:
            return None
        home = self._home(evidence)
        draft = HoursDraft(
            channel=None if channel == "any" else channel,
            not_before=None if lift else before,
            not_after=None if lift else after,
            days=tuple(sorted(set(days))),
            lift=lift,
            evidence_event_ids=tuple(e.event_id for e in evidence),
        )
        home.hours.append(draft)
        self._hours_fact(home, draft, window, evidence)
        return True

    def _hours_fact(
        self, home: "_Parts", draft: HoursDraft, window: Window | None, evidence: Sequence[EventRecord]
    ) -> None:
        """The hours as the person's contact time fact, when the space keeps one and the model kept no such
        fact from these turns in the person's own words: the default block carries it as a contact
        preference (juridico-zero-sandbox, 10/10/2026: the window held a call at 07:00, but no line of a plain
        read said it, since p26 asked the hours apart and the model left the fact out). The hours of one
        session make one fact, in the words the constraints block uses; a later one replaces it."""
        predicate = self._rules.predicate(CONTACT_TIME_FACT)
        if predicate is None:
            return
        said = hours_words(window, draft.channel, self._lang)
        if home.hours_fact is not None:
            before = home.facts[home.hours_fact]
            ids = tuple(dict.fromkeys((*before.evidence_event_ids, *draft.evidence_event_ids)))
            value = f"{before.value}; {said}"
            home.facts[home.hours_fact] = replace(before, value=value, evidence_event_ids=ids)
            return
        if any(f.predicate == CONTACT_TIME_FACT for f in home.facts):
            return  # the model's own fact, in the person's words: it stays as they said it
        home.facts.append(
            FactCommand(
                op=FactOp.ADD,
                predicate=predicate.name,
                namespace=predicate.namespace,
                evidence_event_ids=draft.evidence_event_ids,
                value=said,
                category=predicate.category,
                sensitivity=predicate.sensitivity,
                speaker=Speaker.CUSTOMER,
                valid_at=max(e.occurred_at for e in evidence),
                quarantined=any(e.event_id in self._p.flagged for e in evidence),
                from_voice=self._voice(evidence),
            )
        )
        home.hours_fact = len(home.facts) - 1

    def _instruction(self, raw: dict[str, Any]) -> bool | None:
        """Only what the person said, in turns that may settle memory, with the object it is about; one with
        no object is dropped and counted apart."""
        refs = _refs(raw.get("refs"))
        evidence = self._evidence(refs)
        text = self._text(raw.get("text"))
        strength = raw.get("strength")
        if not evidence or not text or strength not in STRENGTHS:
            return None
        if any(e.speaker is not Speaker.CUSTOMER or not _may_settle(e, self._p) for e in evidence):
            return None
        obj = self._grounded(self._object(refs), evidence)
        # Naming no object, an instruction is about the organization the person speaks of, when the link says
        # which one: a client of the firm, its readers get it as an object's do.
        about = None if obj is not None else self._about(refs, evidence) or self._only_organization()
        if obj is None and about is None:
            self._home(evidence).unscoped += 1
            return True
        attr = raw.get("attr")
        fits = (
            obj is not None
            and isinstance(attr, str)
            and _ATTR.match(attr)
            and attr.split(".", 1)[0] == obj.type
        )
        self._home(evidence).instructions.append(
            InstructionDraft(
                text=text,
                strength=str(strength),
                object=obj,
                evidence_event_ids=tuple(e.event_id for e in evidence),
                attr=str(attr) if fits else None,
                about_handle_id=about,
                only_with=self._only_with(raw.get("only_with"), text, evidence),
                contact=raw.get("contact") is True,
            )
        )
        return True

    def _only_with(self, value: Any, text: str, evidence: Sequence[EventRecord]) -> str | None:
        """The person an instruction holds with, only as the instruction or its turns write them: a name the
        model made up, or reworded, names no one."""
        said = self._text(value)
        if said is None:
            return None
        written = normalized(" ".join([text, *(e.text or "" for e in evidence)]))
        return said if writes(written, normalized(said)) else None

    def _resolved(self, alias: Any) -> MemoryRef | None:
        item = self._p.aliases.items.get(str(alias))
        if item is None:
            return None
        # An item that waits for an operation is closed by that operation, from an action or a system event;
        # a conversation resolves it only when one of its turns tells it done. A promise says its act and
        # words too: never the turn it was made in (a session extracted again carries it), nor a turn this
        # answer reads as the same promise made again ("vou avisar o cliente da audiência hoje").
        promised = {*evidence_event_ids(item), *self._promised_again(item)}
        if item.expected_operation is not None and not any(
            told_done(e.text, item.description) for e in self._batch.events if e.event_id not in promised
        ):
            return None
        return MemoryRef(item.origin_handle_id, item.item_id)

    def _promised_again(self, item: OpenItem) -> set[UUID]:
        """The turns of this answer's items that make `item`'s promise again: what they say is promised, not
        done. A turn that tells one thing done and promises another ("já avisei o cliente e vou enviar a
        cópia") still tells the first done."""
        return {
            e
            for parts in self._parts.values()
            for d in parts.items
            if told_done(d.description, item.description)
            for e in d.evidence_event_ids
        }

    def _slot(self, raw: dict[str, Any]) -> bool | None:
        evidence = self._evidence(_refs(raw.get("refs")))
        value = self._text(raw.get("value"))
        known = {s.name for s in self._rules.slots}
        if raw.get("slot") not in known or not evidence or not value:
            return None
        if any(e.event_id in self._p.flagged for e in evidence):
            return None
        self._home(evidence).slots.append(SlotDraft(raw["slot"], value, tuple(e.event_id for e in evidence)))
        return True

    def _question(self, raw: dict[str, Any]) -> bool | None:
        refs = _refs(raw.get("refs"))
        event = self._agent_turn(refs)
        if event is None:
            return None
        # The type is the decision model's (`app/extract.py`); ambiguous until it answers.
        self._home([event]).questions.append(
            AgentQuestion(
                QuestionType.AMBIGUOUS,
                event.event_id,
                self._known_predicate(raw.get("predicate")),
                self._object(refs),
            )
        )
        return True

    def _claim(self, raw: dict[str, Any]) -> bool | None:
        refs = _refs(raw.get("refs"))
        event = self._agent_turn(refs)
        # A contradicted fact that was never a candidate is a hallucination: the claim goes with it,
        # since a made-up contradiction would be charged to the agent or to the selection (19.2).
        contradicts = self._fact_refs([r for r in refs if r.startswith("f")])
        if event is None or contradicts is None:
            return None
        # Without a value of its own the claim is what the turn said (the measurement finds numbers in it).
        value = self._text(raw.get("value")) or self._text(event.text)
        if not value:
            return None
        self._home([event]).claims.append(
            AgentClaim(
                event.event_id,
                value,
                self._known_predicate(raw.get("predicate")),
                self._object(refs),
                tuple(dict.fromkeys(ref.id for ref in contradicts)),
            )
        )
        return True

    def _values(self, raw: Any) -> bool | None:
        """ "<type> <value>": kept as the turn wrote it, in each session whose turns state it, with those
        turns as evidence."""
        if not isinstance(raw, str):
            return None
        kind, _, written = raw.strip().partition(" ")
        written = written.strip()
        if not written or len(written) > MAX_WRITTEN_VALUE:
            return None
        if any(m.group(1) not in RESTORABLE_IN_VALUES for m in _PLACEHOLDER.finditer(written)):
            return None
        value_type = ValueType(kind)
        value = self._p.redactor.restore(written)
        found = [
            (event, as_written)
            for event in sorted(self._p.aliases.events.values(), key=lambda e: (e.occurred_at, e.event_id))
            if _may_settle(event, self._p) and (as_written := locate(event.text or "", value)) is not None
        ]
        if not found:
            return None
        by_session: dict[UUID, list[tuple[EventRecord, str]]] = {}
        for event, as_written in found:
            by_session.setdefault(self._p.aliases.session_of[event.event_id], []).append((event, as_written))
        for sid, hits in by_session.items():
            self._parts[sid].values.append(
                ValueDraft(value_type, hits[0][1], tuple(e.event_id for e, _ in hits))
            )
        return True

    def _evidence(self, refs: Sequence[str]) -> list[EventRecord]:
        """Turns of the batch only: an unknown turn id voids the entry, a turn heard poorly does not count."""
        turns = [r for r in refs if r.startswith("e")]
        events = [self._p.aliases.events.get(r) for r in turns]
        if any(e is None for e in events):
            return []
        usable = {e.event_id: e for e in events if e is not None and e.event_id not in self._p.low_confidence}
        return list(usable.values())

    def _agent_turn(self, refs: Sequence[str]) -> EventRecord | None:
        # 19.2: an agent turn under the STT floor yields neither a question nor a claim.
        for ref in refs:
            event = self._p.aliases.events.get(ref)
            if event is not None:
                if event.speaker not in AGENT_SPEAKERS or event.event_id in self._p.low_confidence:
                    return None
                return event
        return None

    def _fact_refs(self, aliases: Sequence[str]) -> list[MemoryRef] | None:
        facts = [self._p.aliases.facts.get(a) for a in aliases]
        if any(f is None for f in facts):
            return None
        return [MemoryRef(f.origin_handle_id, f.fact_id) for f in facts if f is not None]

    def _object(self, refs: Sequence[str]) -> ObjectKey | None:
        """An id the input never offered (an order number, say) is dropped, not the entry it came with."""
        return next((self._p.aliases.objects[r] for r in refs if r in self._p.aliases.objects), None)

    def _tagged(self, evidence: Sequence[EventRecord]) -> ObjectKey | None:
        """The one object the evidence turns are tagged with (`object_refs`), when it is one the input
        offered; none when they name none or more than one."""
        offered = set(self._p.aliases.objects.values())
        tagged = {k for e in evidence for k in e.object_refs if k in offered}
        return tagged.pop() if len(tagged) == 1 else None

    def _grounded(self, obj: ObjectKey | None, evidence: Sequence[EventRecord]) -> ObjectKey | None:
        """The object an instruction is about, only as its own turns name it: tagged with it or writing its
        key; otherwise the one object they are tagged with, and with none, no object, so the instruction is
        the organization's. Round 9 (juridico-sandbox, 06/10/2026): "não falar de valores dos processos com
        Clara", said in a note no case was tagged on, was stored on the one case the batch offered, and Clara
        got the amount of the client's other case."""
        if obj is None:
            return None
        return obj if obj in self._spoken_of(evidence) else self._tagged(evidence)

    def _spoken_of(self, evidence: Sequence[EventRecord]) -> set[ObjectKey]:
        """The objects the turns themselves name: tagged with them, or writing their key."""
        return {k for e in evidence for k in (*e.object_refs, *self._p.aliases.written.get(e.event_id, ()))}

    def _named(self, evidence: Sequence[EventRecord]) -> ObjectKey | None:
        """The one offered object the evidence turns are tagged with or write the natural key of (rules 1
        and 2 of resolution); none when they name none or more than one."""
        offered = set(self._p.aliases.objects.values())
        named = {k for e in evidence for k in (*e.object_refs, *self._p.aliases.written.get(e.event_id, ()))}
        named &= offered
        return named.pop() if len(named) == 1 else None

    def _about(self, refs: Sequence[str], evidence: Sequence[EventRecord]) -> UUID | None:
        """Only with exactly one active link, or when the evidence carries the organization (5.7, rev. 10)."""
        link = next((self._p.aliases.links[r] for r in refs if r in self._p.aliases.links), None)
        if link is None:
            return None
        if len(self._p.aliases.links) == 1 or any(link.org_handle_id in e.handle_ids for e in evidence):
            return link.org_handle_id
        return None

    def _only_organization(self) -> UUID | None:
        organizations = {link.org_handle_id for link in self._p.aliases.links.values()}
        return organizations.pop() if len(organizations) == 1 else None

    def _voice(self, evidence: Sequence[EventRecord]) -> bool:
        return any(
            e.stt_confidence is not None
            or self._channels[self._p.aliases.session_of[e.event_id]] in VOICE_CHANNELS
            for e in evidence
        )

    def _known_predicate(self, name: Any) -> str | None:
        return name if isinstance(name, str) and self._rules.predicate(name) else None

    def _closed(self, value: Any, allowed: Sequence[str], fallback: str, material: SessionMaterial) -> str:
        if not allowed:
            return self._text(value) or fallback
        if value in allowed:
            return str(value)
        self._parts[material.session.session_id].dropped += 1
        return fallback

    def _text(self, value: Any) -> str | None:
        """What the model wrote, whole: memory never cuts what it learned. A long text is bounded where one
        read shows it, and stays whole in the history."""
        if not isinstance(value, str) or not value.strip():
            return None
        return self._p.redactor.restore(value.strip(), self._marker)

    def _marker(self, kind: str) -> str:
        return marker_for(kind, self._lang)

    def _time(self, value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=self._tz)


_KINDS = frozenset(k.value for k in OpenItemKind)
_NUMBERS_SAID = {"pt": "Números citados", "es": "Números citados", "en": "Numbers said"}


def unbacked_day(due: datetime, evidence: Sequence[EventRecord], timezone: str) -> bool:
    """Whether an item's due day comes only from an agent's turn that stated a date nothing it had backs:
    the SDK's check of that turn (`backing`, 13, 6.2) found an unbacked date, the turn writes the due's day,
    and no turn of anyone else in the evidence writes it. The client's attendant told "enviar os extratos
    até 27/10", a day no memory held (the lawyer had asked for 06/10), and the item was opened due on the
    27th (juridico-zero, 07/10/2026). The item stays, undated, so a retelling of an open item can still
    be told apart (`domain.memory.items.to_judge_retelling`). A relative day ("o advogado liga amanhã") is
    not a date value the check reads, so an agent's own new commitment keeps its day."""
    doubted = [e for e in evidence if e.speaker in AGENT_SPEAKERS and _unbacked_dates(e)]
    return agents_day(due, doubted, timezone) and agents_day(due, evidence, timezone)


def agents_day(due: datetime, evidence: Sequence[EventRecord], timezone: str) -> bool:
    """Whether an agent's turn of the evidence writes the due's day and no turn of anyone else does."""
    written = _day_written(due, timezone)
    if not any(e.speaker in AGENT_SPEAKERS and written.search(e.text or "") for e in evidence):
        return False
    return not any(e.speaker not in AGENT_SPEAKERS and written.search(e.text or "") for e in evidence)


def _day_written(due: datetime, timezone: str) -> re.Pattern[str]:
    day = due.astimezone(ZoneInfo(timezone)).date()
    return re.compile(rf"(?<!\d)0?{day.day}\s*[/.-]\s*0?{day.month}(?!\d)|{day.isoformat()}")


def _unbacked_dates(event: EventRecord) -> bool:
    backing = (event.content_meta or {}).get("backing")
    values = backing.get("unbacked_values") if isinstance(backing, dict) else None
    return isinstance(values, list) and any(isinstance(v, dict) and v.get("kind") == "date" for v in values)


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _refs(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(r).strip("[] ") for r in raw if isinstance(r, str)]


def _first_per_key(values: Sequence[ValueDraft]) -> list[ValueDraft]:
    kept: dict[str, ValueDraft] = {}
    for value in values:
        kept.setdefault(value_key(value.raw), value)
    return list(kept.values())


def _may_settle(event: EventRecord, prompt: Prompt) -> bool:
    """A turn a value may come from: said to the customer, trusted, and heard well enough."""
    return (
        event.kind is EventKind.MESSAGE
        and bool(event.text)
        and event.visibility is Visibility.PUBLIC
        and event.event_id not in prompt.flagged
        and event.event_id not in prompt.low_confidence
    )


def _unit(value: Any, default: float) -> float:
    if not isinstance(value, int | float) or math.isnan(value):
        return default
    return min(1.0, max(0.0, float(value)))


def _sentiment(value: Any) -> float | None:
    if not isinstance(value, int | float) or math.isnan(value):
        return None
    return min(1.0, max(-1.0, float(value)))


def with_amounts(items: Sequence[OpenItemDraft], events: Iterable[EventRecord]) -> list[OpenItemDraft]:
    """An item whose text names no amount keeps the one amount its own turns state, as they write it, when no
    other item rests on those turns: "Vou solicitar o estorno de R$ 112,64 da tarifa" written as "Solicitar
    estorno da tarifa" never closed when the system refunded 112.64 (`items.closes_by_amount` reads the
    amount in the text), and its line no longer said how much (benchmark promise_action: 27 of 33 items kept
    their amount on 30/09, 8 of 29 on 09/10/2026). A turn two items rest on says whose amount it is only to
    the model, and only an amount written with its currency counts: a case number holds "8.26"."""
    said = {e.event_id: e.text or "" for e in events}
    shared = Counter(e for i in items for e in set(i.evidence_event_ids))
    kept: list[OpenItemDraft] = []
    for item in items:
        if written_amounts(item.description) or any(shared[e] > 1 for e in item.evidence_event_ids):
            kept.append(item)
            continue
        found: dict[Decimal, str] = {}
        for event_id in item.evidence_event_ids:
            for value, written in written_amounts(said.get(event_id, ""), marked=True).items():
                found.setdefault(value, written)
        if len(found) != 1:
            kept.append(item)
            continue
        [written] = found.values()
        kept.append(replace(item, description=f"{item.description.rstrip(' .')} ({written})"))
    return kept
