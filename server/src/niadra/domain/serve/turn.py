"""What the customer's last turn asks of memory (11, 3.3), read by rules, without a model, in Portuguese,
English and Spanish: the identifiers it names, the kind of value it asks for ("the protocol", "o CEP",
"el comprobante"), whether it asks how many times, when, which or what happened, the channels it names,
its time words and anchors ("after the inspection"), and its content words.

The analysis only reads; it never guesses. A word it does not know leaves the turn to the lexical
channel, and the manifest records what the rules saw (types, kinds, counts), never the text.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from typing import Any

from niadra.domain.serve.relative_time import read_when
from niadra.domain.serve.search import tokenize

MAX_WORDS = 120
"""A turn is read up to this many words: the question is near its end or its start, never in a paste."""

VALUE_TYPES: dict[str, tuple[str, ...]] = {
    "protocol": ("protocol",),
    "ticket": ("chamado", "ticket", "ocorrenc", "incident", "incidenc"),
    "order": ("pedido", "order", "orden"),
    "record": ("registr", "record", "logged", "referenc"),
    "receipt": ("comprovante", "receipt", "recibo", "comprobante"),
    "postal_code": ("cep", "zip", "postal"),
    "amount": ("valor", "amount", "monto", "quantia"),
    "code": ("codigo", "code"),
    "claim": ("sinistro", "claim", "siniestro"),
    "invoice": ("fatura", "boleto", "invoice", "factura"),
    "tracking": ("rastrei", "tracking", "rastreo"),
    "address": ("endereco", "address", "direccion"),
    "document": ("documento", "document", "cpf", "dni", "passaporte", "passport"),
    "coupon": ("cupom", "coupon", "cupon"),
    "policy": ("apolice", "poliza"),
}
"""Value types by the stems that name them in the three languages (a word starting with a stem names the
type). Item texts are matched with the same stems, so "Registrei como 88280" answers "o registro"."""

_PHRASE_TYPES = (
    (re.compile(r"\b(?:zip|postal) code\b|\bcodigo postal\b"), "postal_code"),
    # How much something cost is an amount asked for: "quanto custou", "how much was", "what price".
    (
        re.compile(
            r"\b(?:quanto (?:custou|custa|custaram|paguei|pago|deu|foi|ficou|cobraram)|how much|what price|"
            r"which price|cuanto (?:costo|cuesta|pague|fue|cobraron)|que precio)\b|\bpreco\b|\bprice\b|"
            r"\bprecio\b|\bcusto\b|\bcost\b|\bcosto\b"
        ),
        "amount",
    ),
    (re.compile(r"\bcodigo de barras\b|\bbar ?code\b"), "code"),
    (re.compile(r"\bid number\b|\b(?:id|rg|ssn)\b"), "document"),
)


class QuestionKind(StrEnum):
    COUNT = "count"
    WHEN = "when"
    STATUS = "status"
    WHICH = "which"
    WHAT = "what"
    OTHER = "other"


_QUESTIONS = (
    (QuestionKind.COUNT, re.compile(r"\b(?:quant[ao]s|how many|how often|cuant[ao]s|number of times)\b")),
    (
        QuestionKind.WHEN,
        re.compile(
            r"\b(?:quando|when|cuando|que dia|what day|which day|que data|what date|que fecha|ate quando|"
            r"until when|hasta cuando|que horas|what time)\b"
        ),
    ),
    (
        QuestionKind.STATUS,
        re.compile(
            r"\b(?:ja (?:saiu|chegou|foi|caiu)|saiu|went through|come through|status|situacao|andamento|"
            r"estado del|ya (?:salio|llego))\b"
        ),
    ),
    (QuestionKind.WHICH, re.compile(r"\b(?:qual|quais|which|what is|what s|what was|cual|cuales)\b")),
    (QuestionKind.WHAT, re.compile(r"\b(?:o que|what|que)\b")),
)

_CHANNELS: dict[str, tuple[str, ...]] = {
    "email": ("email", "e mail", "correo", "mail", "emailed", "e mailed", "mailed"),
    "whatsapp": ("whatsapp", "zap", "wpp"),
    "voice": ("ligacao", "telefonema", "phone call", "llamada", "telefone", "telefono"),
    "app": ("app", "aplicativo", "aplicacion"),
    "sms": ("sms",),
    "chat": ("chat",),
}
# "desta ligação", "this call": the contact in progress, not one the customer refers to.
_CURRENT = re.compile(r"\b(?:dest[ae]|nest[ae]|est[ae]|this|esta)\s+$")

_ANCHOR = re.compile(
    r"\b(depois d[aoe]s?|apos(?: [ao]s?)?|after(?: the| my| our)?|antes d[aoe]s?|before(?: the| my| our)?|"
    r"desde (?:[ao]s?|que|el|la|los|las)|since(?: the| my| our)?|despues de(?:l| la| los| las)?|"
    r"antes de(?:l| la| los| las)?)\s+(?:(?:minha|meu|minhas|meus|my|mi|mis|su|sua|seu|nossa|nosso)\s+)?"
    r"([a-z]{3,})"
)
_ADJECTIVES = frozenset(
    {"new", "novo", "nova", "nuevo", "nueva", "last", "ultimo", "ultima", "first", "primeiro"}
)
_RELATIONS = (
    ("depois", "after"), ("apos", "after"), ("after", "after"), ("despues", "after"), ("since", "since"),
    ("desde", "since"), ("antes", "before"), ("before", "before"),
)  # fmt: skip

_SINGLE_TIME = frozenset(
    {
        "hoje", "ontem", "anteontem", "today", "yesterday", "hoy", "ayer", "anteayer", "antier", "recently",
        "lately", "recentemente", "ultimamente", "recientemente",
    }
)  # fmt: skip
_WEEKDAY_WORDS = frozenset(
    {
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "segunda", "terca",
        "quarta", "quinta", "sexta", "sabado", "domingo", "lunes", "martes", "miercoles", "jueves", "viernes",
    }
)  # fmt: skip
_WEEKDAY_BEFORE = frozenset({"na", "no", "on", "last", "el", "la", "past"})
_WEEKDAY_AFTER = frozenset({"passada", "passado", "pasada", "pasado", "anterior"})
_SHORT_MONTHS = frozenset(
    {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec", "fev", "abr", "mai",
     "set", "out", "dez", "ene", "dic", "sept", "marco"}
)  # fmt: skip
# Far ahead, so a month or a year the customer names is never "in the future" while the phrase is detected.
_DETECT_AT = datetime(2100, 12, 31, tzinfo=UTC)

_STOPWORD_TEXT = """
    a o as os um uma uns umas de da do das dos em no na nos nas por para pra pro com sem que se me mim meu
    minha meus minhas seu sua seus suas voce voces vcs eu ele ela eles elas nos isso isto esse essa este esta
    aquele aquela ao aos e ou mas mais muito muita ja nao sim foi era sao ser estar estou esta estava tem
    tenho tinha ter vou vai fiz fez faz qual quais quanto quantos quantas quando onde como porque por que oi
    ola bom boa dia tarde noite obrigado obrigada favor pode poderia gostaria queria quero saber sobre ainda
    tambem so lhe lhes vcs ai aqui la agora entao numero n desta deste nesta neste dessa desse vezes vez
    tudo bem ok okay tchau valeu blz beleza
    the an and or but of to in on at for with without from by is are was were be been am do does did have
    has had i me my mine you your yours we our us they their it its this that these those what which who
    when where how why can could would should will shall may might please hi hello thanks thank there here
    now just about again any some number no not yes many times time being
    el la los las un una unos unas del al y o pero que en con sin por para es son fue era soy estoy esta
    tengo tiene mi mis tu tus su sus usted ustedes nosotros yo le les lo como cuando donde cual cuales
    cuanto cuantos cuantas hola gracias ya numero veces vez
    disse dizer falei falar contei contar informei informar mencionei mencionar citei citar passei passaram
    passou lembro lembra precisa preciso precisam precisava deixar deixa deixe
    said say tell told mention mentioned give gave given need needs needed want wants wanted let remember
    according
    dije dijo decir conte conto informe mencione cite necesita necesito dejar deja segun recuerdo
    ate dias semana semanas mes meses ligando liguei ligar ligou causa escrevi escrever mandei mandar enviei
    enviar esses essas estes estas resposta responderam respondeu
    days day week weeks month months calling called call wrote write sent send reply replied answer because
    cause these those
    hasta semanas llamando llame llamar escribi escribir mande envie enviar respuesta respondieron porque
    causa estos estas
