"""A deadline the customer states is structure, not prose (17, 6.6): "I need it by the 24th", "preciso até
sexta", "lo necesito para el día 20" becomes an open item with `due_at`, so the pinned body carries the date
before the agent says hello.

The model is asked for it; this pass is the net for when it writes the deadline only into the summary or
leaves it out. It reads the customer's turns without a model, in Portuguese, English and Spanish, and
resolves the day against the turn's own time in the space's time zone: a day of the month is the next one
from that day, a weekday the next one after it. It only reads a sentence that also asks for something
("need", "preciso", "necesito", "prazo"...), so "the internet was down until the 24th" is no deadline, and
it never invents one farther than `MAX_AHEAD` or in the past.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from niadra.domain.extract.result import OpenItemDraft
from niadra.domain.memory.items import same_day
from niadra.domain.records import EventRecord

MAX_AHEAD = timedelta(days=120)

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
    "segunda": 0, "terca": 1, "quarta": 2, "quinta": 3, "sexta": 4, "sabado": 5, "domingo": 6,
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4,
}  # fmt: skip
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
    "enero": 1, "febrero": 2, "marzo": 3, "mayo": 5, "junio": 6, "julio": 7, "septiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
}  # fmt: skip
_TOMORROW = {"tomorrow": "en", "amanha": "pt", "manana": "es"}

_CUES = {
    "pt": (
        r"\b(preciso|precisa|precisamos|precisaria|quero|queria|gostaria|necessito|prazo|resolv\w*|"
        r"soluc\w*|consert\w*|urgente|tem que|tenho que|no maximo|conseguem|consegue)\b"
    ),
    "en": (
        r"\b(need|needs|needed|want|would like|must|have to|has to|deadline|solve\w*|fix\w*|resolve\w*|"
        r"urgent|no later|at the latest|latest)\b"
    ),
    "es": (
        r"\b(necesito|necesita|necesitamos|quiero|quisiera|plazo|resolver|resuelv\w*|solucion\w*|"
        r"arregl\w*|urgente|a mas tardar|tiene que|tengo que)\b"
    ),
}
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?(?:\s*[/.-]\s*(\d{1,2})(?:\s*[/.-]\s*(\d{2,4}))?)?(?!\d)"
_WEEKDAY = r"(?:a\s+|o\s+|the\s+|el\s+|next\s+|proxima\s+|proximo\s+)?([a-z]+)(?:-feira)?"
_LEADS = {
    "pt": r"\b(?:ate|antes\s+d[aoe]|para\s+o|no\s+maximo\s+(?:ate\s+)?)",
    "en": r"\b(?:by|before|no\s+later\s+than|until)",
    "es": r"\b(?:para|hasta|antes\s+del?|a\s+mas\s+tardar\s+(?:el\s+)?)",
}
_DAY_WORDS = r"(?:\s+(?:o|the|el))?(?:\s+(?:dia|day))?"
_UNIT_AFTER = re.compile(
    r"\s*(?:h\b|hs\b|horas?\b|hours?\b|min|dias?\b|days?\b|semanas?\b|weeks?\b|mes\b|meses\b|months?\b|"
    r"anos?\b|years?\b|%|reais\b)"
)
_MONTH_AFTER = re.compile(r"\s+(?:de\s+|of\s+)?([a-z]+)")
_SENTENCE = re.compile(r"(?<=[.!?;\n])\s+")


@dataclass(frozen=True, slots=True)
class Deadline:
    day: date
    lang: str


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()


def deadlines_in(text: str, said_at: datetime, timezone: str) -> list[Deadline]:
    """Every deadline the text asks for, in order, resolved against the day it was said."""
    today = said_at.astimezone(ZoneInfo(timezone)).date()
    found: list[Deadline] = []
    for sentence in _SENTENCE.split(fold(text)):
        for lang, cue in _CUES.items():
            if not re.search(cue, sentence):
                continue
            for day in _days(sentence, lang, today):
                if today <= day <= today + MAX_AHEAD and all(d.day != day for d in found):
                    found.append(Deadline(day, lang))
    return found


def _days(sentence: str, lang: str, today: date) -> list[date]:
    lead = _LEADS[lang]
    days: list[date] = []
    for m in re.finditer(lead + _DAY_WORDS + r"\s+" + _DAY, sentence):
        rest = sentence[m.end() :]
        month_name = None
        if m.group(2) is None:
            if _UNIT_AFTER.match(rest):
                continue  # "até 24 horas", "by 3 days": a span, not a day
            named = _MONTH_AFTER.match(rest)
            month_name = named.group(1) if named and named.group(1) in _MONTHS else None
        day = _calendar_day(today, int(m.group(1)), m.group(2), m.group(3), month_name)
        if day is not None:
            days.append(day)
    for m in re.finditer(lead + r"\s+([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?!\d)", sentence):
        if m.group(1) in _MONTHS:  # "by September 24"
            day = _calendar_day(today, int(m.group(2)), None, None, m.group(1))
            if day is not None:
                days.append(day)
    for m in re.finditer(lead + r"\s+" + _WEEKDAY + r"\b", sentence):
        weekday = _WEEKDAYS.get(m.group(1))
        if weekday is not None:
            ahead = (weekday - today.weekday()) % 7 or 7
            days.append(today + timedelta(days=ahead))
    for word, word_lang in _TOMORROW.items():
        if word_lang == lang and re.search(lead + r"\s+" + word + r"\b", sentence):
            days.append(today + timedelta(days=1))
    return days


def _calendar_day(
    today: date, day: int, month: str | None, year: str | None, month_name: str | None
) -> date | None:
    number = int(month) if month else _MONTHS.get(month_name or "")
    try:
        if number is None:
            if day >= today.day:
                return date(today.year, today.month, day)
            following = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
            return following.replace(day=day)
        full = today.year
        if year is not None:
            full = int(year) + (2000 if len(year) == 2 else 0)
        candidate = date(full, number, day)
        if year is None and candidate < today:
            candidate = date(full + 1, number, day)
        return candidate
    except ValueError:
        return None


_RELATIVE = r"(?:hoje|amanha|today|tomorrow|hoy|manana)"
_WRITTEN = r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?!\d)"
_BESIDE = re.compile(
    rf"\b{_RELATIVE}\s*[,(]?\s*(?:(?:o\s+)?dia\s+|the\s+|el\s+)?{_WRITTEN}|{_WRITTEN}\s*[,(]?\s*{_RELATIVE}\b"
)


def written_day(texts: Sequence[str], said_at: datetime, timezone: str) -> datetime | None:
    """The day an item is due when the model gave none and its turns write a day next to a relative one:
    "enviar a cópia amanhã, 06/10/2026", written on 06/10, left the promise undated because the two disagree
    (juridico-sandbox, 06/10/2026). The written date wins, as a day (its start in the space's time zone, as
    the model's own dates: `domain.memory.items.due_end` runs it to the day's end); only when the turns write
    exactly one such day, from the day they were said to `MAX_AHEAD`."""
    today = said_at.astimezone(ZoneInfo(timezone)).date()
    found: set[date] = set()
    for text in texts:
        for m in _BESIDE.finditer(fold(text)):
            day, month, year = (m.group(1), m.group(2), m.group(3)) if m.group(1) else m.group(4, 5, 6)
            written = _calendar_day(today, int(day), month, year, None)
            if written is not None and today <= written <= today + MAX_AHEAD:
                found.add(written)
    if len(found) != 1:
        return None
    return datetime.combine(found.pop(), time(), tzinfo=ZoneInfo(timezone))


_DESCRIPTION = {
    "pt": "Prazo pedido pelo cliente",
    "en": "Deadline the customer asked for",
    "es": "Plazo pedido por el cliente",
}


def is_deadline_item(description: str) -> bool:
    """Whether an item is the net's own generic one ("Prazo pedido pelo cliente"), not one the model wrote."""
    return description.startswith(tuple(_DESCRIPTION.values()))


