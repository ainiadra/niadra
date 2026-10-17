"""Turns the stored shapes that context-use measurement reads (receipts, manifests, extraction output,
events) into its model.

The shapes are the ones other modules store: a receipt row (5.13), a manifest (P7, 5.10) and the
extraction output with `agent_questions[]` and `agent_claims[]` (5.7).
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from niadra.domain.compile import etag_for
from niadra.domain.compile.model import public_item_id
from niadra.domain.extract.result import QuestionType
from niadra.domain.measure.model import (
    AgentAnswer,
    AgentClaim,
    AgentQuestion,
    ContextStamp,
    Delivery,
    Handoff,
    Manifest,
    ManifestItem,
    SessionAction,
)
from niadra.domain.measure.slots import ChannelRun, GuardEntry, SlotDelivery, SlotEntry
from niadra.domain.records import AgentAction, EventRecord
from niadra.domain.vocabulary import EventKind, ReceiptKind, ReceiptSurface, Speaker

HANDOFF_EVENT = "handoff"
UNUSED = "unused"
"""The exclusion reason of an item the source's trim left out (15.8)."""
CONTROL_GROUP = "control"
_AGENT_SPEAKERS = frozenset({Speaker.AI_AGENT, Speaker.HUMAN_AGENT})
_DELIVERY_KINDS = frozenset({ReceiptKind.READ.value, ReceiptKind.HOLDOUT.value})


def object_ref_key(ref: Mapping[str, Any] | None) -> str | None:
    """One key for an object however it was stored: manifests write `id`, the extraction output
    writes the record's `external_id`."""
    external_id = (ref.get("id") or ref.get("external_id")) if ref else None
    if not ref or not ref.get("type") or not external_id:
        return None
    return f"{ref['type']}/{ref.get('namespace') or ''}/{external_id}"


def deliveries_from_receipts(
    receipts: Iterable[Mapping[str, Any]], *, since: datetime, until: datetime
) -> list[Delivery]:
    """One delivery per (manifest, source): repeated reads of a pinned pack do not multiply rows (19.3)."""
    first: dict[tuple[str, UUID], Delivery] = {}
    for receipt in receipts:
        manifest_hash, source_id, read_at = (
            receipt.get("manifest_hash"),
            receipt.get("source_id"),
            receipt["read_at"],
        )
        if receipt.get("kind") not in _DELIVERY_KINDS or not manifest_hash or source_id is None:
            continue
        if not since <= read_at <= until:
            continue
        holdout = receipt.get("kind") == ReceiptKind.HOLDOUT.value
        group = receipt.get("experiment_group") or (CONTROL_GROUP if holdout else "")
        pack_hash = receipt.get("pack_hash")
        key = (manifest_hash, _uuid(source_id))
        if key not in first or read_at < first[key].delivered_at:
            first[key] = Delivery(
                manifest_hash,
                key[1],
                read_at,
                holdout=holdout,
                experiment_group=group,
                etag=etag_for(pack_hash) if pack_hash else None,
                profile_id=_maybe_uuid(receipt.get("profile_id")),
            )
    return sorted(first.values(), key=lambda d: (d.delivered_at, d.manifest_hash, str(d.source_id)))


def slot_deliveries_from_receipts(
    receipts: Iterable[Mapping[str, Any]], *, since: datetime, until: datetime
) -> list[SlotDelivery]:
    """The per-turn slots of the session's reads (11, 3.4), from the `slots` receipts: the plan WP2 keeps
    in `detail.slots` (ids, kinds, channels, positions, tokens), with `detail.why` beside it when the read
    wrote one. A receipt whose plan does not parse is skipped; it never fails the measurement."""
    found = []
    for receipt in receipts:
        source_id, read_at = receipt.get("source_id"), receipt["read_at"]
        if (
            receipt.get("surface") != ReceiptSurface.SLOTS.value
            or receipt.get("kind") != ReceiptKind.READ.value
        ):
            continue
        if source_id is None or not since <= read_at <= until:
            continue
        detail = receipt.get("detail") or {}
        plan = _json(detail.get("slots"))
        if plan is None:
            continue
        why = _json(detail.get("why")) or {}
        reasons = {str(w["id"]): w for w in why.get("items") or () if isinstance(w, Mapping) and w.get("id")}
        items = tuple(
            entry
            for e in plan.get("items") or ()
            if (entry := _slot_entry(e, _reason(reasons, e))) is not None
        )
        runs = plan.get("channels")
        if not isinstance(runs, Mapping):
            runs = {}
        channels = tuple(
            (str(name), ChannelRun(int(run.get("hits") or 0), bool(run.get("skipped"))))
            for name, run in sorted(runs.items())
            if isinstance(run, Mapping)
        )
        derived = tuple(d for d in why.get("derived") or () if isinstance(d, Mapping))
        version = why.get("weights_version")
        guards = tuple(g for e in plan.get("guards") or () if (g := _guard_entry(e)) is not None)
        intent = detail.get("intent")
        found.append(
            SlotDelivery(
                _uuid(source_id),
                read_at,
                items,
                channels,
                str(receipt.get("experiment_group") or ""),
                tuple(_public_basis(d) for d in derived),
                version if isinstance(version, int) else None,
                guards,
                str(intent) if isinstance(intent, str) and intent else None,
            )
        )
    return sorted(found, key=lambda d: (d.read_at, str(d.source_id)))


