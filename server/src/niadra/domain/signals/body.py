"""The compact signals of one subject, or of one beneficiary under them (24.3.2): one sealed body per row of
`subject_signals`, rewritten by `derive_signals`.

It keeps affinity by (field, value) as decayed accumulators, what the person said (preferences and
attributes), their interests, the objects they were shown and the ones they turned down, a few recent
exposures, so a later `engaged` or `seen` of the same list finds what the items showed, and the inferences
the person deleted, so the evidence behind them never infers them again (24.5.3). Every accumulator is
as of `at`: a fold decays them to its own time first, so one timestamp serves them all.
"""

import json
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

BODY_VERSION = 1
DERIVED_BYTES = 16_320
"""What the derived accumulators take of the sealed body at most: affinity by value, interests, the objects
shown and the recent exposures. They decay and are computed again from the raw interaction segments kept in
object storage, so trimming them loses nothing memory was told. What the person said (`stated`, `traits`),
the inferences they deleted (`suppressed`) and the objects they turned down (`excluded`) are never trimmed."""
MAX_STORED_BYTES = 4 * 1024 * 1024
"""The sealed body's ceiling, which `subject_signals.size_bytes` checks: a runaway guard, never reached by
what people say."""
MAX_VALUES = 256
MAX_INTERESTS = 200
MAX_PRESENTED = 200
MAX_EXPOSURES = 10
MAX_EXPOSURE_ITEMS = 20
TRACKED_SESSIONS = 3
"""Sessions whose contribution to a value is kept apart, so none of them counts for more than 30% of it."""


@dataclass(slots=True)
class Value:
    """The accumulators of one field's value (`item_variant.color` = `bege`)."""

    attr: str
    value: str
    e: float = 0.0
    """Effective exposure: each exposure weighted by the chance its position was seen."""
    e_top: float = 0.0
    """The part of `e` shown in the first three positions, for the implicit negative."""
    s: float = 0.0
    """The weighted signal, summed over every session."""
    n: float = 0.0
    """Signal events behind `s`, counted, not decayed: `n_eff` of the evidence rule."""
    sessions: list[tuple[str, float]] = field(default_factory=list)
    """The largest session contributions to `s`, by session."""
    seen_at: datetime | None = None


@dataclass(slots=True)
class Stated:
    """A preference the person said, or the SDK took from a tool's arguments, or a correction; or, with
    `scope: object`, an instruction the person said about one object, or with `scope:
    about`, about the organization they spoke of: it holds for the reads of that object or organization and
    never for the person's own."""

    key: str
    attr: str
    op: str
    values: list[Any]
    strength: str
    scope: str
    source: str
    said_at: datetime
    category: str | None = None
    expires_at: datetime | None = None
    session: str | None = None
    turn_id: str | None = None
    event_key: str | None = None
    object: str | None = None
    """With `scope: object`, the object as `type:namespace:id`."""
    text: str | None = None
    """With `scope: object`, the instruction as it was said, short: what a reader without a tool binding
    for `attr` honors."""
    audience: str | None = None
    """With `scope: object` or `about`, who besides the person may read it: `shared` or `internal`."""
    about: str | None = None
    """With `scope: about`, the handle of the organization it is about."""
    speaker: str | None = None
    """With `scope: object` or `about`, `human_agent` when the company's own person said it (a staff user of
    an internal agent); None for the customer."""
    only_with: str | None = None
    """With `scope: object` or `about`, the one person it holds with, as the turns name them."""
    contact: bool = False
    """With `scope: object` or `about`, a contact preference of `only_with`: how or when to reach them."""


@dataclass(slots=True)
class Trait:
    """What the person is: said (`stated`, `correction`), never guessed here."""

    name: str
    value: Any
    source: str
    said_at: datetime
    system: str | None = None
    category: str | None = None


@dataclass(slots=True)
class Interest:
    ref: str
    reason: str
    times: int
    at: datetime
    shown: dict[str, Any] = field(default_factory=dict)
    """The values the object showed for the fields its type tracks, for `changes_since_seen`."""
    condition: str | None = None
    consent: str | None = None
    until: datetime | None = None


@dataclass(slots=True)
class Presented:
    ref: str
    times: int
    at: datetime


