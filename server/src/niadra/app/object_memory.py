"""Memory bound to an object, across its participants.

Rows stay under the handle of who said them (L6). A compilation reads, for every open object of the subject
it compiles (and every one final within the window), the rows about them under the handles of each object's
owner and participants: four statements in the worker's transaction (the participants, the facts, the items,
the signals that hold the instructions), whatever the number of objects, below the runaway guards of
`domain.memory.bound`; what a guard leaves out is counted. A row about an object that crosses to other
participants asks for their packs again when it is written (`participant_profiles`), so a hot read never
asks the database for it.
"""

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from niadra.app.typed_state import Attribution, lifecycle_stage
from niadra.config.models import SpaceConfig
from niadra.contracts.state import ObjectMemoryOut, SaidAbout
from niadra.domain.compile.model import MemorySnapshot
from niadra.domain.extract.prompt import KnownObject
from niadra.domain.memory.bound import (
    FINAL_WINDOW,
    MAX_HANDLES,
    MAX_ROWS,
    ROWS_CEILING,
    BoundRow,
    ObjectMemory,
    kept,
)
from niadra.domain.memory.items import KEPT_WINDOW, noted_at
from niadra.domain.objects.written import distinctive, normalized
from niadra.domain.records import BusinessObject, Fact, ObjectKey, OpenItem
from niadra.domain.state.lifecycle import EXPIRED_WITHOUT_OUTCOME
from niadra.domain.vocabulary import MemoryAudience, OpenItemStatus, Speaker
from niadra.ports.signals import HeldInstruction, ObjectInstructions
from niadra.ports.uow import UnitOfWork


def ref_of(key: ObjectKey) -> str:
    return f"{key.type}:{key.namespace}:{key.external_id}"


def final_at(obj: BusinessObject, config: SpaceConfig) -> datetime | None:
    """When the object reached a final state of its type's lifecycle, None while it is open or untyped."""
    declared = config.object_types.get(obj.key.type)
    if declared is None or declared.lifecycle is None:
        return None
    names = {source.source_id: source.name for source in config.sources}
    stage = lifecycle_stage(obj.fields, declared, Attribution(declared, names))
    outcome = declared.lifecycle.outcome
    if stage is None:
        return None
    if stage.state == EXPIRED_WITHOUT_OUTCOME or (outcome is not None and stage.state in outcome.final):
        return stage.at
    return None


def known_objects(objects: Sequence[BusinessObject], config: SpaceConfig) -> tuple[KnownObject, ...]:
    """The objects a customer is tied to, as object resolution reads them (2.5), newest first, each with the
    identifiers a conversation may write (`ObjectType.written_keys`): any value of a field `key.match` shapes,
    a value of any other only when it is specific enough (`domain.objects.written.distinctive`). A value two
    of these objects of one type hold names neither: the customer's own objects, every one the caller read
    (up to `domain.memory.bound.OBJECTS_CEILING`), are the whole search. Their state is sealed, so no index
    of the space's values could serve it; the customer's objects are read by the index of who they involve."""
    newest = sorted(objects, key=lambda o: (o.as_of, o.object_id), reverse=True)
    found: list[tuple[BusinessObject, tuple[tuple[str, str], ...]]] = []
    for obj in newest:
        declared = config.object_types.get(obj.key.type)
        shaped = set(declared.key.match) if declared is not None and declared.key else set()
        natural = set(declared.key.natural) if declared is not None and declared.key else set()
        keys = tuple(
            (name, str(value))
            for name in (declared.written_keys() if declared is not None else ())
            if isinstance(value := obj.state.get(name), str | int)
            and str(value)
            and (name in shaped or distinctive(str(value), natural=name in natural))
        )
        found.append((obj, keys))
    held = Counter(
        pair for obj, keys in found for pair in {(obj.key.type, normalized(value)) for _, value in keys}
    )
    return tuple(
        KnownObject(
            obj.key,
            obj.as_of,
            final_at(obj, config) is None,
            tuple((name, value) for name, value in keys if held[(obj.key.type, normalized(value))] == 1),
        )
        for obj, keys in found
    )


