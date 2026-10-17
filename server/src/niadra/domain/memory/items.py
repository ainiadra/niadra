"""Open items: when something done in a system closes one (18.4) and when one is overdue (5.9)."""

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from niadra.domain.memory.facts import normalize_value
from niadra.domain.memory.ids import stable_uuid
from niadra.domain.memory.values import fold
from niadra.domain.objects.operations import acts_on_same, canonical_operation, same_operation, trusts
from niadra.domain.objects.written import distinctive
from niadra.domain.records import AgentAction, ObjectKey, OpenItem
from niadra.domain.vocabulary import ActionStatus, OpenItemStatus

CLOSING_WINDOW = timedelta(hours=72)
AMOUNT_WINDOW = timedelta(days=30)
"""How long after a promise a closing that names its amount may still fulfil it: a refund of the same
amount weeks later is that refund, while an operation alone matches only within `CLOSING_WINDOW`."""
OPEN_STATUSES = (OpenItemStatus.OPEN, OpenItemStatus.OVERDUE)
KEPT_WINDOW = timedelta(days=7)
"""How long a resolved promise stays in the pack as kept: the customer who calls about it days later hears
that it was done and when, next to the system's record of it."""
AMOUNT_FIELDS = ("amount", "value", "total", "valor", "monto", "importe")
SAME_DAY_SLACK = timedelta(days=1)
"""Two due dates this close are one deadline read in two time zones, not two deadlines (`same_day`)."""
CLOSED_BY = "closed_by"
"""The key of an item's `origin` that keeps what closed it: the `closed_by` its `open_item.closed` carried."""
_WORD = re.compile(r"[^\W\d_]{3,}")
_STEM = 5
_FUNCTION_WORDS = frozenset(
    {
        "ate", "aos", "apos", "antes", "depois", "para", "pela", "pelo", "pelas", "pelos", "sobre", "com",
        "sem", "dos", "das", "nos", "nas", "uma", "uns", "umas", "que", "seu", "sua", "seus", "suas",
        "este", "esta", "esse", "essa", "isso", "quando", "como", "mais", "del", "los", "las", "por",
        "hasta", "despues", "cuando", "the", "and", "for", "with", "from", "after", "before", "until",
        "when", "about", "into", "his", "her", "their", "your", "our",
    }
)  # fmt: skip
_DATE_WORDS = frozenset(
    {
        "segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo", "feira", "monday", "tuesday",
        "wednesday", "thursday", "friday", "saturday", "sunday", "lunes", "martes", "miercoles", "jueves",
        "viernes", "janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro",
        "outubro", "novembro", "dezembro", "january", "february", "march", "april", "may", "june", "july",
        "august", "september", "october", "november", "december", "enero", "febrero", "marzo", "mayo",
        "junio", "julio", "septiembre", "octubre", "noviembre", "diciembre", "hoje", "amanha", "ontem",
        "manha", "tarde", "noite", "today", "tomorrow", "yesterday", "morning", "afternoon", "evening",
        "tonight", "hoy", "manana", "ayer", "dia", "dias", "day", "days",
    }
)  # fmt: skip
"""When an item is due is its `due_at`: the words that say it ("até sexta-feira", "amanhã de manhã") never
tell two items apart."""