"""
# The last lines: how a customer points back at something they said ("que eu mencionei", "as I told you",
# "segun le dije"), how they say they got in touch ("mandei um e-mail esses dias", "I'm calling because")
# and verbs any request carries; never what the question is about.
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())


@dataclass(frozen=True, slots=True)
class Anchor:
    relation: str
    """`after`, `before` or `since`."""
    term: str
    """The folded word the customer anchored on ("vistoria", "delivery"); WP4 maps it to an event type."""


@dataclass(frozen=True, slots=True)
class TurnAnalysis:
    identifiers: tuple[str, ...] = ()
    """Tokens with three or more digits, as digits only: `45778-204` and `45778204` are one identifier."""
    asked_types: tuple[str, ...] = ()
    question: QuestionKind = QuestionKind.OTHER
    topic: tuple[str, ...] = ()
    """Content words, folded, in the order said, without stop words, time words or channel names."""
    time_phrases: tuple[str, ...] = ()
    anchors: tuple[Anchor, ...] = ()
    channels: tuple[str, ...] = ()
    folded: str = ""
    """The turn folded (`fold`), up to `MAX_WORDS` words, for phrases a lexicon reads across stop words; it
    never leaves the process (the manifest has kinds and counts only)."""

    @property
    def empty(self) -> bool:
        return not (self.identifiers or self.asked_types or self.topic or self.time_phrases or self.anchors)

    def manifest(self) -> dict[str, Any]:
        """What the rules saw, for the receipt: kinds and counts, never the customer's words or numbers."""
        return {
            "question": self.question.value,
            "asked_types": list(self.asked_types),
            "channels": list(self.channels),
            "identifiers": len(self.identifiers),
            "topic_terms": len(self.topic),
            "time_phrases": len(self.time_phrases),
            "anchors": [a.relation for a in self.anchors],
        }