def with_deadlines(
    items: Sequence[OpenItemDraft],
    turns: Sequence[EventRecord],
    reference: datetime,
    timezone: str,
    subject: str | None = None,
    known_dues: Sequence[datetime] = (),
) -> list[OpenItemDraft]:
    """The model's items, each customer deadline among them: an item already due that day stands, an item
    citing the turn without a date gets it, and otherwise a company item carries it, unless an open item
    memory already holds is due that day (`known_dues`, the items the model was shown). The model leaves
    out an item it was shown, rightly: a session extracted again, or a customer who says the deadline again,
    must not get a second, generic item for it (B21)."""
    tz = ZoneInfo(timezone)
    out = list(items)
    for turn in turns:
        for deadline in deadlines_in(turn.text or "", turn.occurred_at or reference, timezone):
            # A day, as the model's own dates are: "até 06/10/2026 23:59" read like a time someone set.
            due = datetime.combine(deadline.day, time(), tzinfo=tz)
            # A model item due a day off (a time zone read differently) is the same deadline.
            if any(same_day(i.due_at, due) for i in out):
                continue
            undated = next(
                (k for k, i in enumerate(out) if i.due_at is None and turn.event_id in i.evidence_event_ids),
                None,
            )
            if undated is not None:
                out[undated] = replace(out[undated], due_at=due)
                continue
            if any(same_day(known, due) for known in known_dues):
                continue
            description = _DESCRIPTION[deadline.lang] + (f" ({subject})" if subject else "")
            out.append(OpenItemDraft(description, "company", (turn.event_id,), due_at=due))
    return out
