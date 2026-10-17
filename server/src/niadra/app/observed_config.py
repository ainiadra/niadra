"""The observed layer of a space's configuration: what its traffic shows, written by
the cell, under what the company declared.

`observe_config` runs for each space once a minute. It reads the objects that changed since its last run,
counts what their states show (`domain.objects.infer.seen`), computes the space's observed types and keeps
both in the cell's `observed_types` document of the space: the evidence, the types, their version and a note
for each change, never a value. When the types change, the cell's observed counter moves on, so every process
reads them within one refresh, and this process serves them at once. A process merges them under the declared
registry (`config.observed.with_observed`): a declaration always wins, and an observed type that does not hold
together with it is left out and noted.

Nothing here loosens privacy (P3): inference only adds types, fields, states, relations and due dates, a
field that ever looked personal or was free text stays undeclared and held (`domain.objects.undeclared`),
and inference stops at its limits (P5) with a note.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from niadra.config.models import SpaceConfig
from niadra.config.observed import observed_operation_names, observed_types, observed_words
from niadra.domain.common.errors import InvalidInput, NotFound, Unavailable
from niadra.domain.config import OBSERVED_DOC, OBSERVED_VERSION, StoredDocument
from niadra.domain.config.signing import content_hash
from niadra.domain.extract.observed import ObservedOperations
from niadra.domain.ingest.rules import CUSTOMER_FACING_SOURCES
from niadra.domain.objects.infer import (
    Shape,
    changes,
    empty,
    kind_of,
    observed_operations,
    one_type,
    seen,
    shared_wordable,
    word_basis,
    word_context,
    wordable,
)
from niadra.domain.objects.infer import observed_types as infer_types
from niadra.domain.objects.state import COORDINATION_NAMESPACE
from niadra.domain.objects.undeclared import held
from niadra.domain.objects.words import (
    CONTEXT_NAMES,
    CONTEXT_TYPES,
    NAMES_PER_CALL,
    REWORD_GROWTH,
    TYPE_CALLS_PER_RUN,
    WORDS_VERSION,
    Words,
)
from niadra.domain.records import BusinessObject
from niadra.domain.tasks import Task
from niadra.ports.data_issues import DataIssues
from niadra.ports.infra import Clock, ConfigProvider
from niadra.ports.proposals import ObservedRecords
from niadra.ports.uow import UnitOfWorkFactory

log = logging.getLogger(__name__)

BATCH = 500
"""Changed objects read per run: a busier space catches up over the next runs, a minute apart."""
WORDS_RETRY = timedelta(days=1)
LIMIT_NOTES = frozenset({"limit", "unsafe_names", "left_out"})
"""Notes that say inference did not do something: each is a data issue the Console lists, never only a line
in the document (memory never cuts)."""
REASON_NAMED = 8
"""Notes a record's reason names one by one; the rest are counted, and the record's body holds them whole."""
ID_CHARS = 128
"""Longer than this, a text names no object."""


@runtime_checkable
class ObservedSink(Protocol):
    """A configuration copy that serves a space's observed types the moment this process inferred them."""

    def observe(
        self,
        space_id: UUID,
        types: Sequence[Mapping[str, Any]],
        operations: Sequence[str] = (),
        words: Words | None = None,
    ) -> None: ...


class ObservedWordsWriter(Protocol):
    """Writes the space's words for field names and codes (`app.observed_words`)."""

    async def write(
        self,
        space_id: UUID,
        locale: str,
        names: Sequence[str],
        codes: Sequence[str],
        context: Sequence[Mapping[str, Any]] = (),
    ) -> Words: ...


class ObservedOperationsSource(Protocol):
    """The operations the space's agents record (`app.observed_operations`), without writing meanings."""

    async def snapshot(self, space_id: UUID) -> ObservedOperations: ...