def fold(text: str) -> str:
    """Lower case, no accents, words separated by single spaces; digits and letters only."""
    folded = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


_NOT_DIGIT = re.compile(r"\D+")


def digits(token: str) -> str:
    return _NOT_DIGIT.sub("", token)


PROXIMITY = 6
"""A value belongs to a type named at most this many words away: "o protocolo do seu atendimento é 81220"."""
_DATE = re.compile(r"^\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?$|^\d{4}-\d{2}-\d{2}$|^\d{1,2}[:h]\d{2}$")
_EDGES = ".,;:!?()[]{}\"'"


def _words(text: str) -> list[str]:
    # Accents off by decomposing and dropping what is not ASCII: the three languages spell in Latin letters.
    return unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode().split()


@lru_cache(maxsize=65_536)
def _value(word: str) -> str | None:
    """The digits of a word that is a value (three or more digits), or None; a date or a time is not one."""
    bare = word.strip(_EDGES)
    found = digits(bare)
    return found if len(found) >= 3 and not _DATE.match(bare) else None


TEXT_CACHE = 16_384
"""Lines seen by the process: a customer's lines come back on every turn of a conversation, so what the
rules read of a line is kept by its text (a few hundred bytes each)."""


@lru_cache(maxsize=TEXT_CACHE)
def identifiers_in(text: str) -> tuple[str, ...]:
    """Every value the text holds, as digits only, in the order said, without repeats."""
    return tuple(dict.fromkeys(v for w in _words(text) if (v := _value(w))))


@lru_cache(maxsize=TEXT_CACHE)
def _word_types(word: str) -> tuple[str, ...]:
    bare = re.sub(r"[^a-z0-9]", "", word)
    return tuple(name for name, stems in VALUE_TYPES.items() if bare and bare.startswith(stems))


def typed_values(text: str) -> dict[str, tuple[str, ...]]:
    """The values a text holds by the type named near them: "Registrei como 88280" -> {"record": ("88280",)}.
    Item texts go through the same stems a turn is read with."""
    return dict(_typed(text))


_CURRENCY = frozenset({"r$", "$", "us$", "u$s", "usd", "brl", "eur", "reais", "real", "dolares", "dollars"})
"""A value written with a currency mark is an amount: "R$ 131,26", "$51.90", "51,90 reais"."""


def _priced(words: list[str], i: int) -> bool:
    word = words[i]
    return (
        word.startswith(("r$", "$", "us$"))
        or (i > 0 and words[i - 1] in _CURRENCY)
        or (i + 1 < len(words) and words[i + 1].strip(_EDGES) in _CURRENCY)
    )