def _slot_entry(entry: object, why: Mapping[str, Any] | None = None) -> SlotEntry | None:
    if not isinstance(entry, Mapping) or not entry.get("id"):
        return None
    try:
        return SlotEntry(
            item_id=str(entry["id"]),
            kind=str(entry["kind"]),
            channels=tuple(str(c) for c in entry["channels"]),
            position=int(entry["position"]),
            tokens=int(entry["tokens"]),
            why=_public_why(why) if why is not None else None,
            value_type=str(entry["value_type"]) if entry.get("value_type") else None,
        )
    except (TypeError, ValueError):
        return None


def _guard_entry(entry: object) -> GuardEntry | None:
    """A guard the read wrote (13, 6.1): the value it stated, by internal id, its type and the arm."""
    if not isinstance(entry, Mapping) or not entry.get("id") or not entry.get("value_type"):
        return None
    return GuardEntry(str(entry["id"]), str(entry["value_type"]), str(entry["arm"]))


def _reason(reasons: Mapping[str, Mapping[str, Any]], entry: object) -> Mapping[str, Any] | None:
    return reasons.get(str(entry.get("id"))) if isinstance(entry, Mapping) else None


def _public_why(why: Mapping[str, Any]) -> dict[str, Any]:
    """A slot's `why` as context-use shows it: ids are public there, as every item's (5.13)."""
    return {k: v for k, v in why.items() if k != "id"}


def _public_basis(derived: Mapping[str, Any]) -> dict[str, Any]:
    shown = dict(derived)
    if isinstance(shown.get("basis"), str):
        shown["basis"] = public_item_id(shown["basis"])
    return shown


def _json(value: object) -> Mapping[str, Any] | None:
    """Receipt details are strings (they are chained); the plan is canonical JSON inside one."""
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def reads_by_source(receipts: Iterable[Mapping[str, Any]]) -> list[tuple[UUID, datetime]]:
    return [
        (_uuid(r["source_id"]), r["read_at"])
        for r in receipts
        if r.get("source_id") is not None and r.get("kind") == ReceiptKind.READ.value
    ]


def manifest_from_json(
    manifest_hash: str, doc: Mapping[str, Any], fact_values: Mapping[str, str]
) -> Manifest:
    def item(entry: Mapping[str, Any]) -> ManifestItem:
        item_id = str(entry["id"])
        return ManifestItem(
            item_id=item_id,
            kind=str(entry.get("kind") or "unknown"),
            predicate=entry.get("predicate"),
            category=entry.get("category"),
            object_ref=object_ref_key(entry.get("object_ref")),
            object_id=str(entry["object_id"]) if entry.get("object_id") else None,
            value=fact_values.get(item_id),
            bound=tuple((str(b["kind"]), str(b["id"])) for b in entry.get("bound") or ()),
        )

    excluded = [e for e in doc.get("excluded", ()) if e.get("id")]
    return Manifest(
        manifest_hash=manifest_hash,
        view=str(doc.get("view") or "unknown"),
        items=tuple(item(e) for e in doc.get("items", ())),
        withheld=tuple(item(e) for e in excluded),
        trimmed=tuple(item(e) for e in excluded if e.get("reason") == UNUSED),
        tokens_saved=int((doc.get("trim") or {}).get("tokens_saved") or 0),
    )


def fact_ids(doc: Mapping[str, Any]) -> list[str]:
    return [str(e["id"]) for e in doc.get("items", ()) if e.get("kind") == "fact"]


def agent_answers(events: Sequence[EventRecord], stt_floor: float) -> tuple[AgentAnswer, ...]:
    """The agents' messages with text, above the STT floor, with the SDK's backing report when the turn
    carried one (13, 6.2): kinds, counts and short ids, never a value."""
    found = []
    for event_id, event in agent_turns(events, stt_floor).items():
        if event.kind is not EventKind.MESSAGE or not event.text:
            continue
        report = (event.content_meta or {}).get("backing")
        checked: int | None = None
        unbacked = 0
        violations: tuple[str, ...] = ()
        if isinstance(report, Mapping):
            try:
                checked = int(report.get("checked") or 0)
                unbacked = len(report.get("unbacked_values") or ())
                violations = tuple(str(g) for g in report.get("guard_violations") or ())
            except (TypeError, ValueError):
                checked, unbacked, violations = None, 0, ()
        found.append(
            AgentAnswer(
                event_id, event.source_id, event.occurred_at, event.text, checked, unbacked, violations
            )
        )
    return tuple(sorted(found, key=lambda a: (a.at, str(a.event_id))))