@dataclass(frozen=True, slots=True)
class Closing:
    """An agent action or a system-of-record event that may fulfil open items.

    `trusted` says the source may close items for this operation without confirmation (5.1);
    a system event is its own confirmation, so it arrives `confirmed`.
    """

    operation: str
    occurred_at: datetime
    status: ActionStatus
    object: ObjectKey | None = None
    item_id: UUID | None = None
    action_id: UUID | None = None
    trusted: bool = False
    amount: Decimal | None = None
    """The amount the action or the system event names (`fields.amount`), when it names one."""
    parents: tuple[ObjectKey, ...] = ()
    """The objects the closing's object belongs to, by its type's to-one relations: the filing names its case,
    an order line its order. An item about one of them closes as one about the object itself."""
    event_id: UUID | None = None
    """The system event that confirmed the operation, when an item found it already done at its creation."""
    strict: bool = False
    """A declared action of a customer-facing agent: it closes the item it names (`closes.item_id`), one whose
    words it says (`said`, see `says`), or one the decision model judged it does (`judged`), never by
    operation and object alone. The same operation on the same case is evidence from a system of record, not
    from what an attendant says it did."""
    said: str | None = None
    """What the action says it did (its `result`)."""

    def touches(self, key: ObjectKey) -> bool:
        return key == self.object or key in self.parents

    @classmethod
    def of_action(
        cls,
        action: AgentAction,
        key: ObjectKey | None,
        trusted_ops: Collection[str],
        *,
        customer_facing: bool = False,
    ) -> "Closing":
        return cls(
            operation=action.operation,
            occurred_at=action.occurred_at,
            status=action.status,
            object=key,
            item_id=action.closes_item_id,
            action_id=action.action_id,
            # A stored action keeps no reply to read: a customer-facing agent's is trusted only by a list.
            trusted=trusts(trusted_ops, action.operation, checked=not customer_facing),
            strict=customer_facing and action.status is ActionStatus.DECLARED,
            said=action.result,
        )


def recorded_words(label: str | None) -> str | None:
    """What a system event records, in words: its type with the separators as spaces ("custas.paid" is
    "custas paid", "filing.protocolled" is "filing protocolled"). It is what the closing says, so an item that
    waits for no operation can be judged against it (`to_judge`)."""
    if not label:
        return None
    words = " ".join(label.replace(".", " ").replace("_", " ").replace("-", " ").split())
    return words or None


def noted_at(item: OpenItem) -> datetime:
    """When the item was said; the closing window counts from it, while `created_at` is when we knew."""
    raw = item.origin.get("noted_at")
    return datetime.fromisoformat(raw) if isinstance(raw, str) else item.created_at


def item_id_for(
    origin_handle_id: UUID,
    session_id: str,
    description: str,
    due_at: datetime | None,
    object: ObjectKey | None,
) -> UUID:
    """An item's one id: whose it is, what it is (its content words), when it is due and what it is about.
    The same promise said again in other words ("sobre planta e termo", "sobre a planta e o termo") or in
    another conversation of the same person is the same item, by construction, while a due date or an object
    sets two apart. An item with neither is tied to nothing but the conversation that said it, so the
    conversation enters its id: "call the customer back" said again next week is a new promise. A text with
    no content word keeps its whole normalized form, so two short items never share an id."""
    words = " ".join(sorted(item_words(description))) or normalize_value(description)
    due = due_at.astimezone(UTC).isoformat() if due_at else ""
    about = f"{object.type}\x1f{object.namespace}\x1f{object.external_id}" if object else ""
    pinned = due or about
    return stable_uuid(str(origin_handle_id), words, due, about, "" if pinned else session_id)


def evidence_event_ids(item: OpenItem) -> tuple[UUID, ...]:
    """The events the item was extracted from; `origin` keeps them as text (JSONB)."""
    return tuple(UUID(str(e)) for e in item.origin.get("event_ids") or ())


def closes(
    item: OpenItem, closing: Closing, window: timedelta = CLOSING_WINDOW, *, judged: bool = False
) -> bool:
    """The closing names the item; or it says the item's words or the decision model judged it does them
    (`judged`), on what the item is about, at any time after it was said; or object and operation alone
    match in the window.

    A quarantined or divergent action closes nothing; a declared one only when its source trusts that
    operation (`trusts`). A customer-facing agent's declared action (`strict`) must say the item's words or
    be judged to do it (`to_judge` says when the model is asked).

    The window guards only the match that has no evidence but the operation: the same operation on the same
    case weeks later is another doing, while a closing that says the item's words, or that the model judged
    does it, does it whenever it comes. A promise due 06/10 done on 12/10 closes then, instead of reading
    "atrasada" forever. Evidence closes an item that waits for no operation, or for one of
    the closing's family (`fulfils_by`): with no declared vocabulary, an item is extracted before
    anyone recorded the operation that does it. An item about no object is the customer's own (the closing
    pass reads the customer's items only), and closes on that evidence too.
    """
    if not _may_close(item, closing):
        return False
    if closing.item_id is not None:
        return closing.item_id == item.item_id
    evidence = says(closing.said, item.description) or (judged and anchored(closing, item))
    if closing.strict and not evidence:
        return False
    if evidence and fulfils_by(item, closing, window):
        return True
    if _on_object(item, closing, window):
        return True
    return closes_by_amount(item, closing, window)