class ObservedConfigService:
    def __init__(
        self,
        uow: UnitOfWorkFactory,
        config: ConfigProvider,
        clock: Clock,
        *,
        records: ObservedRecords | None = None,
        operations: ObservedOperationsSource | None = None,
        words: ObservedWordsWriter | None = None,
        issues: DataIssues | None = None,
    ) -> None:
        self._uow = uow
        self._issues = issues
        self._config = config
        self._clock = clock
        self._records = records
        self._operations = operations
        self._words = words

    async def observe(self, task: Task) -> None:
        space_id = task.space_id
        try:
            space = self._config.space(space_id)
        except NotFound:
            return
        async with self._uow.cell() as cell:
            stored = await cell.config.document(space_id, OBSERVED_DOC)
        document: dict[str, Any] = (
            dict(stored.document)
            if stored
            else {"version": 0, "evidence": empty(), "types": [], "unreported": []}
        )
        inferred = await self._inferred(space, document)
        if inferred is not None:
            document = inferred
        worded = await self._worded(space, document)
        if worded is not None:
            document = {**worded, "moved": True}
        if inferred is not None or worded is not None:
            await self._save(space_id, document)
        reported = await self._report(space_id, document)
        if reported != document.get("reported", {}):
            # The notes a record named leave the document: the record holds them now.
            told = int(reported.pop("notes", 0))
            left = list(document.get("unreported") or [])[told:]
            await self._save(space_id, {**document, "reported": reported, "unreported": left})

    async def _inferred(self, space: SpaceConfig, document: dict[str, Any]) -> dict[str, Any] | None:
        """The document after the objects that changed since the last run; None when none did."""
        space_id = space.space_id
        before: list[dict[str, Any]] = observed_types(document)
        evidence: dict[str, Any] = without_agents(space, document.get("evidence") or empty())
        async with self._uow(space_id) as uow:
            seq = int(evidence.get("seq", 0))
            if seq and seq > await uow.objects.last_revision():
                # The space's database was emptied (reset-spaces.sh) or restored from a dump, and its sequence
                # starts lower: read it from the start, or nothing new is observed until it catches up.
                evidence = {**evidence, "seq": 0}
            read = await uow.objects.changed_since(int(evidence.get("seq", 0)), limit=BATCH)
            if not read:
                return None
            changed = from_systems(space, read)
            if not changed and evidence == (document.get("evidence") or empty()):
                return {
                    **document,
                    "evidence": {**evidence, "seq": max(o.revision for o in read)},
                    "moved": False,
                }
            known = (
                {t.type for t in space.object_types.types}
                | set(evidence.get("types") or {})
                | {o.key.type for o in changed}
            )
            named = (
                await uow.objects.types_keyed_by(
                    [o.key.namespace for o in changed], sorted(known), _ids(changed)
                )
                if changed
                else {}
            )
        names = {source.source_id: source.name for source in space.sources}
        shapes = [
            Shape(
                type=o.key.type,
                external_id=o.key.external_id,
                state=o.state,
                as_of=o.as_of,
                owned=o.origin_handle_id is not None,
                system=names.get(o.source_id),
            )
            for o in changed
        ]
        evidence = seen(evidence, shapes, named)
        evidence["seq"] = max(o.revision for o in read)
        now = self._clock.now()
        types = infer_types(evidence, _declared(space, before), now)
        kept = _holding(space, before, types)
        notes = [
            {**note, "at": now.isoformat()}
            for note in [*changes(before, kept), *_left_out(types, kept), *_limits(document, evidence)]
        ]
        operations = observed_operations(evidence)
        known = set(observed_operation_names(document))
        notes += [
            {"kind": "operation", "operation": name, "at": now.isoformat()}
            for name in operations
            if name not in known
        ]
        await self._limited(space_id, [n for n in notes if n["kind"] in LIMIT_NOTES])
        moved = kept != before or operations != observed_operation_names(document)
        version = int(document.get("version", 0)) + (1 if moved else 0)
        if moved:
            log.info(
                "observed types changed",
                extra={"space_id": str(space_id), "version": version, "notes": len(notes)},
            )
        return {
            **document,
            "version": version,
            "evidence": evidence,
            "types": kept,
            "operations": operations,
            # Every note since the last record, none cut: the record names them, then they live in the record.
            "unreported": [*list(document.get("unreported") or []), *notes],
            "moved": moved,
        }

    async def _limited(self, space_id: UUID, notes: Sequence[Mapping[str, Any]]) -> None:
        """Each limit inference reached, type it left out or unsafe name it met: an `inference_limit` issue
        the company sees in the Console, counted per kind and type, never only a note in this document."""
        if self._issues is None or not notes:
            return
        async with self._uow(space_id) as uow:
            for note in notes:
                await self._issues.open(
                    uow,
                    space_id,
                    "inference_limit",
                    object_type=note.get("type"),
                    field=str(note.get("what") or note["kind"]),
                )

    async def _worded(self, space: SpaceConfig, document: dict[str, Any]) -> dict[str, Any] | None:
        """The document with the space's words for what its systems send that is still unworded; None when
        nothing new was worded (`app.observed_words`)."""
        if self._words is None:
            return None
        evidence = document.get("evidence") or empty()
        names, codes = wordable(evidence)
        stored: dict[str, Any] = dict(document.get("words") or {})
        fields: dict[str, str] = dict(stored.get("fields") or {})
        values: dict[str, str] = dict(stored.get("values") or {})
        # What each word was asked with: the version of the asking and the size of its record type then. A
        # word of an older version, or whose type has grown by `REWORD_GROWTH` since, is asked once more with
        # the context there is now; a word asked again for its type's growth is never asked for it again.
        basis = word_basis(evidence)
        marks: dict[str, list[Any]] = {k: list(v) for k, v in (stored.get("marks") or {}).items()}
        now = self._clock.now()
        # A name the model left unanswered is asked again a day later, never on every run.
        asked: dict[str, str] = {
            name: at
            for name, at in (stored.get("asked") or {}).items()
            if now - datetime.fromisoformat(at) < WORDS_RETRY
        }
        # A label a person declared stands; the one an observed type carries is its name made readable
        # (`domain.objects.infer`), which the space's word replaces. Counted as labelled, the fields the
        # inference declared were never worded: "Cnj", "Case id" on a pt-BR space (juridico-zero, 08/10/2026).
        observed = {t.get("type") for t in document.get("types") or []}
        labelled = {
            n
            for type_name, labels in space.object_types.field_labels().items()
            if type_name not in observed
            for n in labels
        }

        def due(key: str, worded: bool) -> bool:
            if not worded:
                return key not in asked
            mark = marks.get(key)
            # A word of the first asking carries no mark: it is asked once more at once, whenever it was
            # asked. "held" was worded "retido" at 01:08 from the names alone, and the asking with the record
            # types at 14:42 passed it over as asked that day (juridico-zero, 09/10/2026, final round).
            if mark is None:
                return True
            version, size, regrown = mark
            if version < WORDS_VERSION:
                return key not in asked
            return not regrown and basis.get(key, 0) >= size + REWORD_GROWTH

        asked_names = [n for n in names if n not in labelled and due(f"f:{n}", n in fields)][:NAMES_PER_CALL]
        asked_codes = [c for c in codes if due(f"v:{c}", c in values)][:NAMES_PER_CALL]
        stamp = now.isoformat()
        if asked_names or asked_codes:
            context = word_context(
                evidence, asked_names, asked_codes, types=CONTEXT_TYPES, per_type=CONTEXT_NAMES
            )
            found = await self._words.write(
                space.space_id, space.settings.locale, asked_names, asked_codes, context
            )
            worded_before = {f"f:{n}" for n in fields} | {f"v:{c}" for c in values}
            failed = not (found.fields or found.values)
            for key in [*(f"f:{n}" for n in asked_names), *(f"v:{c}" for c in asked_codes)]:
                before = marks.get(key)
                if failed and key in worded_before and (before is None or before[0] < WORDS_VERSION):
                    # Nothing came back (a spent budget, a model error): a word of an older asking waits a
                    # day and is asked again, never taken as asked at this version.
                    marks[key] = [1, basis.get(key, 0), False]
                    continue
                # Asked again at this version for its type's growth: marked, so it is the last time.
                regrown = before is not None and before[0] >= WORDS_VERSION
                marks[key] = [WORDS_VERSION, basis.get(key, 0), regrown]
            fields, values = {**fields, **found.fields}, {**values, **found.values}
            asked = {
                **asked,
                **{f"f:{n}": stamp for n in asked_names},
                **{f"v:{c}": stamp for c in asked_codes},
            }
        types, typed = await self._typed_words(space, evidence, stored, asked, labelled, stamp)
        if not (asked_names or asked_codes or typed):
            return None
        said: dict[str, Any] = {
            "fields": fields,
            "values": values,
            "asked": {**asked, **typed},
            "marks": marks,
        }
        if types:
            said["types"] = types
        return {**document, "words": said}

    async def _typed_words(
        self,
        space: SpaceConfig,
        evidence: Mapping[str, Any],
        stored: Mapping[str, Any],
        asked: Mapping[str, str],
        labelled: set[str],
        stamp: str,
    ) -> tuple[dict[str, dict[str, dict[str, str]]], dict[str, str]]:
        """Each record type's own words for the names and codes it shares with another type, asked with that
        type alone as context (`domain.objects.infer.shared_wordable`), at most `TYPE_CALLS_PER_RUN` types a
        run: the space's one word for "held" said a hearing was "retido" when it took place. Returns the
        types' words and the names asked now, keyed `t:<type>:f:<name>` and `t:<type>:v:<code>`. A name the
        model left unanswered is asked again a day later, as the space's are."""
        assert self._words is not None
        types: dict[str, dict[str, dict[str, str]]] = {
            str(name): {"fields": dict(own.get("fields") or {}), "values": dict(own.get("values") or {})}
            for name, own in (stored.get("types") or {}).items()
            if isinstance(own, Mapping)
        }
        typed: dict[str, str] = {}
        calls = 0
        for type_name, (names, codes) in shared_wordable(evidence).items():
            if calls >= TYPE_CALLS_PER_RUN:
                break
            own = types.get(type_name) or {"fields": {}, "values": {}}
            due_names = [
                n
                for n in names
                if n not in labelled and n not in own["fields"] and f"t:{type_name}:f:{n}" not in asked
            ][:NAMES_PER_CALL]
            due_codes = [c for c in codes if c not in own["values"] and f"t:{type_name}:v:{c}" not in asked][
                :NAMES_PER_CALL
            ]
            if not (due_names or due_codes):
                continue
            context = word_context(
                one_type(evidence, type_name), due_names, due_codes, types=1, per_type=CONTEXT_NAMES
            )
            found = await self._words.write(
                space.space_id, space.settings.locale, due_names, due_codes, context
            )
            calls += 1
            own["fields"].update(found.fields)
            own["values"].update(found.values)
            if own["fields"] or own["values"]:
                types[type_name] = own
            typed |= {f"t:{type_name}:f:{n}": stamp for n in due_names}
            typed |= {f"t:{type_name}:v:{c}": stamp for c in due_codes}
        return types, typed

    async def _save(self, space_id: UUID, document: dict[str, Any]) -> None:
        moved = bool(document.pop("moved", False))
        version = int(document.get("version", 0))
        async with self._uow.cell() as cell:
            await cell.config.save_document(
                StoredDocument(space_id, OBSERVED_DOC, content_hash(document), version, document)
            )
            if moved:
                await cell.config.set_state(OBSERVED_VERSION, int(self._clock.now().timestamp() * 1_000_000))
        if moved and isinstance(self._config, ObservedSink):
            self._config.observe(
                space_id,
                observed_types(document),
                observed_operation_names(document),
                observed_words(document),
            )

    async def _report(self, space_id: UUID, document: Mapping[str, Any]) -> dict[str, Any]:
        """Records in the control plane what changed since the last record, as the Console shows it: the
        observed types, and the operations the agents record with their meanings (WP4). A control plane out
        of reach leaves it for the next run."""
        reported: dict[str, Any] = dict(document.get("reported") or {})
        if self._records is None:
            return reported
        version = int(document.get("version", 0))
        if version and reported.get("object-types") != version:
            reason = _reason(document.get("unreported") or [], version)
            if await self._record(space_id, "object-types", {"types": observed_types(document)}, reason):
                reported["object-types"] = version
                reported["notes"] = len(document.get("unreported") or [])
        recorded = (
            (await self._operations.snapshot(space_id)).operations if self._operations is not None else ()
        )
        named = {o.name for o in recorded}
        confirmed = [name for name in observed_operation_names(document) if name not in named]
        if recorded or confirmed:
            body = {
                "operations": [
                    *({"name": o.name, "meaning": o.meaning} for o in recorded),
                    *({"name": name, "meaning": None} for name in confirmed),
                ]
            }
            digest = content_hash(body)
            if reported.get("schemas") != digest:
                said = [*(o.name for o in recorded), *confirmed][:10]
                reason = f"operations the agents record and the systems confirm: {', '.join(said)}"
                if await self._record(space_id, "schemas", body, reason):
                    reported["schemas"] = digest
        return reported

    async def _record(self, space_id: UUID, name: str, document: Any, reason: str) -> bool:
        assert self._records is not None
        try:
            await self._records.record(space_id, name, document, reason)
        except Unavailable:
            return False
        except InvalidInput:
            log.exception("observed record refused", extra={"space_id": str(space_id), "type": name})
            return False
        return True


