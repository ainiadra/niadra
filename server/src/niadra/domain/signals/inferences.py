"""What the signals infer about a subject, as the reviewable profile shows it (24.5.3; LGPD, art. 20).

Every value the body keeps accumulators for is an inference: `soft_constraint` when the block would carry it
as a soft entry, `attribute` when it is the size the person's own signal points to, `affinity` otherwise. So
is every interest. Each comes with its evidence as the body counts it (effective exposure, weighted signal,
events and sessions behind it), its confidence and when it was last seen. What the person said is not an
inference, and stays out.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from niadra.domain.signals.affinity import ALPHA, SIZE, Prior, affinity, lift, no_prior, sizes, soft_entries
from niadra.domain.signals.body import Body
from niadra.domain.signals.derive import Catalog, interest_token, stated_token, value_token


@dataclass(frozen=True, slots=True)
class Inferred:
    token: str
    """`derive.value_token` or `derive.interest_token`: what a deletion suppresses."""
    kind: str
    origin: str
    at: datetime
    attr: str | None = None
    name: str | None = None
    """The attribute an inferred size is, as the constraints block names it."""
    value: Any = None
    polarity: str | None = None
    ref: str | None = None
    confidence: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


def inferred(body: Body, catalog: Catalog, prior: Prior = no_prior) -> list[Inferred]:
    """Every inference of one body, by token."""
    values = list(body.values.values())
    soft = {(a.attr, a.value) for a in soft_entries(values, catalog.family, prior)}
    sized = {(a.attr, a.value) for a in sizes(values, catalog.family, prior)}
    out: list[Inferred] = []
    for v in values:
        found = affinity(v, prior(v.attr, v.value))
        key = (v.attr, v.value)
        kind, polarity, name = "affinity", None, None
        if key in sized:
            kind, name = "attribute", size_name(v.attr, catalog)
        elif key in soft and catalog.family(v.attr) != SIZE and found is not None:
            kind, polarity = "soft_constraint", found.polarity
        out.append(
            Inferred(
                token=value_token(v.attr, v.value),
                kind=kind,
                origin="implicit_negative" if found is not None and found.implicit else "interactions",
                at=v.seen_at or body.at,
                attr=v.attr,
                name=name,
                value=v.value,
                polarity=polarity,
                confidence=round(found.confidence if found else v.n / (v.n + ALPHA), 4),
                evidence={
                    "exposure": round(v.e, 4),
                    "signal": round(v.s, 4),
                    "events": round(v.n, 4),
                    "sessions": len(v.sessions),
                    "lift": round(lift(v, prior(v.attr, v.value)), 4),
                },
            )
        )
    for interest in body.interests.values():
        out.append(
            Inferred(
                token=interest_token(interest.ref),
                kind="interest",
                origin=interest.reason,
                at=interest.at,
                ref=interest.ref,
                evidence={"times": interest.times},
            )
        )
    # An instruction about an object is what the person said, listed so a correction can withdraw it.
    out += [
        Inferred(token=stated_token(s.key), kind="instruction", origin="stated", at=s.said_at, ref=s.object,
                 value=s.text)
        for s in body.stated
        if s.scope in ("object", "about") and (s.object or s.about)
    ]  # fmt: skip
    return sorted(out, key=lambda i: i.token)


def size_name(attr: str, catalog: Catalog) -> str:
    """`size.pants` for `pants.size`, `size` for a field named after the family."""
    field_name = attr.split(".", 1)[-1]
    family = catalog.family(attr)
    return family if field_name == family else f"{family}.{field_name}"