def to_judge(item: OpenItem, closing: Closing, window: timedelta = CLOSING_WINDOW) -> bool:
    """Whether the decision model is asked if a closing does the item: the closing could close it on
    evidence (`fulfils_by`), says one of its words (`anchored`) but not the act and the rest (`says`), and
    nothing closes it without the model: a customer-facing agent's action always needs the evidence, any
    other closing needs it unless object and operation alone match in the window. "Informar que a sentença
    foi julgada improcedente" does "comunicar a sentença ao cliente" in other words; "avisar o cliente sobre a
    verificação da sentença" says more of the item's words and does not do it, so words alone cannot tell
    them apart (juridico-sandbox, 05/10/2026). A system event says what it records (`Closing.said`, "custas
    paid"): "recolher as custas do preparo", noted before any event named its operation, is judged against
    it."""
    if closing.item_id is not None or not _may_close(item, closing):
        return False
    if not anchored(closing, item) or says(closing.said, item.description):
        return False
    if not fulfils_by(item, closing, window):
        return False
    return closing.strict or not _on_object(item, closing, window)


def fulfils_by(item: OpenItem, closing: Closing, window: timedelta = CLOSING_WINDOW) -> bool:
    """What a closing with evidence (the item's words, or the model's yes) may close: an item that waits for
    no operation, or for one of the closing's family (`same_family`: the model named "Recolher as custas do
    preparo" `pay`, and the system confirmed `pay_court_fees`), about the closing's object or about no
    object, said before the closing or at most `window` after it."""
    if item.expected_operation is not None and not same_family(item.expected_operation, closing.operation):
        return False
    if item.object is not None and not closing.touches(item.object):
        return False
    return closing.occurred_at >= noted_at(item) - window


def anchored(closing: Closing, item: OpenItem) -> bool:
    """The action says at least one of the item's words besides its act: what it is about, by its stem. A
    model's yes never closes "comunicar a sentença" on "comuniquei ao cliente", which names nothing of it."""
    words = [_stem(w) for w in _content_words(item.description)]
    if not closing.said or len(words) < 2:
        return False
    return bool(set(words[1:]) & {_stem(w) for w in _content_words(closing.said)})


def told_done(said: str | None, description: str) -> bool:
    """A conversation tells of the item being done: a turn says its act, done, and one more of its words, by
    stem. What a conversation resolves comes from the extraction model, which resolves by topic: Rafael's "vou
    comunicar a sentença ao cliente" resolved his own "avisar o cliente da audiência" on the same case
    (juridico-sandbox, 05/10/2026). "Já avisei o cliente da audiência" tells it; "comunicar a sentença" does
    not, and neither does the act said as still to come or as not done (`_not_yet`): "vou avisar o cliente da
    audiência", "aviso o cliente da audiência amanhã", "ainda não avisei o cliente da audiência"."""
    words = [_stem(w) for w in _content_words(description)]
    if not said or not words:
        return False
    tokens = _TOKEN.findall(fold(said))
    heard = {_stem(w) for w in _content_words(said)}
    act = words[0]
    done = [i for i, t in enumerate(tokens) if len(t) >= 3 and _stem(t) == act]
    told = any(not _not_yet(tokens, i, act) for i in done)
    return told and (len(words) == 1 or bool(set(words[1:]) & heard))