def from_systems(space: SpaceConfig, objects: Sequence[BusinessObject]) -> list[BusinessObject]:
    """The objects a type is inferred from: those a company system or an internal agent wrote. Coordination's
    own records (a handoff, a commitment) are Niadra's, never a company system's type, and an object a
    customer-facing agent made is what that agent named, not what a system sent: inferring a type from either
    makes it a system's (`mirror_of`), and then an agent's action naming the next one is refused as unknown
    (e2e, 07/10/2026: coordination's `handoff` observed as a `whatsapp` system's type, and a handoff's
    outcome refused)."""
    audiences = {source.source_id: source.audience for source in space.sources}
    return [
        o
        for o in objects
        if o.key.namespace != COORDINATION_NAMESPACE
        and audiences.get(o.source_id) not in CUSTOMER_FACING_SOURCES
    ]


def without_agents(space: SpaceConfig, evidence: Mapping[str, Any]) -> dict[str, Any]:
    """The evidence without the types counted from a customer-facing source's objects, which `from_systems`
    no longer counts: a space that inferred one before keeps it no longer."""
    agents = {source.name for source in space.sources if source.audience in CUSTOMER_FACING_SOURCES}
    types = evidence.get("types") or {}
    kept = {name: entry for name, entry in types.items() if entry.get("system") not in agents}
    return {**evidence, "types": kept} if len(kept) != len(types) else dict(evidence)


