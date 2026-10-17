"""Derived fields at the read (24.2.7): a composite object's related objects, found
at its relation's path, read from their copies (a shared one from the working set's hot copy, with no
statement while warm) and aggregated for the reader. Nothing is stored: a piece that changes changes the
next read.

`related` names what the relations of a composite point to; a reverse relation (`match`) names nothing in the
composite, so `lookups` says which value of which field of the customers' objects finds its related objects,
and the read finds them by the keyed digest each write keeps (`matched`). `derive` computes the derived fields
from the copies the read found, and names the pieces that kept each one from `yes`. A related object's field
the reader may not read, by its access, makes the derived field that reads it withheld rather than let an
aggregate carry it; a source whose licence bars the reader does not feed it.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import jmespath

from niadra.app.typed_changes import names_read
from niadra.app.typed_state import (
    ReadRequest,
    TypedObject,
    barred_sources,
    evaluated,
    field_access,
    resolved_state,
)
from niadra.config.object_types import DerivedField, ObjectType, ObjectTypeRegistry, Relation
from niadra.contracts.state import DerivedState
from niadra.domain.state.derived import Piece, aggregate
from niadra.domain.state.expr import Environment, ExprError, evaluate, parse
from niadra.domain.state.logic import Logic
from niadra.domain.state.shared import shared_key

PIECES_MAX = 50
"""Related objects a relation reads for its derived fields; past it the rest count as not known and the field
says it is partial."""
PIECES_OUT_MAX = 10
"""The pieces a derived field names as out: the ones it fails for, or that are not known."""
EXACT = "exact"
"""The status of a piece that is the related object itself; any other (`missing_in_catalog`, `future`) is a
substitute, an observation that is not state, and counts as not known."""


@dataclass(frozen=True, slots=True)
class Related:
    """One related object a composite names, with the entry's status when its relation declares one."""

    type: str
    namespace: str
    external_id: str
    variant: str = ""
    status: Any = None

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.type, self.namespace, self.external_id, self.variant)

    @property
    def ref(self) -> str:
        base = f"{self.type}:{self.namespace}:{self.external_id}"
        return f"{base}#{self.variant}" if self.variant else base


@dataclass(frozen=True, slots=True)
class Lookup:
    """A reverse relation's search: the customers' objects of `type` whose `field` holds `value`."""

    type: str
    field: str
    value: str


@dataclass(frozen=True, slots=True)
class Derivation:
    fields: dict[str, DerivedState]
    withheld: frozenset[str] = frozenset()
    """Derived fields that read a related field the reader may not read."""


NOT_DERIVED = Derivation({})
"""What an object whose type derives nothing, or that is not known, gets."""


def related(
    declared: ObjectType, obj: TypedObject, names: Mapping[UUID, str], now: datetime
) -> dict[str, list[Related]]:
    """Per relation some derived field is computed over, the related objects its path finds in the object's
    state, up to one past `PIECES_MAX`; an entry its `required_field` marks optional is left out."""
    wanted = sorted({spec.over for spec in declared.derived_fields.values()})
    if not wanted:
        return {}
    state = resolved_state(obj.fields, declared, names, now)
    return {
        name: _entries(declared.relations[name], obj.key.namespace, state)[: PIECES_MAX + 1]
        for name in wanted
        if name in declared.relations and declared.relations[name].match is None
    }


def lookups(
    declared: ObjectType, obj: TypedObject, names: Mapping[UUID, str], now: datetime
) -> dict[str, list[Lookup]]:
    """Per reverse relation some derived field is computed over, the searches the values at its `via` make;
    a composite with no value there is left out, so its fields over that relation are not known."""
    wanted = sorted({spec.over for spec in declared.derived_fields.values()})
    reverse = {name: declared.relations[name] for name in wanted if name in declared.relations}
    reverse = {name: r for name, r in reverse.items() if r.match is not None}
    if not reverse:
        return {}
    state = resolved_state(obj.fields, declared, names, now)
    found = {
        name: [Lookup(r.type, r.match, v) for v in dict.fromkeys(_ids(_search(r.via, state)))]
        for name, r in reverse.items()
        if r.match is not None
    }
    return {name: searches for name, searches in found.items() if searches}


def named_by(relation: Relation, namespace: str, state: Mapping[str, Any]) -> list[Related]:
    """The related objects a relation's path names in a state; a reverse relation names none: its related
    objects hold the value (`match_values`)."""
    return [] if relation.match is not None else _entries(relation, namespace, state)


def match_values(relation: Relation, state: Mapping[str, Any]) -> list[str]:
    """The values a reverse relation finds its related objects by: theirs at its `match` field."""
    return [] if relation.match is None else list(dict.fromkeys(_ids(_search(relation.via, state))))


