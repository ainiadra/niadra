"""Folding interactions into a subject's compact signals (24.3.2): the same function derives the stored body
(`derive_signals`) and the view a read builds from that body and the session's overlay, so a pending
interaction reads the same before and after it is derived.

What each kind adds (round 5 of the retail annex, section A):

- `presented`: exposure of each value an item showed, by position, for the items that were visible (the
  first `visible_k`, or up to a later `seen`; all of them, by position, when neither is known);
- `engaged`: 0.3 for a click, a detail or a mention, 0.6 for a compare or a share, 1.5 for an item added to a
  cart; a repeat on the same item within 10 minutes counts once;
- `feedback`: +1 or -1 on the values of the item (only the fields it names, when it names them); a negative
  one also excludes the item;
- `preference`: kept as said; a persistent `prefer` or `avoid` adds +2 or -2 to each of its values;
- `attribute`: kept as said, or as an outcome derived it (`acquired`, `kept`, `returned_for_size`), each
  apart from what was said; `watch` and `unwatch`: the interest the person asked to be told about.

An object whose type holds a sensitive field adds nothing but its count, and a field that is sensitive or
personal data never feeds affinity. Purchases, returns and "kept it" come from lifecycle outcomes, never
from an agent, so nothing here reads them. An inference the person deleted (`suppress`) leaves the body,
and no evidence from up to that moment feeds it again: only what happens after it can infer it anew
(24.5.3).
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from niadra.domain.signals.affinity import add_signal, age_days, decay, position_weight, scale
from niadra.domain.signals.body import (
    MAX_EXPOSURE_ITEMS,
    Body,
    Exposure,
    Interest,
    Presented,
    Stated,
    Trait,
    Value,
)

ENGAGED = {"click": 0.3, "detail": 0.3, "mention": 0.3, "other": 0.3}
ENGAGED |= {"compare": 0.6, "share": 0.6, "add_to_cart": 1.5}
FEEDBACK = 1.0
STATED_PERSISTENT = 2.0
TOP_POSITIONS = 3
REPEAT_WINDOW = timedelta(minutes=10)
SESSION_TTL = timedelta(hours=24)
"""How long a preference said for the session holds without the session saying it again."""
PRESENTED_FOR = timedelta(days=30)
INTEREST_FOR = timedelta(days=395)
MAX_TEXT = 256
FAINT = 0.01
"""Accumulators decayed below this leave the body."""


@dataclass(frozen=True, slots=True)
class Quotable:
    """A field an item shows as a number (`money`, `percent`, `number`), as a claim check reads it."""

    role: str | None
    """The field's role, for claims."""
    claim_max_age_ms: float | None
    """How long after it was shown a value may still be claimed; None when the type allows no claim of it."""


@dataclass(frozen=True, slots=True)
class Catalog:
    """What the space's object types say about their fields, for signals."""

    families: Mapping[str, str] = field(default_factory=dict)
    """`type.field` -> its attribute family, when the type declares one."""
    hidden: frozenset[str] = frozenset()
    """`type.field` that is sensitive or personal data: it never feeds affinity nor stays in a body."""
    sensitive_types: frozenset[str] = frozenset()
    """Types with a sensitive field: their objects are counted and nothing else."""
    tracked: Mapping[str, frozenset[str]] = field(default_factory=dict)
    """Type -> the fields it tracks (`track_changes`), whose shown values an interest keeps."""
    quotable: Mapping[str, Quotable] = field(default_factory=dict)
    """`type.field` -> how a claim reads the number an item shows in it."""

    def family(self, attr: str) -> str:
        return self.families.get(attr) or attr.split(".", 1)[-1]

    def sensitive(self, ref: str) -> bool:
        return ref.split(":", 1)[0] in self.sensitive_types


@dataclass(frozen=True, slots=True)
class Entry:
    """One interaction to fold: from a turn record (`t:<turn>:<index>`) or a batch item (`b:<key>`)."""

    key: str
    at: datetime
    interaction: Mapping[str, Any]
    """As the interaction spec writes it: `kind`, `for` and the kind's fields."""
    session: str | None = None
    """A keyed digest of the conversation, when there is one."""
    turn_id: str | None = None
    event_key: str | None = None
    agent: str | None = None
    """The agent whose turn or item carried it, for attribution (24.7)."""
    source: str | None = None
    """The source whose turn or batch carried it: the segment of its priors, when the space asks for one."""

    @property
    def kind(self) -> str:
        return str(self.interaction["kind"])

    @property
    def for_(self) -> str:
        return str(self.interaction.get("for") or "self")

    def session_key(self) -> str:
        """The session of the 30% cap: the conversation, or else the day."""
        return self.session or f"day:{self.at.date().isoformat()}"