def _ids(changed: Sequence[BusinessObject]) -> list[str]:
    """The text values of the changed objects that may be another object's key: never a held value."""
    ids: set[str] = set()
    for o in changed:
        for name, value in o.state.items():
            if kind_of(value) == "string" and len(value) <= ID_CHARS and held(name, value) is None:
                ids.add(value)
    return sorted(ids)


def _declared(space: SpaceConfig, before: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    """The types the company declared: the served ones but the ones the last run observed."""
    observed = {t["type"] for t in before}
    return frozenset(t.type for t in space.object_types.types if t.type not in observed)


def _holding(
    space: SpaceConfig, before: Sequence[Mapping[str, Any]], types: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The observed types that hold together with the declared registry: what `under` would leave out is not
    kept, so the document says what the space serves."""
    declared = _declared(space, before)
    registry = space.object_types.model_copy(
        update={"types": [t for t in space.object_types.types if t.type in declared]}
    )
    served = {t.type for t in registry.under(types).types}
    return [doc for doc in types if doc["type"] in served]


def _reason(notes: Sequence[Mapping[str, Any]], version: int) -> str:
    """What the record says it changed, by kind and name, never a value: the first `REASON_NAMED` notes, and
    how many more there are (the record's body holds every type, field, state and relation)."""
    said = [
        f"{n['kind']} {n.get('type', '')}{'.' + str(n['field']) if n.get('field') else ''}".strip()
        for n in list(notes)[:REASON_NAMED]
    ]
    more = len(notes) - len(said)
    text = "; ".join(said) or "no change of shape"
    said_more = f"; and {more} more" if more > 0 else ""
    return f"observed from the traffic, version {version}: {text}{said_more}"


def _left_out(types: Sequence[Mapping[str, Any]], kept: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    held_back = {t["type"] for t in kept}
    return [{"kind": "left_out", "type": t["type"]} for t in types if t["type"] not in held_back]


def _limits(document: Mapping[str, Any], evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A note when inference reached a limit or met an unsafe name it had not met before (P5, P6)."""
    old = (document.get("evidence") or {}).get("limited") or {}
    notes = [
        {"kind": "limit", "what": what, "count": count}
        for what, count in (evidence.get("limited") or {}).items()
        if count and not old.get(what)
    ]
    old_types = (document.get("evidence") or {}).get("types") or {}
    for name, entry in (evidence.get("types") or {}).items():
        was = old_types.get(name) or {}
        if entry.get("limited") and not was.get("limited"):
            notes.append(
                {"kind": "limit", "what": "fields_or_states", "type": name, "count": entry["limited"]}
            )
        if entry.get("unsafe") and not was.get("unsafe"):
            notes.append({"kind": "unsafe_names", "type": name, "count": entry["unsafe"]})
    return notes