@lru_cache(maxsize=TEXT_CACHE)
def _typed(text: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    words = _words(text)
    values = [(i, v) for i, w in enumerate(words) if (v := _value(w))]
    if not values:
        return ()
    at: dict[str, list[int]] = {}
    for i, word in enumerate(words):
        for name in _word_types(word):
            at.setdefault(name, []).append(i)
    for i, _ in values:
        if _priced(words, i):
            at.setdefault("amount", []).append(i)
    found = []
    for name in VALUE_TYPES:
        near = [v for j, v in values if any(abs(i - j) <= PROXIMITY for i in at.get(name, ()))]
        if near:
            found.append((name, tuple(dict.fromkeys(near))))
    return tuple(found)


def types_in(folded: str) -> tuple[str, ...]:
    """The value types a folded text names, by the stems of `VALUE_TYPES`."""
    found = []
    for pattern, name in _PHRASE_TYPES:
        if pattern.search(folded):
            found.append(name)
            folded = pattern.sub(" ", folded)  # "zip code" is a postal code, not also a code
    words = folded.split()
    for name, stems in VALUE_TYPES.items():
        if name not in found and any(w.startswith(stem) for w in words for stem in stems):
            found.append(name)
    return tuple(found)


def _question(folded: str) -> QuestionKind:
    for kind, pattern in _QUESTIONS:
        if pattern.search(folded):
            return kind
    return QuestionKind.OTHER


def _channels(folded: str) -> tuple[str, ...]:
    found = []
    padded = f" {folded} "
    for channel, words in _CHANNELS.items():
        for word in words:
            for m in re.finditer(rf"(?<= ){re.escape(word)}(?= )", padded):
                if not _CURRENT.search(padded[: m.start()]):
                    found.append(channel)
                    break
            if channel in found:
                break
    return tuple(found)


def _anchors(folded: str) -> tuple[Anchor, ...]:
    found: list[Anchor] = []
    for m in _ANCHOR.finditer(folded):
        head = m.group(1).split()[0]
        relation = next((r for prefix, r in _RELATIONS if head.startswith(prefix)), None)
        term = m.group(2)
        if term in _ADJECTIVES:
            # "after the new card": the anchor is the noun after the adjective.
            rest = folded[m.end() :].split()
            term = rest[0] if rest else ""
        if relation and term and term not in _STOPWORDS and not _parses(term):
            found.append(Anchor(relation, term))
    return tuple(dict.fromkeys(found))


def _parses(phrase: str) -> bool:
    return read_when(phrase, _DETECT_AT, "UTC") is not None


def _plausible(words: list[str]) -> bool:
    """A phrase `read_when` accepts that is also a time phrase in a sentence, not a word that happens to
    parse: "segunda via" is not Monday, "out" is not October, "may I" is not May."""
    if len(words) == 1:
        return words[0] in _SINGLE_TIME
    last = words[-1]
    if any(w in _WEEKDAY_WORDS for w in words):
        return words[0] in _WEEKDAY_BEFORE or last in _WEEKDAY_AFTER or last == "feira"
    return not (last in _SHORT_MONTHS or (len(words) == 2 and words[0] in ("a", "o")))


def _time_phrases(words: list[str]) -> list[tuple[int, int]]:
    """Spans of the longest time phrases, left to right, never overlapping."""
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(words):
        for size in range(min(6, len(words) - i), 0, -1):
            chunk = words[i : i + size]
            if _plausible(chunk) and _parses(" ".join(chunk)):
                spans.append((i, i + size))
                i += size
                break
        else:
            i += 1
    return spans


def analyze(text: str | None) -> TurnAnalysis:
    if not text:
        return TurnAnalysis()
    folded = fold(text)
    words = folded.split()[:MAX_WORDS]
    folded = " ".join(words)
    spans = _time_phrases(words)
    timed = {i for start, end in spans for i in range(start, end)}
    channels = _channels(folded)
    channel_words = {w for c in channels for word in _CHANNELS[c] for w in word.split()}
    topic = [
        w
        for i, w in enumerate(tokenize(folded))
        if i not in timed and w not in _STOPWORDS and w not in channel_words and (len(w) > 2 or w.isdigit())
    ]
    return TurnAnalysis(
        identifiers=identifiers_in(text),
        asked_types=types_in(folded),
        question=_question(folded),
        topic=tuple(dict.fromkeys(topic)),
        time_phrases=tuple(" ".join(words[start:end]) for start, end in spans),
        anchors=_anchors(folded),
        channels=channels,
        folded=folded,
    )