_TOKEN = re.compile(r"[^\W\d_]+")
_NOT_YET_BEFORE = frozenset(
    {
        "nao", "nunca", "jamais", "vou", "vamos", "vai", "vao", "irei", "iremos", "ira", "irao", "pretendo",
        "pretendemos", "estou", "estamos", "not", "never", "didnt", "havent", "hasnt", "dont", "wont",
        "will", "shall", "going", "gonna", "plan", "am", "are", "voy", "van", "estoy", "todavia",
    }
)  # fmt: skip
"""Words up to three before an act that say it is not done: negated ("ainda não avisei"), intended ("vou
avisar", "will send", "voy a enviar") or under way ("estou enviando")."""
_LATER = frozenset({"amanha", "tomorrow", "manana", "depois", "later", "luego"})
"""A word a few after an act in the present that puts it later ("envio amanhã", "aviso o cliente amanhã")."""
_PAST_ENDINGS = ("ei", "ou", "eu", "iu", "aram", "eram", "iram", "ed")
"""Past forms ("avisei", "enviou", "sent" aside, "emailed"): a later word after them is another act's."""
_FUTURE_ENDINGS = (
    "arei", "erei", "irei", "aremos", "eremos", "iremos", "arao", "erao", "irao", "ara", "era", "ira",
)  # fmt: skip
"""Portuguese future endings ("avisarei", "enviaremos", "avisará"), counted when the ending lies past the
act's stem: "espera" is the stem "esper" and one letter, not a future."""


def _not_yet(tokens: Sequence[str], at: int, act: str) -> bool:
    """Whether the act at `tokens[at]` is said as not done: negated, intended or under way just before it,
    put later a few words after it when it is not in the past, or conjugated in the future."""
    if _NOT_YET_BEFORE & set(tokens[max(0, at - 3) : at]):
        return True
    word = tokens[at]
    if not word.endswith(_PAST_ENDINGS) and _LATER & set(tokens[at + 1 : at + 6]):
        return True
    return any(word.endswith(e) and len(word) - len(act) >= len(e) for e in _FUTURE_ENDINGS)


def _may_close(item: OpenItem, closing: Closing) -> bool:
    if item.status not in OPEN_STATUSES:
        return False
    if closing.status in (ActionStatus.QUARANTINED, ActionStatus.DIVERGENT):
        return False
    return not (closing.status is ActionStatus.DECLARED and not closing.trusted)


def _on_object(item: OpenItem, closing: Closing, window: timedelta) -> bool:
    return _touches(item, closing, window) and same_operation(item.expected_operation, closing.operation)


def _touches(item: OpenItem, closing: Closing, window: timedelta) -> bool:
    """The closing is about the item's object, or one it belongs to, in the window."""
    return (
        item.object is not None
        and closing.touches(item.object)
        and in_window(item, closing.occurred_at, window)
    )


def says(said: str | None, description: str) -> bool:
    """`said` names what `description` asks to be done: its first content word, the act, and every other one
    but one, each by its stem, so a conjugation or a noun of the same verb still counts ("réplica
    protocolada" says "protocolar a réplica", "aviso da intimação para contestar" says "avisará o cliente da
    intimação para contestar", leaving out whom the customer-facing agent was talking to) while another act
    does not ("avisar o cliente sobre a verificação da sentença" is not "comunicar a sentença ao cliente").
    An item of one or two content words is said only whole."""
    words = [_stem(w) for w in _content_words(description)]
    if not said or not words:
        return False
    heard = {_stem(w) for w in _content_words(said)}
    act, rest = words[0], set(words[1:])
    return act in heard and len(rest - heard) <= (1 if len(rest) >= 2 else 0)


def _stem(word: str) -> str:
    """The first five letters, less a closing vowel: "avisar", "avisará" and "aviso" are one verb, while
    "contestar" and "contrato" stay apart."""
    head = word[:_STEM]
    return head[:-1] if len(head) == _STEM and head[-1] in "aeiou" else head


def stems(text: str) -> frozenset[str]:
    """The stems of a text's content words: what two wordings of one thing to be done share."""
    return frozenset(_stem(w) for w in _content_words(text))


def in_window(item: OpenItem, at: datetime, window: timedelta = CLOSING_WINDOW) -> bool:
    """From `window` before the item was said to `window` after it, or after its due date when that is later:
    a deadline days ahead ("file the reply by the 9th") is met days ahead."""
    noted = noted_at(item)
    end = max(noted, item.due_at) if item.due_at is not None else noted
    return noted - window <= at <= end + window