def value_token(attr: str, value: str) -> str:
    """What the body calls an inference drawn from a field's value: its affinity, or the size it gives."""
    return f"v|{attr}|{value}"


def interest_token(ref: str) -> str:
    return f"i|{ref}"


def stated_token(key: str) -> str:
    """What the body calls an instruction about an object the person said: a correction withdraws it by this
    name."""
    return f"s|{key}"


def suppress(body: Body, token: str, until: datetime) -> Body:
    """The inference `token` deleted: its accumulators or its interest leave, and evidence up to `until`
    never feeds it again."""
    kind, _, rest = token.partition("|")
    if kind == "v":
        attr, _, value = rest.partition("|")
        body.values.pop((attr, value), None)
    elif kind == "i":
        body.interests.pop(rest, None)
    elif kind == "s":
        body.stated = [s for s in body.stated if s.key != rest]
    before = body.suppressed.get(token)
    body.suppressed[token] = max(until, before) if before else until
    return body


def _held_back(body: Body, token: str, at: datetime) -> bool:
    until = body.suppressed.get(token)
    return until is not None and at <= until


def decayed(body: Body, now: datetime, catalog: Catalog) -> Body:
    """The body with every accumulator decayed from its `at` to `now`."""
    if now <= body.at:
        return body
    elapsed = age_days(body.at, now)
    for v in body.values.values():
        scale(v, decay(elapsed, catalog.family(v.attr)))
    body.at = now
    return body


def fold(body: Body | None, entries: Iterable[Entry], now: datetime, catalog: Catalog) -> Body:
    """`body` with `entries` added, as of `now`. Entries are folded in the order they happened."""
    out = decayed(body, now, catalog) if body is not None else Body(at=now)
    for entry in sorted(entries, key=lambda e: (e.at, e.key)):
        _FOLD[entry.kind](out, entry, now, catalog)
        if out.last_activity_at is None or entry.at > out.last_activity_at:
            out.last_activity_at = entry.at
    return tidy(out, now)


def merge(bodies: Sequence[Body], now: datetime, catalog: Catalog) -> Body | None:
    """One subject's bodies across their handles, as one: the accumulators add up."""
    if not bodies:
        return None
    if len(bodies) == 1:
        return decayed(bodies[0], now, catalog)
    out = Body(at=now)
    for body in bodies:
        body = decayed(body, now, catalog)
        for key, v in body.values.items():
            mine = out.values.setdefault(key, Value(v.attr, v.value))
            mine.e += v.e
            mine.e_top += v.e_top
            for session, amount in v.sessions:
                add_signal(mine, session, amount, 0.0)
            mine.s += v.s - sum(c for _, c in v.sessions)
            mine.n += v.n
            mine.seen_at = max(filter(None, (mine.seen_at, v.seen_at)), default=None)
        known = {s.key for s in out.stated}
        out.stated += [s for s in body.stated if s.key not in known]
        out.traits += body.traits
        for ref, interest in body.interests.items():
            if ref not in out.interests or interest.at > out.interests[ref].at:
                out.interests[ref] = interest
        for ref, p in body.presented.items():
            before = out.presented.get(ref)
            out.presented[ref] = Presented(
                ref, p.times + (before.times if before else 0), max(p.at, before.at) if before else p.at
            )
        for ref, at in body.excluded.items():
            out.excluded[ref] = max(at, out.excluded.get(ref, at))
        for token, until in body.suppressed.items():
            out.suppressed[token] = max(until, out.suppressed.get(token, until))
        out.exposures += body.exposures
        if body.last_activity_at and (
            out.last_activity_at is None or body.last_activity_at > out.last_activity_at
        ):
            out.last_activity_at = body.last_activity_at
    out.traits = _latest_traits(out.traits)
    return out


def tidy(body: Body, now: datetime) -> Body:
    """What no longer holds leaves: faded values, preferences past their expiry or their session's life,
    exposures past their time. What the person turned down stays until they take it back (positive feedback)
    or the subject is erased: a refusal is something the person said, never derived, so it never expires."""
    body.values = {k: v for k, v in body.values.items() if max(abs(v.e), abs(v.s), abs(v.e_top)) >= FAINT}
    body.stated = [s for s in body.stated if holds(s, now, None, any_session=True)]
    body.presented = {r: p for r, p in body.presented.items() if now - p.at <= PRESENTED_FOR}
    body.interests = {
        r: i
        for r, i in body.interests.items()
        if now - i.at <= INTEREST_FOR and (i.until is None or i.until > now)
    }
    return body