async def gather(
    uow: UnitOfWork,
    config: SpaceConfig,
    objects: Sequence[BusinessObject],
    instructions: ObjectInstructions,
    *,
    focus: Collection[UUID] = (),
    focus_types: Collection[str] = (),
    now: datetime | None = None,
) -> tuple[ObjectMemory, ...]:
    """What was said about every open object of `objects` (and each one final for less than the window), by
    its owner and participants, newest first: every audience, for every reader; a reader's own compilation
    keeps what it may read (`domain.memory.bound.for_reader`). Never the newest few objects: the 20 newest
    lost every case past a client's fifth. An object in `focus`, or of a type in
    `focus_types`, is marked so its instructions enter the constraints block. With `now`, the items kept
    within `KEPT_WINDOW` before it come too, said as kept."""
    finals = {o.object_id: final_at(o, config) for o in objects}
    picked = [
        o
        for o in sorted(objects, key=lambda o: (o.as_of, o.object_id), reverse=True)
        if (ended := finals[o.object_id]) is None or now is None or now - ended <= FINAL_WINDOW
    ]
    if not picked:
        return ()
    # One more than the guard tells an object that has more participants than it gathers.
    taking_part = await uow.objects.participants_of([o.object_id for o in picked], per_object=MAX_HANDLES + 1)
    roles: dict[UUID, dict[UUID, str]] = {}
    unread: dict[UUID, int] = {}
    for obj in picked:
        mine: dict[UUID, str] = {}
        if obj.origin_handle_id is not None:
            mine[obj.origin_handle_id] = "owner"
        parts = taking_part.get(obj.object_id, ())
        for handle_id, role in parts:
            if len(mine) >= MAX_HANDLES and handle_id not in mine:
                break
            if mine.get(handle_id) in (None, "participant"):
                mine[handle_id] = role
        if len({h for h, _ in parts} - set(mine)):
            unread[obj.object_id] = await uow.objects.participant_count(obj.object_id) - len(mine)
        roles[obj.object_id] = mine
    handles = sorted({h for mine in roles.values() for h in mine}, key=str)
    keys = [o.key for o in picked]
    facts = await uow.memory.object_facts(keys, handles, per_object=MAX_ROWS)
    items = await uow.memory.object_items(
        keys, handles, per_object=ROWS_CEILING, kept_since=now - KEPT_WINDOW if now is not None else None
    )
    held = await instructions.held(uow, config.space_id, handles, [ref_of(k) for k in keys])
    observes = {p.name: p.observes for p in config.extraction.predicates if p.observes}
    out = []
    for obj in picked:
        mine = roles[obj.object_id]
        rows = [
            *(
                _observation(f, mine, obj, observes)
                for f in facts
                if f.object_ref == obj.key and f.origin_handle_id in mine
            ),
            *(_item(i, mine) for i in items if i.object == obj.key and i.origin_handle_id in mine),
            *(_instruction(h, mine) for h in held if h.object == ref_of(obj.key) and h.handle_id in mine),
        ]
        if not rows:
            continue
        out.append(
            ObjectMemory(
                key=obj.key,
                object_id=obj.object_id,
                rows=kept(rows),
                focus=obj.object_id in focus or obj.key.type in focus_types,
                final_at=finals.get(obj.object_id),
                handles_unread=max(unread.get(obj.object_id, 0), 0),
            )
        )
    return tuple(out)


async def account_instructions(
    uow: UnitOfWork, config: SpaceConfig, snapshot: MemorySnapshot, instructions: ObjectInstructions
) -> tuple[BoundRow, ...]:
    """What an organization's contacts (and the organization itself) said must or must not be done with it,
    naming no object: every reader of the organization gets them, as an object's participants do. One
    statement."""
    # Every linked person's words, not only the contacts shown in detail (`instruction_speakers`).
    roles = dict(snapshot.instruction_speakers) | {h: c.role for c in snapshot.contacts for h in c.handle_ids}
    handles = sorted({*snapshot.profile.handle_ids, *roles}, key=str)
    abouts = [str(h) for h in snapshot.profile.handle_ids]
    held = await instructions.held(uow, config.space_id, handles, (), abouts)
    rows = [replace(_instruction(h, roles), role=roles.get(h.handle_id, "owner")) for h in held if h.about]
    return kept(rows)


async def take_part(uow: UnitOfWork, speakers: Mapping[UUID, str], keys: Sequence[ObjectKey]) -> None:
    """Who said something about an object in a conversation takes part in it from then on, as an event that
    names them makes them a participant: their packs show its memory, theirs reach its
    other participants. Only objects the company's systems already sent; a conversation never creates one."""
    if not speakers or not keys:
        return
    for obj in await uow.objects.get_objects(list(dict.fromkeys(keys))):
        await uow.objects.add_participants(obj.object_id, sorted(speakers.items(), key=lambda p: str(p[0])))