def matched(state: Mapping[str, Any], fields: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """(field, value) of each of `fields` the state holds as an id: what a reverse relation finds the object
    by."""
    return tuple(sorted({(name, value) for name in fields for value in _ids(state.get(name))}))


def derive(
    declared: ObjectType,
    registry: ObjectTypeRegistry,
    pieces: Mapping[str, Sequence[Related]],
    copies: Mapping[tuple[str, str, str, str], TypedObject],
    request: ReadRequest,
    *,
    names: Mapping[UUID, str],
    now: datetime,
    utc_offset_min: int = 0,
) -> Derivation:
    """The object's derived fields from its related objects' copies (a missing copy is a piece not known)."""
    found: dict[str, DerivedState] = {}
    withheld: set[str] = set()
    reader = request.reader.reading(request.purpose) if request.reader else None
    for name, spec in declared.derived_fields.items():
        relation = declared.relations[spec.over]
        target = registry.get(relation.type)
        refs = pieces.get(spec.over)
        if target is None or refs is None:
            continue  # a reverse relation with no value to find its related objects by: not known
        hidden = field_access(target, reader) if reader else {}
        if hidden.keys() & _reads(spec):
            withheld.add(name)
            continue
        barred = barred_sources(target, reader, request.commercial)
        # Past `PIECES_MAX` the first ones are still read and the rest count as not known, so an answer they
        # already decide (`any` with one that holds, `all` with one that fails) is given and said partial,
        # never thrown away.
        seen = [
            _piece(
                spec, relation, ref, copies.get(ref.key), target, barred, request, names, now, utc_offset_min
            )
            for ref in refs[:PIECES_MAX]
        ] + [Piece(Logic.UNOBSERVED)] * max(0, len(refs) - PIECES_MAX)
        result = aggregate(spec.agg, seen)
        found[name] = DerivedState(
            v=result.value,
            logic=result.logic.value,
            over=result.over,
            unknown=result.unknown,
            pieces_out=[] if result.logic is Logic.YES else _out(spec, refs, seen),
            partial=len(refs) > PIECES_MAX,
        )
    return Derivation(found, frozenset(withheld))


def private_derived(registry: ObjectTypeRegistry) -> dict[str, frozenset[str]]:
    """Per type, the derived fields that read a field of the related type no text for a model carries."""
    private = registry.private_fields()
    found: dict[str, frozenset[str]] = {}
    for declared in registry.types:
        names = set()
        for name, spec in declared.derived_fields.items():
            relation = declared.relations.get(spec.over)
            if relation is not None and private.get(relation.type, frozenset()) & _reads(spec):
                names.add(name)
        if names:
            found[declared.type] = frozenset(names)
    return found


def _piece(
    spec: DerivedField,
    relation: Relation,
    ref: Related,
    obj: TypedObject | None,
    target: ObjectType,
    barred: frozenset[str],
    request: ReadRequest,
    names: Mapping[UUID, str],
    now: datetime,
    utc_offset_min: int,
) -> Piece:
    if spec.agg == "count" and spec.where is None:
        return Piece(Logic.YES)  # counts what the relation names, held or not
    if obj is None or (relation.status_field is not None and ref.status not in (None, EXACT)):
        return Piece(Logic.UNOBSERVED)
    seen = evaluated(
        obj, target, names, now, utc_offset_min=utc_offset_min, purpose=request.purpose, barred=barred
    )
    holds = _holds(spec.where, seen.env) if spec.where else Logic.YES
    value = None
    if spec.field is not None:
        compared = seen.resolved.get(spec.field)
        value = compared.value if compared is not None and compared.logic is Logic.YES else None
    return Piece(holds, value)


def _out(spec: DerivedField, refs: Sequence[Related], seen: Sequence[Piece]) -> list[str]:
    """What kept a derived field from `yes`: for `all` and `any`, the pieces the condition does not hold
    for or that are not known; for `count`, `min` and `max`, the ones not known. In the relation's order, up
    to `PIECES_OUT_MAX`."""
    if spec.agg in ("all", "any"):
        out = [ref for ref, piece in zip(refs, seen, strict=True) if piece.holds is not Logic.YES]
    else:
        out = [ref for ref, piece in zip(refs, seen, strict=True) if not piece.holds.known]
    return [ref.ref for ref in out[:PIECES_OUT_MAX]]


def _holds(where: str, env: Environment) -> Logic:
    try:
        value = evaluate(parse(where), env)
    except ExprError:
        return Logic.UNOBSERVED
    if value.logic is Logic.YES and value.datum is True:
        return Logic.YES
    if value.logic is Logic.NO or value.datum is False:
        return Logic.NO
    return Logic.UNOBSERVED


def _reads(spec: DerivedField) -> set[str]:
    """The related type's fields a derived field reads."""
    return (names_read(spec.where) if spec.where else set()) | ({spec.field} if spec.field else set())


def _entries(relation: Relation, namespace: str, state: Mapping[str, Any]) -> list[Related]:
    """The related objects at the relation's path: for `list[*].leaf`, each entry of the list with its id at
    `leaf`, its status and whether it is required; otherwise the ids the path finds."""
    head, star, leaf = relation.via.rpartition("[*].")
    if not star:
        return [_ref(relation.type, namespace, value, None) for value in _ids(_search(relation.via, state))]
    entries = _search(f"{head}[*]", state)
    found: list[Related] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, Mapping):
            continue
        if relation.required_field is not None and entry.get(relation.required_field) is False:
            continue
        status = entry.get(relation.status_field) if relation.status_field else None
        found += [_ref(relation.type, namespace, value, status) for value in _ids(_search(leaf, entry))]
    return found


def _search(path: str, data: Mapping[str, Any]) -> Any:
    try:
        return jmespath.search(path, dict(data))
    except jmespath.exceptions.JMESPathError:
        return None


def _ids(found: Any) -> list[str]:
    items = found if isinstance(found, list) else [found]
    return [str(item) for item in items if isinstance(item, str | int) and not isinstance(item, bool)]


def _ref(type_: str, namespace: str, value: str, status: Any) -> Related:
    """A bare id is taken in the composite's own namespace; a `type:namespace:id` as it is."""
    key = shared_key(value) if value.count(":") >= 2 else None
    if key is not None:
        return Related(key.type, key.namespace, key.external_id, key.variant, status)
    return Related(type_, namespace, value, "", status)