def closes_by_amount(item: OpenItem, closing: Closing, window: timedelta = CLOSING_WINDOW) -> bool:
    """A promise closed by what was done and how much (17, WP-10): "refund the R$ 41,78 shipping fee" waits
    for `refund_fee` and the ERP records `order.refunded` for 41.78 on an order the promise never named.
    The same amount the promise states, an operation of the same family (`refund_fee` is a `refund`) and
    the object, when the promise names one, close it within `AMOUNT_WINDOW` after it was said. Without an
    amount on both sides, or without the operation the promise waits for, nothing closes."""
    if closing.amount is None or item.expected_operation is None:
        return False
    if item.object is not None and not closing.touches(item.object):
        return False
    if not same_family(item.expected_operation, closing.operation):
        return False
    noted = noted_at(item)
    if not (noted - window <= closing.occurred_at <= noted + AMOUNT_WINDOW):
        return False
    return closing.amount in amounts_in(item.description)


def same_family(a: str | None, b: str | None) -> bool:
    """`refund_fee` and `refund`, `cancel_subscription` and `cancel`: one verb, whatever it is done to."""
    left, right = canonical_operation(a), canonical_operation(b)
    if left is None or right is None:
        return False
    return left == right or left.split("_", 1)[0] == right.split("_", 1)[0]


_MONEY = re.compile(
    r"(?:r\$|us\$|\$|€|£|usd|brl|eur)\s*(\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
    r"|(\d{1,3}(?:[.,]\d{3})*[.,]\d{2}|\d+[.,]\d{2})(?!\d)",
    re.IGNORECASE,
)


def amounts_in(text: str) -> frozenset[Decimal]:
    """The amounts a text states: "R$ 41,78", "$284.85", "1.234,56 reais", "US$ 1,234.56"; a bare number
    counts only with two decimals, so a protocol number is never an amount."""
    return frozenset(written_amounts(text))


def written_amounts(text: str, *, marked: bool = False) -> dict[Decimal, str]:
    """Each amount a text states (`amounts_in`) and how it first writes it: "R$ 41,78". `marked`: only those
    written with a currency, never a bare number with two decimals, which a case number holds ("8.26" in
    "1021234-23.2026.8.26.0224")."""
    found: dict[Decimal, str] = {}
    for m in _MONEY.finditer(text):
        if marked and m.group(1) is None:
            continue
        if (value := parse_amount(m.group(1) or m.group(2))) is not None:
            found.setdefault(value, m.group(0).strip())
    return found


