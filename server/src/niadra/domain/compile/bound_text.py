"""How a row about an object reads, the same on the pack's line and in the state block: an
instruction, an item with the date it is due by or the day it was kept, an observation beside the value the
system holds. One wording for both, in the language a pack is written in (`templates.language`); each view
writes the dates as it writes every other date. Pure.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from niadra.domain.memory.bound import BoundRow, own_words, without_due
from niadra.domain.vocabulary import OpenItemKind, Speaker

WORDS: dict[str, dict[str, str]] = {
    "pt": {
        "instruction": "instrução",
        "instruction_if": "instrução se quem fala for {who}",
        "not_others": "(Com qualquer outro contato, não se aplica.)",
        "contact_of": "preferência de contato de {who} (para quando a equipe procurar essa pessoa; não "
        "muda o que se pode responder a ela)",
        "contact_any": "preferência de contato (para quando a equipe fizer contato; não muda o que se pode "
        "responder)",
        "promise": "promessa",
        "dispute": "contestação",
        "request": "pedido",
        "by": "até",
        "late": "atrasada",
        "kept": "feito em {date}",
        "kept_due": "feito em {date}; data combinada {due}",
        "said": "dito",
        "system": "sistema",
        "team": "equipe",
        "owner": "cliente",
        "participant": "participante",
        "yes": "sim",
        "no": "não",
        "more_done": "mais {count} já feitas",
        "more_said": "mais {count} registros (consulte o histórico)",
    },
    "en": {
        "instruction": "instruction",
        "instruction_if": "instruction if the one speaking is {who}",
        "not_others": "(With any other contact, it does not apply.)",
        "contact_of": "contact preference of {who} (for when the team reaches out to this person; it does "
        "not change what may be said to them)",
        "contact_any": "contact preference (for when the team reaches out; it does not change what may be "
        "said)",
        "promise": "promise",
        "dispute": "disputed",
        "request": "requested",
        "by": "by",
        "late": "overdue",
        "kept": "done on {date}",
        "kept_due": "done on {date}; agreed date {due}",
        "said": "said",
        "system": "system",
        "team": "team",
        "owner": "customer",
        "participant": "participant",
        "yes": "yes",
        "no": "no",
        "more_done": "{count} more already done",
        "more_said": "{count} more records (see the history)",
    },
}


@dataclass(frozen=True, slots=True)
class Dates:
    """How the view writes the dates of a row: an item's due date, with its time when it names one (`due`),
    the day an item was kept (`kept`), when a row was said (`said`), and the day an instant falls on in the
    space's time zone (`day_of`), which is the due day an item's words may repeat."""

    due: Callable[[datetime], str]
    kept: Callable[[datetime], str]
    said: Callable[[datetime], str]
    day_of: Callable[[datetime], date]


def more_text(group: str, count: int, lang: str) -> str:
    """How a line says the rows of one group it leaves out (`domain.memory.bound.OnLine.more`): the kept
    items (`done`) and the observations (`said`)."""
    return WORDS.get(lang, WORDS["en"])[f"more_{group}"].format(count=count)


def row_text(row: BoundRow, lang: str, dates: Dates, *, account: bool = False) -> str:
    """One row as a line says it. An instruction about an account names no one (`account`): the contact's
    role, "(admin)", told an agent nothing."""
    words = WORDS.get(lang, WORDS["en"])
    if row.kind == "instruction":
        return _instruction(row, words, account=account)
    if row.kind == "item":
        return _item(row, words, dates)
    return _observation(row, words, dates)


def _instruction(row: BoundRow, words: Mapping[str, str], *, account: bool) -> str:
    if row.contact:
        # How to reach someone, never a limit on what may be said (`domain.signals.text.contact_line`).
        key = "contact_of" if row.only_with else "contact_any"
        return f"{words[key].format(who=row.only_with)}: {own_words(row.text, row.only_with)}"
    if row.only_with:
        # Required only if that person speaks: "não falar de valores com Clara" read as binding the client the
        # attendant talked with (juridico-sandbox, 05/10/2026).
        return f"{words['instruction_if'].format(who=row.only_with)}: {row.text} {words['not_others']}"
    said = f"{words['instruction']}: {row.text}"
    return said if account else f"{said} ({_who(row, words)})"


def _item(row: BoundRow, words: Mapping[str, str], dates: Dates) -> str:
    """An item names no owner nor participant: the handle it was noted under is not who promised it (a
    lawyer's promise is noted under the client's account), and its owner is the one of the conversation it
    was said in; only a named role (`lawyer`) is said. Its own due day is said beside it, never left inside
    its words (`without_due`), and a line never ends in a stray period."""
    text = (without_due(row.text, dates.day_of(row.due_at)) if row.due_at else row.text).rstrip(". ")
    if row.kept_at is not None:
        # Under the done items: the day after the words, so no status leads into another item. Its agreed
        # date is kept beside it and never called "prazo": without it "até quando?" was answered "não há
        # prazo" (Q2, round 9); as "prazo 06/10/2026; cumprido em 06/10/2026" it was read as the case's
        # deadline, due 13/10 (Q14, round 10). gpt-4.1-mini, 10 tries: Q14 7 and Q2 10 with this wording.
        kept_on = dates.kept(row.kept_at)
        done = (
            words["kept_due"].format(date=kept_on, due=dates.due(row.due_at))
            if row.due_at is not None
            else words["kept"].format(date=kept_on)
        )
        return f"{text} ({done})"
    # The date it is due by comes before its words: after them, "Avisar o cliente da intimação para
    # contestar, até 06/10/2026" was read as the deadline to contest (Q17: 1 of 10 right, 7 of 10 with the
    # date first, juridico-sandbox round 8). Past its due it says so after the date, which stays the due date
    # ("até quando?": 4 of 10 with "atrasada desde", 10 of 10 with "até 06/10/2026 (atrasada)").
    kind = words[(row.item_kind or OpenItemKind.PROMISE).value]
    late = f" ({words['late']})" if row.overdue else ""
    due = f" {words['by']} {dates.due(row.due_at)}{late}" if row.due_at is not None else ""
    named = f" ({row.role.replace('_', ' ')})" if row.role not in (None, "owner", "participant") else ""
    return f"{kind}{due}: {text}{named}"


def _observation(row: BoundRow, words: Mapping[str, str], dates: Dates) -> str:
    said = f"{row.text} ({_who(row, words)}, {dates.said(row.said_at)})"
    if row.observes is None:
        return f"{words['said']}: {said}"
    # The recorded value beside the said one, never replaced by it (2.4).
    recorded = "-" if row.recorded is None else _value(row.recorded, words)
    stamp = (
        f", {dates.said(row.recorded_at)}" if row.recorded_at is not None and row.recorded is not None else ""
    )
    field = row.observes.replace("_", " ")
    return f"{field}: {recorded} ({words['system']}{stamp}); {words['said']} {said}"


def _who(row: BoundRow, words: Mapping[str, str]) -> str:
    """Who said a row: the company's own person (`human_agent`) as the team, never the client (a lawyer's
    instruction read "(cliente)" on the client's case, juridico-sandbox, 05/10/2026), else their role on the
    object."""
    if row.speaker is Speaker.HUMAN_AGENT:
        return words["team"]
    role = row.role or "participant"
    return words[role] if role in ("owner", "participant") else role.replace("_", " ")


def _value(value: Any, words: Mapping[str, str]) -> str:
    if isinstance(value, bool):
        return words["yes"] if value else words["no"]
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)
