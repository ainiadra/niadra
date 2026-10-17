"""The subject's `now` view (24.3.13): what changed since they saw it, and the view as the turn block's lines.

Pure. `changes_since_seen` compares the values an interest was shown (the tracked fields, as the signals
module kept them) with what the shared object holds now; a price that moved after the card was shown is the
case. The lines go in the turn block after `slots`, never in the pack's text, so the prefix the model caches
stays the same bytes for the whole conversation (L5). They are short: an object's state, its outcome and its
next deadline; an interest only when something changed or may not be claimed. Dates are absolute, in the
space's time zone, as the pack's templates write them.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from niadra.domain.compile.bound_text import Dates, more_text, row_text
from niadra.domain.compile.envelope import preamble, wrap
from niadra.domain.compile.templates import language
from niadra.domain.memory.bound import BoundRow

CONTENT_CHARS = 280
"""What a line quotes of a content field: a longer text says it goes on, and is whole in the JSON view and in
the history (`open_history_item`)."""
_WORDS: dict[str, dict[str, str]] = {
    "pt": {
        "continues": "continua no histórico",
        "open": "estado",
        "state": "situação",
        "outcome": "desfecho",
        "final": "final",
        "provisional": "provisório",
        "due_on": "vence em",
        "open_items": "pendências abertas",
        "done_items": "já feitas",
        "reminder": "lembrete",
        "day": "%d/%m/%Y",
        "was": "era {was} quando visto",
        "no_claim": "não afirmar: {what}",
        "expired": "vencido por mudança em {inputs}",
        "date": "%d/%m/%Y %H:%M",
        "yes": "sim",
        "no": "não",
        "out": "fora: {pieces}",
        "of": "de {parents}",
        "more_objects": "mais {count} objetos deste cliente fora desta lista (consulte o histórico)",
        "more_done": "mais {count}",
    },
    "en": {
        "continues": "continues in the history",
        "open": "state",
        "state": "state",
        "outcome": "outcome",
        "final": "final",
        "provisional": "provisional",
        "due_on": "due",
        "open_items": "open items",
        "done_items": "already done",
        "reminder": "reminder",
        "day": "%Y-%m-%d",
        "was": "was {was} when seen",
        "no_claim": "do not claim: {what}",
        "expired": "expired by a change in {inputs}",
        "date": "%Y-%m-%d %H:%M",
        "yes": "yes",
        "no": "no",
        "out": "out: {pieces}",
        "of": "of {parents}",
        "more_objects": "{count} more objects of this customer off this list (see the history)",
        "more_done": "{count} more",
    },
}


@dataclass(frozen=True, slots=True)
class Change:
    ref: str
    field: str
    seen: Any
    now: Any
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ObjectLine:
    """What a line says of one object."""

    ref: str
    state: str | None = None
    outcome: str | None = None
    final: bool = False
    next_due: tuple[str, datetime] | None = None
    """The next armed timer: a reminder the type fires, said after the date it counts from."""
    dates: Sequence[tuple[str | None, Any]] = ()
    """(name, value): the dates the type's timers count from (a deadline's `due_on`, a hearing's
    `scheduled_for`), the object's own dates, said before any reminder so a model never reads a reminder's
    time as the due date. A None name is the date the object is due, said in the space's words ("vence em
    27/10/2026"): a computed value has no label, and its raw name (`due_on`) beside a promise's "até 09/10"
    left a model to pick the promise's date (juridico-sandbox, Q20 of 05/10/2026)."""
    fields: Sequence[tuple[str, Any]] = ()
    """(name, value): the fields the type tracks the changes of (a case's risk band), as they stand."""
    expired_by: Sequence[str] = ()
    prohibitions: Sequence[str] = ()
    changes: Sequence[Change] = ()
    content: Sequence[tuple[str, str]] = ()
    """(field, text): a third party's content the scan cleared, which goes inside the envelope."""
    interest: bool = False
    """A shared object the subject showed interest in: said only when something changed since they saw it, or
    when it says derived fields."""
    derived: Sequence[tuple[str, Any, Sequence[str]]] = ()
    """(name, value, pieces out): the derived fields the space lets the text say, computed from the related
    objects, with the ones that kept each from `yes` (`type namespace:id`)."""
    parents: Sequence[tuple[str, str | None]] = ()
    """The objects this one belongs to by its type's to-one relations, each as (`type namespace:id`, the name
    it goes by or None): a deadline's case, so a model reading the lines can tell which case a deadline is
    of."""
    name: str | None = None
    """What people call the object, its type's first key a conversation writes (`key.match`) with its label:
    "Número CNJ 0801234-67.2026.8.26.0100". A line names its object and its parents by it, so "the deadline
    in case 0801234-67..." finds the deadline's line, not a promise's date (juridico-sandbox, 05/10/2026)."""
    bound: Sequence[BoundRow] = ()
    """What its owner and participants said about it, as the line says it."""
    more: Mapping[str, int] = dataclasses.field(default_factory=dict)
    """How many more rows its memory holds than the line says, by group (`domain.memory.bound.OnLine`): the
    kept items (`done`) and the observations (`said`), so an agent knows the line is not all there is."""
    summarized: Sequence[tuple[str, int]] = ()
    """(type, count): its children whose own lines only said the system's bookkeeping, counted here instead
    (`summarized`)."""