@dataclass(slots=True)
class Exposure:
    exposure_id: str
    at: datetime
    counted: int
    """The highest position already counted in `e`: a later `seen` counts only past it."""
    items: list[tuple[int, str, dict[str, Any]]] = field(default_factory=list)
    """Position, object and the values that feed affinity."""


@dataclass(slots=True)
class Body:
    at: datetime
    values: dict[tuple[str, str], Value] = field(default_factory=dict)
    stated: list[Stated] = field(default_factory=list)
    traits: list[Trait] = field(default_factory=list)
    interests: dict[str, Interest] = field(default_factory=dict)
    presented: dict[str, Presented] = field(default_factory=dict)
    excluded: dict[str, datetime] = field(default_factory=dict)
    exposures: list[Exposure] = field(default_factory=list)
    last_activity_at: datetime | None = None
    suppressed: dict[str, datetime] = field(default_factory=dict)
    """An inference the person deleted (`derive.value_token`, `derive.interest_token`) -> the moment up to
    which evidence no longer counts for it."""


def _t(value: datetime | None) -> float | None:
    return None if value is None else round(value.timestamp(), 3)


def _d(value: float | None) -> datetime | None:
    return None if value is None else datetime.fromtimestamp(value, UTC)


def _r(value: float) -> float:
    return round(value, 4)


def to_json(body: Body) -> dict[str, Any]:
    return {
        "v": BODY_VERSION,
        "at": _t(body.at),
        "la": _t(body.last_activity_at),
        "val": [
            [
                v.attr,
                v.value,
                _r(v.e),
                _r(v.e_top),
                _r(v.s),
                _r(v.n),
                [[k, _r(c)] for k, c in v.sessions],
                _t(v.seen_at),
            ]
            for v in body.values.values()
        ],
        "st": [
            {
                "k": s.key,
                "a": s.attr,
                "o": s.op,
                "vs": s.values,
                "g": s.strength,
                "sc": s.scope,
                "src": s.source,
                "at": _t(s.said_at),
                "c": s.category,
                "x": _t(s.expires_at),
                "ss": s.session,
                "t": s.turn_id,
                "e": s.event_key,
            }
            | ({"ob": s.object, "tx": s.text, "au": s.audience} if s.object else {})
            | ({"ab": s.about, "tx": s.text, "au": s.audience} if s.about else {})
            | ({"sp": s.speaker} if s.speaker else {})
            | ({"ow": s.only_with} if s.only_with else {})
            | ({"ct": 1} if s.contact else {})
            for s in body.stated
        ],
        "tr": [
            {"n": t.name, "v": t.value, "src": t.source, "at": _t(t.said_at), "sy": t.system, "c": t.category}
            for t in body.traits
        ],
        "in": [
            {
                "r": i.ref,
                "why": i.reason,
                "n": i.times,
                "at": _t(i.at),
                "sh": i.shown,
                "cd": i.condition,
                "cs": i.consent,
                "u": _t(i.until),
            }
            for i in body.interests.values()
        ],
        "pr": [[p.ref, p.times, _t(p.at)] for p in body.presented.values()],
        "ex": [[ref, _t(at)] for ref, at in body.excluded.items()],
        "xp": [[x.exposure_id, _t(x.at), x.counted, [list(i) for i in x.items]] for x in body.exposures],
        "sp": [[token, _t(until)] for token, until in body.suppressed.items()],
    }


def from_json(data: dict[str, Any]) -> Body:
    if data.get("v") != BODY_VERSION:
        raise ValueError(f"unknown signals body version {data.get('v')!r}")
    at = _d(data["at"])
    assert at is not None
    values = {}
    for attr, value, e, e_top, s, n, sessions, seen in data["val"]:
        values[(attr, value)] = Value(attr, value, e, e_top, s, n, [(k, c) for k, c in sessions], _d(seen))
    stated = [
        Stated(
            key=s["k"],
            attr=s["a"],
            op=s["o"],
            values=s["vs"],
            strength=s["g"],
            scope=s["sc"],
            source=s["src"],
            said_at=_d(s["at"]) or at,
            category=s["c"],
            expires_at=_d(s["x"]),
            session=s["ss"],
            turn_id=s["t"],
            event_key=s["e"],
            object=s.get("ob"),
            text=s.get("tx"),
            audience=s.get("au"),
            about=s.get("ab"),
            speaker=s.get("sp"),
            only_with=s.get("ow"),
            contact=bool(s.get("ct")),
        )
        for s in data["st"]
    ]
    traits = [Trait(t["n"], t["v"], t["src"], _d(t["at"]) or at, t["sy"], t["c"]) for t in data["tr"]]
    interests = {
        i["r"]: Interest(i["r"], i["why"], i["n"], _d(i["at"]) or at, i["sh"], i["cd"], i["cs"], _d(i["u"]))
        for i in data["in"]
    }
    presented = {ref: Presented(ref, times, _d(t) or at) for ref, times, t in data["pr"]}
    excluded = {ref: _d(t) or at for ref, t in data["ex"]}
    exposures = [
        Exposure(xid, _d(t) or at, counted, [(pos, ref, shown) for pos, ref, shown in items])
        for xid, t, counted, items in data["xp"]
    ]
    suppressed = {token: _d(t) or at for token, t in data["sp"]}
    return Body(
        at, values, stated, traits, interests, presented, excluded, exposures, _d(data["la"]), suppressed
    )


