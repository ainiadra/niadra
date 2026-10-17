"""The `handoff` view (24.3.4): what the receiving side of a handoff reads first, in the space's language and
at its own verification level. The coordination lines (who hands off and why, who holds the subject, what was
offered and done, what the subject may not be contacted for, how to report back) go before the customer's
block the context compiler wrote for that level, so the package never carries what the receiver could not
read. Canonical, with no model: the same package renders the same bytes.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from niadra.domain.compile.templates import Formatter

_WORDS: dict[str, dict[str, str]] = {
    "pt": {
        "from": "Transbordo de {source} para {target}.",
        "reason": "Motivo: {reason}.",
        "holder": "Com: {holder} ({level}) até {until}.",
        "offers": "Em vigor: {items}.",
        "offer": "{kind} até {until}",
        "effects": "Já feito: {items}.",
        "effect": "{kind} ({state})",
        "suppressed": "Não contatar para: {purposes}.",
        "outcome": "Responda com um de: {vocabulary}.",
        "outcome_by": "Responda com um de: {vocabulary}, até {until}.",
        "done": "feito",
        "reserved": "em andamento",
        "failed": "falhou",
        "unknown_outcome": "resultado desconhecido",
    },
    "en": {
        "from": "Handoff from {source} to {target}.",
        "reason": "Reason: {reason}.",
        "holder": "With: {holder} ({level}) until {until}.",
        "offers": "In force: {items}.",
        "offer": "{kind} until {until}",
        "effects": "Already done: {items}.",
        "effect": "{kind} ({state})",
        "suppressed": "Do not contact for: {purposes}.",
        "outcome": "Report back one of: {vocabulary}.",
        "outcome_by": "Report back one of: {vocabulary}, by {until}.",
        "done": "done",
        "reserved": "in progress",
        "failed": "failed",
        "unknown_outcome": "outcome unknown",
    },
}


@dataclass(frozen=True, slots=True)
class Holder:
    name: str
    level: str
    until: datetime


@dataclass(frozen=True, slots=True)
class HandoffLines:
    """What coordination knows at the handoff: commitments as `(type, until)`, effects as `(kind, state)`."""

    source: str
    target: str
    reason: str | None = None
    holder: Holder | None = None
    commitments: Sequence[tuple[str, datetime]] = ()
    effects: Sequence[tuple[str, str]] = ()
    suppressed: Sequence[str] = ()
    vocabulary: Sequence[str] = ()
    expected_by: datetime | None = None


def render(lines: HandoffLines, customer: str, fmt: Formatter) -> str:
    """The handoff's coordination lines, then the customer's block as the compiler wrote it."""
    words = _WORDS[fmt.lang]
    out = [words["from"].format(source=lines.source, target=lines.target)]
    if lines.reason:
        out.append(words["reason"].format(reason=lines.reason))
    if lines.holder is not None:
        holder = lines.holder
        out.append(
            words["holder"].format(
                holder=holder.name, level=fmt.value(holder.level), until=fmt.timestamp(holder.until)
            )
        )
    if lines.commitments:
        offers = ", ".join(
            words["offer"].format(kind=fmt.value(kind), until=fmt.date(until))
            for kind, until in lines.commitments
        )
        out.append(words["offers"].format(items=offers))
    if lines.effects:
        done = ", ".join(
            words["effect"].format(kind=fmt.value(kind), state=words.get(state, state))
            for kind, state in lines.effects
        )
        out.append(words["effects"].format(items=done))
    if lines.suppressed:
        out.append(words["suppressed"].format(purposes=", ".join(fmt.value(p) for p in lines.suppressed)))
    if lines.vocabulary:
        vocabulary = ", ".join(fmt.value(v) for v in lines.vocabulary)
        if lines.expected_by is not None:
            out.append(
                words["outcome_by"].format(vocabulary=vocabulary, until=fmt.timestamp(lines.expected_by))
            )
        else:
            out.append(words["outcome"].format(vocabulary=vocabulary))
    head = "\n".join(out)
    return f"{head}\n\n{customer.strip()}" if customer.strip() else head