def agent_turns(events: Sequence[EventRecord], stt_floor: float) -> dict[UUID, EventRecord]:
    """Agent turns that may yield a question or a claim: below the STT floor they yield nothing (19.2)."""
    return {
        e.event_id: e
        for e in events
        if e.speaker in _AGENT_SPEAKERS and (e.stt_confidence is None or e.stt_confidence >= stt_floor)
    }


def context_stamps(events: Sequence[EventRecord]) -> tuple[ContextStamp, ...]:
    """Stamps the ingestion kept in `content_meta["context_stamp"]` on agent turns; unreadable ones are
    skipped, and the receipt's delivery time stands in for them."""
    found = []
    for event in events:
        stamp = (event.content_meta or {}).get("context_stamp")
        if event.speaker not in _AGENT_SPEAKERS or not isinstance(stamp, Mapping):
            continue
        try:
            injected_at = datetime.fromisoformat(str(stamp["injected_at"]))
        except (KeyError, ValueError):
            continue
        etag = stamp.get("etag")
        # A task view's etag may carry the agent notes' suffix (`<pack etag>-<notes>`): the pack is the part
        # the delivery's receipt names.
        found.append(ContextStamp(event.source_id, injected_at, str(etag).split("-", 1)[0] if etag else None))
    return tuple(found)


def first_agent_turns(events: Sequence[EventRecord]) -> dict[UUID, datetime]:
    first: dict[UUID, datetime] = {}
    for event in events:
        if event.kind is not EventKind.MESSAGE or event.speaker not in _AGENT_SPEAKERS:
            continue
        if event.source_id not in first or event.occurred_at < first[event.source_id]:
            first[event.source_id] = event.occurred_at
    return first


def questions_from(result: Mapping[str, Any], turns: Mapping[UUID, EventRecord]) -> tuple[AgentQuestion, ...]:
    found = []
    for entry in result.get("agent_questions") or ():
        turn = _turn(turns, entry.get("event_id"))
        kind = _question_type(entry.get("type"))
        if turn is None or kind is None:
            continue
        found.append(
            AgentQuestion(
                type=kind,
                event_id=turn.event_id,
                source_id=turn.source_id,
                at=turn.occurred_at,
                predicate=entry.get("predicate"),
                object_ref=object_ref_key(entry.get("object_ref")),
            )
        )
    return tuple(found)


def claims_from(result: Mapping[str, Any], turns: Mapping[UUID, EventRecord]) -> tuple[AgentClaim, ...]:
    found = []
    for entry in result.get("agent_claims") or ():
        turn = _turn(turns, entry.get("event_id"))
        if turn is None:
            continue
        found.append(
            AgentClaim(
                event_id=turn.event_id,
                source_id=turn.source_id,
                at=turn.occurred_at,
                predicate=entry.get("predicate"),
                value=None if entry.get("value") is None else str(entry["value"]),
                object_ref=object_ref_key(entry.get("object_ref")),
                contradicts=tuple(str(i) for i in entry.get("contradicts") or ()),
            )
        )
    return tuple(found)


def actions_in(
    actions: Iterable[AgentAction], *, since: datetime, until: datetime
) -> tuple[SessionAction, ...]:
    return tuple(
        SessionAction(
            action_id=a.action_id,
            source_id=a.source_id,
            at=a.occurred_at,
            object_id=str(a.object_id) if a.object_id else None,
            closes_item_id=str(a.closes_item_id) if a.closes_item_id else None,
        )
        for a in actions
        if since <= a.occurred_at <= until
    )


def handoffs_in(events: Sequence[EventRecord]) -> tuple[Handoff, ...]:
    return tuple(
        Handoff(
            at=e.occurred_at,
            from_source_id=e.source_id,
            target_source_id=_maybe_uuid(e.fields.get("target_source")),
        )
        for e in events
        if e.kind is EventKind.SYSTEM_EVENT and e.canonical_type == HANDOFF_EVENT
    )


def episode_category(result: Mapping[str, Any]) -> str | None:
    episode = result.get("episode") or {}
    category = episode.get("category") if isinstance(episode, Mapping) else None
    return str(category) if category else None


def _turn(turns: Mapping[UUID, EventRecord], event_id: object) -> EventRecord | None:
    key = _maybe_uuid(event_id)
    return turns.get(key) if key else None


def _question_type(value: object) -> QuestionType | None:
    try:
        return QuestionType(str(value))
    except ValueError:
        return None


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _maybe_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return _uuid(value)
    except ValueError:
        return None