def parse_amount(raw: Any) -> Decimal | None:
    """41.78, "41,78", "1.234,56" and "1,234.56" as a Decimal of cents; None for anything else."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        text = f"{raw:.2f}"
    elif isinstance(raw, str):
        text = raw.strip().replace(" ", "")
        if "," in text and "." in text:
            decimal_mark = "," if text.rfind(",") > text.rfind(".") else "."
            thousands = "." if decimal_mark == "," else ","
            text = text.replace(thousands, "").replace(decimal_mark, ".")
        elif "," in text:
            head, _, tail = text.rpartition(",")
            text = f"{head.replace(',', '')}.{tail}" if len(tail) <= 2 else text.replace(",", "")
        elif text.count(".") == 1 and len(text.rpartition(".")[2]) == 3:
            text = text.replace(".", "")  # "1.234" is a thousand, not one and a fraction
    else:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value.quantize(Decimal("0.01")) if value.is_finite() and value > 0 else None


def amount_of(fields: Mapping[str, Any] | None) -> Decimal | None:
    """The amount a system event or an action names in its fields, when it names one."""
    for name in AMOUNT_FIELDS:
        if fields and (value := parse_amount(fields.get(name))) is not None:
            return value
    return None


def is_overdue(item: OpenItem, now: datetime, timezone: str = "UTC") -> bool:
    """Past the moment its due date ends. A due date with no time of day ("até 03/10", kept as the start of
    that day in the space's time zone) runs until that day ends there, never from its first minute."""
    if item.status is not OpenItemStatus.OPEN or item.due_at is None:
        return False
    return due_end(item.due_at, timezone) < now


def due_end(due_at: datetime, timezone: str = "UTC") -> datetime:
    """When a due date is past: its instant, or the end of its day when it names no time of day."""
    local = due_at.astimezone(ZoneInfo(timezone))
    if local.time() != time.min:
        return due_at
    return (local + timedelta(days=1)).astimezone(UTC)


def ended_newest_first(items: Collection[OpenItem]) -> list[OpenItem]:
    """Items no longer open, the one closed last first: what history lists when asked for them."""
    return sorted(items, key=lambda i: (i.closed_at or i.created_at, i.item_id), reverse=True)


def kept_sessions(items: Collection[OpenItem]) -> tuple[tuple[str, datetime], ...]:
    """(session id, when the last of its promises was resolved) for each session that left a resolved one."""
    last: dict[str, datetime] = {}
    for item in items:
        session = item.origin.get("session_id")
        if item.status is OpenItemStatus.RESOLVED and item.closed_at is not None and session:
            key = str(session)
            last[key] = max(last.get(key, item.closed_at), item.closed_at)
    return tuple(sorted(last.items()))


def with_closed_by(item: OpenItem, closed_by: Mapping[str, Any] | None) -> OpenItem:
    """`item` keeping in its origin what closed it; with None, as when it reopens, keeping nothing."""
    origin = {k: v for k, v in item.origin.items() if k != CLOSED_BY}
    if closed_by is not None:
        origin[CLOSED_BY] = dict(closed_by)
    return replace(item, origin=origin)


def closed_by_of(item: OpenItem) -> dict[str, Any] | None:
    """What closed a resolved item, as kept when it closed."""
    kept = item.origin.get(CLOSED_BY)
    return dict(kept) if item.status not in OPEN_STATUSES and isinstance(kept, Mapping) else None


def recently_kept(items: Collection[OpenItem], now: datetime) -> list[OpenItem]:
    """Promises resolved within `KEPT_WINDOW` before `now`, newest first."""
    kept = [
        i
        for i in items
        if i.status is OpenItemStatus.RESOLVED
        and i.closed_at is not None
        and now - KEPT_WINDOW <= i.closed_at <= now
    ]
    return sorted(kept, key=lambda i: (i.closed_at, i.item_id), reverse=True)


def item_words(text: str) -> frozenset[str]:
    """The words that say what an item is: accents and case folded, without numbers (a date, an amount) or
    the short and function words a rewording adds or drops."""
    return frozenset(_content_words(text))


def _content_words(text: str) -> list[str]:
    """`item_words` in the order the text says them."""
    return [w for w in _WORD.findall(fold(text)) if w not in _FUNCTION_WORDS and w not in _DATE_WORDS]


def restates(
    description: str,
    evidence: Sequence[UUID],
    due_at: datetime | None,
    object: ObjectKey | None,
    item: OpenItem,
) -> bool:
    """Whether a new item of a session says again an item an earlier extraction of the same session wrote
    (B21): they rest on a turn in common, every word of the shorter text is in the longer one ("Protocolar
    a réplica." and "Protocolar a réplica até sexta-feira"), and no due date or object sets them apart.
    Two items of one turn that differ in what they are about ("enviar a planta", "enviar o habite-se")
    never restate each other: merging two promises would lose one, while a missed match only leaves both."""
    if not set(evidence) & set(evidence_event_ids(item)):
        return False
    if days_apart(due_at, item.due_at):
        return False
    if object is not None and item.object is not None and object != item.object:
        return False
    return _same_words(description, item.description)


def restates_elsewhere(
    description: str, due_at: datetime | None, object: ObjectKey | None, item: OpenItem
) -> bool:
    """Whether a new item says again a promise of the same person from another conversation, as a conversation
    that goes over what is still pending does (juridico-sandbox, 03/10/2026). Across conversations a word more
    can be another promise ("manifestação sobre o termo de entrega" and "manifestação sobre planta e termo de
    entrega" are two filings), so the words must be the same, dates aside, and the items tied by more: the
    same object, with no due dates more than a day apart; or, when either names no object, due dates a day
    apart at most."""
    mine, theirs = item_words(description), item_words(item.description)
    if not mine or mine != theirs:
        return False
    if object is not None and item.object is not None:
        return object == item.object and not days_apart(due_at, item.due_at)
    return same_day(due_at, item.due_at)


NEAR_STEMS = 3
"""The stems the shorter of two dated items must say for one word of it to differ (`restates_on_object`)."""
NEAR_DIFFERENT = 1
"""How many stems of the shorter of two dated items on an object may be missing from the longer one."""


def _without_object_names(text: str) -> str:
    """The text without an identifier written in it and the word that names it ("do processo
    1013456-86.2026.8.26.0002", "order 48213"): two items on one object compared for a retelling are about
    that object already, and the one that names it again says one stem more than the one that does not. Only
    a token `distinctive` calls an identifier (digits, a length no ordinary word has)."""
    tokens = text.split()
    drop: set[int] = set()
    for n, token in enumerate(tokens):
        if distinctive(token.strip(".,;:()[]")):
            drop.add(n)
            if n and tokens[n - 1].isalpha():
                drop.add(n - 1)
    return " ".join(t for n, t in enumerate(tokens) if n not in drop)


def restates_on_object(
    description: str,
    due_at: datetime | None,
    object: ObjectKey | None,
    operation: str | None,
    item: OpenItem,
) -> bool:
    """Whether a new item says again an open item another conversation bound to the same object: the firm's
    attendant tells the client "we will send you the copy of the decision tomorrow", which the lawyer
    promised on the case (juridico-sandbox, 05/10/2026). It is that promise told, not one of its own: the
    same object, the same operation, due dates no more than a day apart when both have one, and every stem
    of the shorter text among the longer one's ("enviaremos a cópia da decisão" and "enviar ao cliente a
    cópia da decisão").

    Both dated for the same day, one word may differ (`NEAR_DIFFERENT`) when the shorter says at least
    `NEAR_STEMS`: "Enviar cópia da sentença ao cliente" and "Enviar ao cliente a cópia da decisão", both
    `deliver_document` on the case by 06/10, were two promises on the client's line (juridico-sandbox,
    05/10/2026). Two things to send on one case say two words apart at least ("enviar o comprovante de
    pagamento" is not "enviar a cópia da decisão"), and two words alone ("enviar a planta", "enviar o
    habite-se") never pass.

    With no configuration, an item said before any operation was observed waits for none, and so may the
    agent's retelling of it: "Enviar comprovante de pagamento ao financeiro do cliente" (the lawyer's) and
    "Enviar o comprovante de pagamento ao financeiro do cliente" (the attendant's), both on the case by
    13/10 and waiting for no operation, were two promises (juridico-zero, 08/10/2026). When either side
    waits for none, nothing but the object and the words tie them, so the words must be the same ones and
    say at least `NEAR_STEMS`: a word more is another thing to do ("protocolar manifestação sobre planta e
    termo de entrega" is not "sobre o termo de entrega"), and the decision model judges what the words do not
    settle. Two operations both known and apart never tie."""
    if object is None or item.object != object or item.status not in OPEN_STATUSES:
        return False
    both = operation is not None and item.expected_operation is not None
    if both and not same_operation(operation, item.expected_operation):
        return False
    if days_apart(due_at, item.due_at):
        return False
    mine = stems(_without_object_names(description))
    theirs = stems(_without_object_names(item.description))
    if not mine or not theirs:
        return False
    shorter, longer = sorted((mine, theirs), key=len)
    if not both:
        return len(mine) >= NEAR_STEMS and mine == theirs
    if shorter <= longer:
        return True
    dated = due_at is not None and item.due_at is not None
    return dated and len(shorter) >= NEAR_STEMS and len(shorter - longer) <= NEAR_DIFFERENT


def to_judge_retelling(
    description: str,
    due_at: datetime | None,
    object: ObjectKey | None,
    operation: str | None,
    item: OpenItem,
    *,
    relayed: bool = False,
) -> bool:
    """Whether the decision model is asked if a new item tells again an open item another conversation bound
    to the same object, when the words alone do not settle it (`restates_on_object`): the same object and
    operation, due dates a day apart at most when both have one, and the new item says one of the open item's
    words besides its act. "Enviar ao financeiro comprovante do pagamento das custas" told the lawyer's
    "enviar comprovante do pagamento ao financeiro do cliente" and opened a second promise on the case, and
    "enviar cópia da sentença", undated, the lawyer's "enviar ao cliente a cópia da decisão"
    (juridico-sandbox, 06/10/2026).

    The operations may differ when the model is asked (`retold_operation`): with no configuration, items said
    before an operation was observed wait for none or for the nearest one known then, and the attendant's
    "providenciar e enviar a cópia da decisão da sentença" (`deliver_document`) opened beside the lawyer's
    "enviar ao cliente a cópia da decisão" (`attach_document`) on the same case (juridico-zero, 07/10/2026).
    Measured in production, the question keeps them apart where it should: 0.68 to 0.89 for the same thing
    told again, 0.08 at most for "juntar o comprovante aos autos" and "enviar o comprovante ao financeiro".

    An item an agent talking with the customer said (`relayed`) is asked whatever its due: asked until when
    to send the extratos the lawyer had asked for by 06/10, the attendant answered "até 27/10", the case's
    deadline to contest, and a second request was opened due on the 27th (juridico-zero, 07/10/2026). Judged
    the same, the open item keeps its own day."""
    if object is None or item.object != object or item.status not in OPEN_STATUSES:
        return False
    if not retold_operation(operation, item.expected_operation):
        return False
    if not relayed and days_apart(due_at, item.due_at):
        return False
    if restates_on_object(description, due_at, object, operation, item):
        return False
    theirs = [_stem(w) for w in _content_words(_without_object_names(item.description))]
    mine = stems(_without_object_names(description))
    return len(theirs) >= 2 and bool(set(theirs[1:]) & mine)


def to_judge_again(
    description: str,
    evidence: Sequence[UUID],
    due_at: datetime | None,
    object: ObjectKey | None,
    operation: str | None,
    item: OpenItem,
) -> bool:
    """Whether the decision model is asked if a new item of a session extracted again is an item an earlier
    run of that session wrote, from the same turns, in other words (`restates` settles the same words): a
    conversation nobody ends is extracted once it goes quiet and again when a turn reopens it,
    and the second run wrote "Enviar os extratos do SAC ao cliente" for the first run's "Mandar os extratos
    ao cliente". They rest on a turn in common, their objects and dates do not set them apart, the operations
    may be one (`retold_operation`), and the new item says one of the earlier one's words besides its act.
    Two promises of one turn ("vou mandar os extratos e ligar amanhã") are the model's to tell apart."""
    if item.status not in OPEN_STATUSES or not set(evidence) & set(evidence_event_ids(item)):
        return False
    if object is not None and item.object is not None and object != item.object:
        return False
    if days_apart(due_at, item.due_at) or not retold_operation(operation, item.expected_operation):
        return False
    if restates(description, evidence, due_at, object, item):
        return False
    theirs = [_stem(w) for w in _content_words(item.description)]
    return len(theirs) >= 2 and bool(set(theirs[1:]) & stems(description))


def retold_operation(new: str | None, open_item: str | None) -> bool:
    """Whether a new item's operation may be the one an open item waits for, told again: the same, of one
    family (`same_family`), done to the same kind of thing (`acts_on_same`), or either side waiting for none
    (`fulfils_by` closes such items on their words too; with no configuration, items said before an operation
    was observed wait for none)."""
    if new is None or open_item is None:
        return True
    return same_operation(new, open_item) or same_family(new, open_item) or acts_on_same(new, open_item)


def same_day(a: datetime | None, b: datetime | None) -> bool:
    """Two due dates, both given, that are one deadline (`SAME_DAY_SLACK`)."""
    return a is not None and b is not None and abs(a - b) <= SAME_DAY_SLACK


def days_apart(a: datetime | None, b: datetime | None) -> bool:
    """Two due dates, both given, that are two deadlines: one missing sets nothing apart."""
    return a is not None and b is not None and abs(a - b) > SAME_DAY_SLACK


def _same_words(a: str, b: str) -> bool:
    mine, theirs = item_words(a), item_words(b)
    if not mine or not theirs:
        return mine == theirs and fold(a).strip() == fold(b).strip()
    shorter, longer = sorted((mine, theirs), key=len)
    return shorter <= longer