def encode(body: Body) -> bytes:
    """The body as it is sealed: compact JSON, compressed. Its derived accumulators within `DERIVED_BYTES`
    (`trim`); what the person said, deleted or turned down whole."""
    data = trim(body)
    return zlib.compress(json.dumps(to_json(data), separators=(",", ":"), ensure_ascii=False).encode(), 6)


def decode(raw: bytes) -> Body:
    return from_json(json.loads(zlib.decompress(raw)))


def trim(body: Body) -> Body:
    """The derived accumulators within their caps, then within `DERIVED_BYTES`, dropping what carries the
    least evidence first. Never what the person said, the inferences they deleted or what they turned down:
    memory keeps those whole, however many (the 51st instruction a person gave used to push out the first)."""
    body.values = dict(
        sorted(body.values.items(), key=lambda kv: (kv[1].n, kv[1].e), reverse=True)[:MAX_VALUES]
    )
    body.interests = dict(
        sorted(body.interests.items(), key=lambda kv: kv[1].at, reverse=True)[:MAX_INTERESTS]
    )
    body.presented = dict(
        sorted(body.presented.items(), key=lambda kv: kv[1].at, reverse=True)[:MAX_PRESENTED]
    )
    body.excluded = dict(sorted(body.excluded.items(), key=lambda kv: kv[1], reverse=True))
    body.stated = sorted(body.stated, key=lambda s: s.said_at, reverse=True)
    body.traits = sorted(body.traits, key=lambda t: t.said_at, reverse=True)
    body.exposures = sorted(body.exposures, key=lambda x: x.at, reverse=True)[:MAX_EXPOSURES]
    body.suppressed = dict(sorted(body.suppressed.items(), key=lambda kv: kv[1], reverse=True))
    while _size(_derived(body)) > DERIVED_BYTES:
        if body.exposures:
            body.exposures.pop()
        elif len(body.values) > 16:
            body.values = dict(list(body.values.items())[: len(body.values) * 3 // 4])
        elif body.presented:
            body.presented = dict(list(body.presented.items())[: len(body.presented) // 2])
        elif body.interests:
            body.interests = dict(list(body.interests.items())[: len(body.interests) // 2])
        else:
            break
    return body


def told(body: Body) -> Body:
    """What the body keeps once the subject has been inactive a long time (24.5.1): what the person said
    (`stated`, which holds their instructions and restrictions, and `traits`), the inferences they deleted
    and the objects they turned down. The derived accumulators go: they are inferences, recomputed from the
    raw segments if the person comes back."""
    return Body(
        body.at,
        stated=body.stated,
        traits=body.traits,
        excluded=body.excluded,
        last_activity_at=body.last_activity_at,
        suppressed=body.suppressed,
    )


def tells(body: Body) -> bool:
    """Whether the body holds anything the person said or decided."""
    return bool(body.stated or body.traits or body.excluded or body.suppressed)


def _derived(body: Body) -> Body:
    """The body's derived accumulators alone, for their byte budget."""
    return Body(
        body.at,
        values=body.values,
        interests=body.interests,
        presented=body.presented,
        exposures=body.exposures,
        last_activity_at=body.last_activity_at,
    )


def _size(body: Body) -> int:
    return len(
        zlib.compress(json.dumps(to_json(body), separators=(",", ":"), ensure_ascii=False).encode(), 6)
    )