def bookkeeping(line: ObjectLine, measured: bool) -> bool:
    """Whether a line says only a system's bookkeeping: no lifecycle state or outcome, no date, no amount
    (`measured`: a date, datetime or money field it says), nothing anyone said about it, no content, no
    derived field, no change. A customer-facing agent can tell a customer nothing from "Verificação de
    segurança clean; Extração do texto extracted; Método de extração texto_nativo", and four such lines per
    case drowned the case's deadlines in juridico-sandbox (05/10/2026)."""
    return not (
        line.state
        or line.outcome
        or line.dates
        or line.next_due
        or measured
        or line.bound
        or line.content
        or line.derived
        or line.changes
    )


def summarized(lines: Sequence[ObjectLine], plain: Sequence[bool]) -> list[ObjectLine]:
    """The lines with each `plain` (bookkeeping) child of an object whose line is here folded into that
    line as a count by type: the object is still served whole in the JSON view and its own route; its line
    says that it exists. A plain line with no parent here stays: it is all there is of it."""
    present = {line.ref for line in lines}
    counts: dict[str, dict[str, int]] = {}
    gone: set[int] = set()
    for i, (line, flat) in enumerate(zip(lines, plain, strict=True)):
        home = next((ref for ref, _ in line.parents if ref in present and ref != line.ref), None)
        if flat and home is not None:
            kind = line.ref.split(" ", 1)[0]
            counts.setdefault(home, {})[kind] = counts.get(home, {}).get(kind, 0) + 1
            gone.add(i)
    return [
        replace(line, summarized=tuple(sorted(counts[line.ref].items()))) if line.ref in counts else line
        for i, line in enumerate(lines)
        if i not in gone
    ]


def changes_since_seen(
    ref: str, shown: Mapping[str, Any], now: Mapping[str, tuple[Any, datetime | None]]
) -> list[Change]:
    """The fields the subject was shown whose value is not the one the object holds now; a field the object
    no longer holds is not a change (it is not known)."""
    changes = []
    for field, seen in sorted(shown.items()):
        current = now.get(field)
        if current is None:
            continue
        value, at = current
        if value != seen:
            changes.append(Change(ref, field, seen, value, at))
    return changes


def render(
    lines: Sequence[ObjectLine], locale: str, timezone: str, nonce: str = "", *, more: int = 0
) -> str | None:
    """The view as the turn block's lines, between the view's markers; None when there is nothing to say.
    Content goes inside the envelope of `nonce`, announced once (context-pack 10.7): the caller renders
    with no nonce first, derives the nonce from those bytes and the space's key, and renders again. `more`
    is how many objects of the subject the view leaves out, said on a last line."""
    lang = language(locale)
    words = _WORDS[lang]
    zone = ZoneInfo(timezone)
    dates = Dates(
        due=lambda at: _day(at, words, zone),
        kept=lambda at: at.astimezone(zone).strftime(words["day"]),
        said=lambda at: at.astimezone(zone).strftime(words["date"]),
        day_of=lambda at: at.astimezone(zone).date(),
    )
    said = [line for line in lines if not line.interest or line.changes or line.derived]
    body = [text for line in grouped(said) if (text := _line(line, words, zone, nonce, lang, dates))]
    if not body:
        return None
    if more:
        body.append(f"- {words['more_objects'].format(count=more)}")
    tag = words["open"]
    opening = [f"<{tag}>", *([preamble(locale, nonce)] if any(line.content for line in said) else [])]
    return "\n".join([*opening, *body, f"</{tag}>"])


def grouped(lines: Sequence[ObjectLine]) -> list[ObjectLine]:
    """The lines in their order, each object's children right after it: a case, then its deadlines, hearings
    and filings, so what belongs to a case reads under it. A line whose parent has no line here stays where
    it was."""
    present = {line.ref for line in lines}
    children: dict[str, list[ObjectLine]] = {}
    roots: list[ObjectLine] = []
    for line in lines:
        home = next((ref for ref, _ in line.parents if ref in present and ref != line.ref), None)
        (children.setdefault(home, []) if home is not None else roots).append(line)
    out: list[ObjectLine] = []
    placed: set[int] = set()

    def place(line: ObjectLine) -> None:
        if id(line) in placed:
            return
        placed.add(id(line))
        out.append(line)
        for child in children.get(line.ref, ()):
            place(child)

    for line in roots:
        place(line)
    # Parents that point at each other have no root: said in their own order.
    for line in lines:
        place(line)
    return out