def holds(
    s: Stated,
    now: datetime,
    session: str | None,
    *,
    any_session: bool = False,
    objects: Collection[str] | None = None,
    abouts: Collection[str] | None = None,
) -> bool:
    """Whether a said preference is still in force for a read in `session`: a turn's never outlives the turn
    on the server, a session's holds in its conversation for a day at most, a persistent one until it
    expires. An instruction about an object (`scope: object`) holds only for a read whose open set or focus
    holds the object (`objects`, refs), and is kept in the body until it expires; the object's own
    lifecycle decides the rest where the object is read."""
    if s.expires_at is not None and s.expires_at <= now:
        return False
    if s.scope == "persistent":
        return True
    if s.scope == "session":
        return now - s.said_at <= SESSION_TTL and (
            any_session or (session is not None and s.session == session)
        )
    if s.scope == "object":
        return any_session if objects is None else s.object in objects
    if s.scope == "about":
        return any_session if abouts is None else s.about in abouts
    return False


SAID = frozenset({"stated", "correction"})


def _latest_traits(traits: Iterable[Trait]) -> list[Trait]:
    """The latest of each attribute, what was said apart from what an outcome derived."""
    latest: dict[tuple[str, str | None, str | None, bool], Trait] = {}
    for t in traits:
        key = (t.name, t.category, t.system, t.source in SAID)
        if key not in latest or t.said_at >= latest[key].said_at:
            latest[key] = t
    return list(latest.values())


def shown_fields(body: Body | None) -> dict[str, dict[str, Any]]:
    """What the subject was last shown of each item, by ref: the fields of its latest exposure, which never
    kept a sensitive or personal one."""
    shown: dict[str, dict[str, Any]] = {}
    for exposure in sorted(body.exposures, key=lambda x: x.at) if body else ():
        for _, ref, values in exposure.items:
            shown[ref] = dict(values)
    return shown


def shown_numbers(body: Body | None, catalog: Catalog, now: datetime) -> dict[str, dict[str, dict[str, Any]]]:
    """The numbers of each item as the subject was last shown them, by ref: each field its type declares as a
    number, with its role and whether it may be claimed now, which it may while the type allows claims of it
    and no more than the field's age for claims has passed since it was shown."""
    shown: dict[str, dict[str, dict[str, Any]]] = {}
    for exposure in sorted(body.exposures, key=lambda x: x.at) if body else ():
        age_ms = (now - exposure.at).total_seconds() * 1000
        for _, ref, values in exposure.items:
            type_ = ref.split(":", 1)[0]
            numbers: dict[str, dict[str, Any]] = {}
            for name, value in values.items():
                spec = catalog.quotable.get(f"{type_}.{name}")
                if spec is None or isinstance(value, bool) or not isinstance(value, int | float):
                    continue
                limit = spec.claim_max_age_ms
                safe = limit is not None and age_ms <= limit
                numbers[name] = {"v": value, "role": spec.role, "claim_safe": safe}
            shown[ref] = numbers
    return shown


def _values(ref: str, shown: Mapping[str, Any], catalog: Catalog) -> dict[str, str]:
    """The `type.field` = value pairs of an item that may feed affinity: text, not hidden."""
    type_ = ref.split(":", 1)[0]
    out = {}
    for name, value in shown.items():
        attr = f"{type_}.{name}"
        if isinstance(value, str) and value and len(value) <= MAX_TEXT and attr not in catalog.hidden:
            out[attr] = value
    return out


def _kept_shown(ref: str, shown: Mapping[str, Any], catalog: Catalog) -> dict[str, Any]:
    type_ = ref.split(":", 1)[0]
    return {k: v for k, v in shown.items() if f"{type_}.{k}" not in catalog.hidden}


def _value(body: Body, attr: str, value: str) -> Value:
    return body.values.setdefault((attr, value), Value(attr, value))