async def participant_profiles(uow: UnitOfWork, keys: Sequence[ObjectKey]) -> list[UUID]:
    """The profiles of the owners and participants of the objects (at most `MAX_HANDLES` handles of each):
    whose packs say what was just written about the objects, so their compilations come again (2.10)."""
    if not keys:
        return []
    objects = await uow.objects.get_objects(list(dict.fromkeys(keys)))
    if not objects:
        return []
    taking_part = await uow.objects.participants_of([o.object_id for o in objects], per_object=MAX_HANDLES)
    handles = {o.origin_handle_id for o in objects if o.origin_handle_id is not None}
    handles |= {h for pairs in taking_part.values() for h, _ in pairs}
    memberships = await uow.identity.memberships(sorted(handles, key=str))
    return sorted(set(memberships.values()), key=str)


def _observation(
    fact: Fact, roles: Mapping[UUID, str], obj: BusinessObject, observes: Mapping[str, str]
) -> BoundRow:
    field = observes.get(fact.predicate)
    recorded = obj.state.get(field) if field else None
    return BoundRow(
        kind="observation",
        row_id=str(fact.fact_id),
        text=fact.value,
        origin_handle_id=fact.origin_handle_id,
        said_at=min(fact.last_confirmed_at, fact.created_at),
        audience=fact.audience or MemoryAudience.INTERNAL,
        speaker=fact.speaker,
        role=roles.get(fact.origin_handle_id),
        category=fact.category,
        sensitivity=fact.sensitivity,
        predicate=fact.predicate,
        observes=field,
        recorded=recorded if isinstance(recorded, str | int | float | bool) else None,
        recorded_at=obj.as_of if recorded is not None else None,
    )


def _item(item: OpenItem, roles: Mapping[UUID, str]) -> BoundRow:
    return BoundRow(
        kind="item",
        row_id=str(item.item_id),
        text=item.description,
        origin_handle_id=item.origin_handle_id,
        said_at=noted_at(item),
        audience=item.audience or MemoryAudience.SHARED,
        speaker=Speaker(item.origin["speaker"]) if item.origin.get("speaker") else None,
        role=roles.get(item.origin_handle_id),
        item_kind=item.kind,
        due_at=item.due_at,
        overdue=item.status is OpenItemStatus.OVERDUE,
        owner=item.owner,
        kept_at=item.closed_at if item.status is OpenItemStatus.RESOLVED else None,
    )


def _instruction(held: HeldInstruction, roles: Mapping[UUID, str]) -> BoundRow:
    return BoundRow(
        kind="instruction",
        row_id=held.key,
        text=held.text,
        origin_handle_id=held.handle_id,
        said_at=held.said_at,
        audience=MemoryAudience(held.audience) if held.audience else MemoryAudience.SHARED,
        speaker=Speaker(held.speaker) if held.speaker else None,
        role=roles.get(held.handle_id),
        strength=held.strength,
        attr=held.attr,
        expires_at=held.expires_at,
        only_with=held.only_with,
        contact=held.contact,
    )


def memory_out(memory: ObjectMemory, now: datetime, *, console: bool = False) -> ObjectMemoryOut:
    """An object's memory as the state view and the Console serve it: in force, newest first."""

    def out(row: BoundRow) -> SaidAbout:
        return SaidAbout(
            kind=row.kind,
            id=row.row_id,
            text=row.text,
            said_at=row.said_at,
            audience=row.audience.value,
            role=row.role,
            by=str(row.origin_handle_id) if console else None,
            predicate=row.predicate,
            item_kind=row.item_kind.value if row.item_kind else None,
            due_at=row.due_at,
            overdue=row.overdue,
            strength="must_not" if row.strength == "must_not" else "must" if row.strength else None,
            attr=row.attr,
            expires_at=row.expires_at,
            observes=row.observes,
            recorded=row.recorded,
            diverges=row.diverges,
        )

    return ObjectMemoryOut(
        said=[out(r) for r in memory.of("observation")],
        instructions=[out(r) for r in memory.instructions(now)],
        items=[out(r) for r in memory.open_items()],
    )