def _named(ref: str, name: str | None) -> str:
    return f"{ref} [{name}]" if name else ref


def _line(
    line: ObjectLine, words: Mapping[str, str], zone: ZoneInfo, nonce: str, lang: str, dates: Dates
) -> str:
    parts: list[str] = []
    if line.state:
        parts.append(f"{words['state']} {line.state}")
    if line.outcome and line.outcome != line.state:
        kind = words["final"] if line.final else words["provisional"]
        parts.append(f"{words['outcome']} {line.outcome} ({kind})")
    for name, value in line.dates:
        parts.append(f"{words['due_on'] if name is None else name} {_when(value, words, zone)}")
    for name, value in line.fields:
        said = _when(value, words, zone) if isinstance(value, date) else _value(value, words)
        parts.append(f"{name} {said}")
    if line.next_due and not line.final:
        # The reminder's day, as a date with no time is written: its timer's name ("notice", "notice_48h") is
        # the type's identifier, and "lembrete notice 25/10/2026 00:00" said nothing a reader could use
        # (juridico-zero, 08/10/2026).
        parts.append(f"{words['reminder']} {_day(line.next_due[1], words, zone)}")
    if line.expired_by:
        parts.append(words["expired"].format(inputs=", ".join(line.expired_by)))
    for name, value, out in line.derived:
        said = f"{name} {_value(value, words)}"
        parts.append(f"{said} ({words['out'].format(pieces=', '.join(out))})" if out else said)
    for change in line.changes:
        was = words["was"].format(was=_value(change.seen, words))
        parts.append(f"{change.field} {_value(change.now, words)} ({was})")
    if line.prohibitions:
        parts.append(words["no_claim"].format(what=", ".join(line.prohibitions)))
    for field, text in line.content:
        quote = text if len(text) <= CONTENT_CHARS else f"{text[:CONTENT_CHARS]}…"
        goes_on = f" ({words['continues']})" if len(text) > CONTENT_CHARS else ""
        parts.append(f"{field} {wrap(quote, nonce)}{goes_on}")
    items = [row for row in line.bound if row.kind == "item"]
    parts += [row_text(row, lang, dates) for row in line.bound if row.kind != "item"]
    if line.more.get("said"):
        parts.append(more_text("said", line.more["said"], lang))
    parts += [f"{kind.replace('_', ' ')}: {count}" for kind, count in line.summarized]
    if not parts and not items:
        return ""
    # A parent goes by its name alone when it has one: its own line, with its ref, is in the block.
    parents = ", ".join(name or ref for ref, name in line.parents)
    of = f" ({words['of'].format(parents=parents)})" if line.parents else ""
    head = f"- {_named(line.ref, line.name)}{of}" + (f": {'; '.join(parts)}" if parts else "")
    return "\n".join([head, *_items(items, words, lang, dates, line.more.get("done", 0))])


def _items(
    items: Sequence[BoundRow], words: Mapping[str, str], lang: str, dates: Dates, more_done: int = 0
) -> list[str]:
    """An object's items under its line, what is still to be done apart from what was done: one line of
    open items and one of done ones. On one semicolon line, "promessa: Avisar o cliente da intimação, até
    06/10/2026; cumprido em 06/10/2026: Protocolar a contestação" was read as the notice being done
    (juridico-sandbox, 06/10/2026)."""
    open_ = [row_text(row, lang, dates) for row in items if row.kept_at is None]
    done = [row_text(row, lang, dates) for row in items if row.kept_at is not None]
    if done and more_done:
        done.append(words["more_done"].format(count=more_done))
    return [
        f"  - {words[label]}: {'; '.join(said)}"
        for label, said in (("open_items", open_), ("done_items", done))
        if said
    ]


def _when(value: Any, words: Mapping[str, str], zone: ZoneInfo) -> str:
    """A date as the space writes it: a day as it is, an instant in the space's time zone."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value) if "T" in value else date.fromisoformat(value)
        except ValueError:
            return str(value)
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=zone)
        return moment.astimezone(zone).strftime(words["date"])
    if isinstance(value, date):
        return value.strftime(words["day"])
    return str(value)


def _day(at: datetime, words: Mapping[str, str], zone: ZoneInfo) -> str:
    """An instant as the space writes it, the day alone when it is the day's start (a date with no time)."""
    local = at.astimezone(zone)
    return local.strftime(words["day"] if local.time() == time() else words["date"])


def _value(value: Any, words: Mapping[str, str]) -> str:
    if isinstance(value, bool):
        return words["yes"] if value else words["no"]
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)