def _expose(body: Body, exposure: Exposure, upto: int, now: datetime, catalog: Catalog) -> None:
    for pos, ref, shown in exposure.items:
        if exposure.counted < pos <= upto:
            for attr, value in _values(ref, shown, catalog).items():
                if _held_back(body, value_token(attr, value), exposure.at):
                    continue
                weight = position_weight(pos) * decay(age_days(exposure.at, now), catalog.family(attr))
                v = _value(body, attr, value)
                v.e += weight
                if pos <= TOP_POSITIONS:
                    v.e_top += weight
                v.seen_at = max(filter(None, (v.seen_at, exposure.at)), default=None)
    exposure.counted = max(exposure.counted, upto)


def _presented(body: Body, entry: Entry, now: datetime, catalog: Catalog) -> None:
    data = entry.interaction
    offset = int(data.get("offset") or 0)
    visible = data.get("visible_k")
    items = sorted(data["items"], key=lambda i: int(i["pos"]))
    at = _at(data.get("delivered_at")) or entry.at
    exposure = Exposure(str(data["exposure_id"]), at, offset, [])
    for item in items:
        ref = str(item["ref"])
        if catalog.sensitive(ref):
            continue
        p = body.presented.get(ref)
        body.presented[ref] = Presented(ref, (p.times if p else 0) + 1, at)
        if len(exposure.items) < MAX_EXPOSURE_ITEMS:
            exposure.items.append((int(item["pos"]), ref, _kept_shown(ref, item.get("shown") or {}, catalog)))
    upto = offset + int(visible) if visible else max((int(i["pos"]) for i in items), default=offset)
    _expose(body, exposure, upto, now, catalog)
    body.exposures = [x for x in body.exposures if x.exposure_id != exposure.exposure_id] + [exposure]


def _seen(body: Body, entry: Entry, now: datetime, catalog: Catalog) -> None:
    wanted = str(entry.interaction["exposure_id"])
    for exposure in body.exposures:
        if exposure.exposure_id == wanted:
            _expose(body, exposure, int(entry.interaction["max_index_seen"]), now, catalog)


def _item(body: Body, ref: str, exposure_id: str | None) -> dict[str, Any]:
    """What an item showed, from the exposure named, or else the latest one that held it."""
    for exposure in sorted(body.exposures, key=lambda x: x.exposure_id != exposure_id):
        for _, item_ref, shown in exposure.items:
            if item_ref == ref:
                return shown
    return {}


def _signal(
    body: Body,
    entry: Entry,
    ref: str,
    shown: Mapping[str, Any],
    amount: float,
    now: datetime,
    catalog: Catalog,
) -> None:
    for attr, value in _values(ref, shown, catalog).items():
        if _held_back(body, value_token(attr, value), entry.at):
            continue
        factor = decay(age_days(entry.at, now), catalog.family(attr))
        add_signal(_value(body, attr, value), entry.session_key(), amount * factor)


def _interest(
    body: Body, ref: str, reason: str, at: datetime, shown: Mapping[str, Any], catalog: Catalog
) -> None:
    if _held_back(body, interest_token(ref), at):
        return
    tracked = catalog.tracked.get(ref.split(":", 1)[0], frozenset())
    before = body.interests.get(ref)
    kept = {k: v for k, v in shown.items() if k in tracked}
    body.interests[ref] = Interest(
        ref,
        reason if before is None or before.reason != "watch" else "watch",
        (before.times if before else 0) + 1,
        at,
        kept or (before.shown if before else {}),
        before.condition if before else None,
        before.consent if before else None,
        before.until if before else None,
    )


def _engaged(body: Body, entry: Entry, now: datetime, catalog: Catalog) -> None:
    data = entry.interaction
    ref = str(data["ref"])
    if catalog.sensitive(ref):
        return
    shown = _item(body, ref, str(data["exposure_id"]) if data.get("exposure_id") else None)
    before = body.interests.get(ref)
    if before is None or entry.at - before.at >= REPEAT_WINDOW or entry.at < before.at:
        _signal(body, entry, ref, shown, ENGAGED[str(data["how"])], now, catalog)
    _interest(body, ref, "engaged", max(entry.at, before.at) if before else entry.at, shown, catalog)


def _feedback(body: Body, entry: Entry, now: datetime, catalog: Catalog) -> None:
    data = entry.interaction
    ref = str(data["ref"])
    if catalog.sensitive(ref):
        return
    shown = _item(body, ref, None)
    reasons = {str(a).split(".", 1)[-1] for a in data.get("reason_attrs") or ()}
    if reasons:
        shown = {k: v for k, v in shown.items() if k in reasons}
    positive = data["polarity"] == "positive"
    _signal(body, entry, ref, shown, FEEDBACK if positive else -FEEDBACK, now, catalog)
    if positive:
        _interest(body, ref, "feedback", entry.at, shown, catalog)
        body.excluded.pop(ref, None)
    else:
        body.excluded[ref] = entry.at


def _preference(body: Body, entry: Entry, now: datetime, catalog: Catalog) -> None:
    data = entry.interaction
    scope = str(data.get("scope") or "session")
    if scope == "turn":
        return  # held by the SDK for its own turn; a later read never carries it
    if scope in ("object", "about") and _held_back(body, stated_token(entry.key), entry.at):
        return  # withdrawn by a correction: only a later saying brings it back
    said = Stated(
        key=entry.key,
        attr=str(data.get("attr") or ""),
        op=str(data["op"]),
        values=list(data["values"]),
        strength=str(data["strength"]),
        scope=scope,
        source=str(data.get("source") or "stated"),
        said_at=entry.at,
        category=data.get("category"),
        expires_at=_at(data.get("expires_at")),
        session=entry.session,
        turn_id=entry.turn_id,
        event_key=entry.event_key,
        object=data.get("object") if scope == "object" else None,
        text=data.get("text") if scope in ("object", "about") else None,
        audience=data.get("audience") if scope in ("object", "about") else None,
        about=data.get("about") if scope == "about" else None,
        speaker=data.get("speaker") if scope in ("object", "about") else None,
        only_with=data.get("only_with") if scope in ("object", "about") else None,
        contact=bool(data.get("contact")) if scope in ("object", "about") else False,
    )
    same = _said_shape(said)
    body.stated = [s for s in body.stated if s.key != said.key and _said_shape(s) != same] + [said]
    if (
        scope == "persistent"
        and said.attr
        and said.strength in ("prefer", "avoid")
        and said.op in ("in", "eq", "not_in", "ne")
    ):
        sign = 1.0 if (said.strength == "prefer") == (said.op in ("in", "eq")) else -1.0
        factor = decay(age_days(entry.at, now), catalog.family(said.attr))
        for value in said.values:
            if (
                isinstance(value, str)
                and said.attr not in catalog.hidden
                and not _held_back(body, value_token(said.attr, value), entry.at)
            ):
                add_signal(
                    _value(body, said.attr, value),
                    entry.session_key(),
                    sign * STATED_PERSISTENT * factor,
                )


def _said_shape(s: Stated) -> tuple[Any, ...]:
    """Two preferences with this same shape say the same thing: the later one stands."""
    return (
        s.attr,
        s.op,
        tuple(map(str, s.values)),
        s.strength,
        s.scope,
        s.category,
        s.session if s.scope == "session" else None,
        s.object,
        s.about,
        s.text,
    )


def _attribute(body: Body, entry: Entry, now: datetime, catalog: Catalog) -> None:  # noqa: ARG001
    data = entry.interaction
    trait = Trait(
        str(data["name"]), data["value"], str(data.get("source") or "stated"), entry.at, data.get("system"),
        data.get("category"),
    )  # fmt: skip
    body.traits = _latest_traits([*body.traits, trait])


def _watch(body: Body, entry: Entry, now: datetime, catalog: Catalog) -> None:  # noqa: ARG001
    data = entry.interaction
    ref = str(data["ref"])
    if catalog.sensitive(ref) or _held_back(body, interest_token(ref), entry.at):
        return
    before = body.interests.get(ref)
    body.interests[ref] = Interest(
        ref, "watch", (before.times if before else 0) + 1, entry.at, before.shown if before else {},
        str(data["condition"]), str(data["consent_event_key"]), _at(data.get("expires_at")),
    )  # fmt: skip


def _unwatch(body: Body, entry: Entry, now: datetime, catalog: Catalog) -> None:  # noqa: ARG001
    ref = str(entry.interaction["ref"])
    before = body.interests.get(ref)
    if before is not None and before.reason == "watch":
        body.interests[ref] = replace(before, reason="engaged", condition=None, consent=None, until=None)


def _at(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _kept_raw_only(body: Body, entry: Entry, now: datetime, catalog: Catalog) -> None:
    """A tool call that returned nothing or little (`demand`): measured from the raw interactions, never
    part of what the subject wants."""


_FOLD = {
    "demand": _kept_raw_only,
    "presented": _presented,
    "seen": _seen,
    "engaged": _engaged,
    "feedback": _feedback,
    "preference": _preference,
    "attribute": _attribute,
    "watch": _watch,
    "unwatch": _unwatch,
}
