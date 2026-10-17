"""The intent gate (13, 6.4): turns that need nothing from memory, read by rules, without a model.

A customer turn made only of closed forms, in Portuguese, English or Spanish, asks memory for nothing:
thanks ("obrigado", "thanks a lot", "muchas gracias"), a short confirmation ("ok", "tá bom", "got it",
"vale"), a request to repeat ("pode repetir?", "say that again", "¿cómo?") and a greeting ("oi, bom dia",
"hello", "buenas tardes"), and any sequence of them ("ok, obrigado!"). Such a turn gets the pinned
skeleton alone: no slots and no prefetch, and the receipt says `intent: none`.

The rule is conservative on purpose: the whole turn must be closed forms, at most `MAX_WORDS` words, no
digit, no other word. "E a outra?", "sim, a de quinta" or "obrigado, e o protocolo?" are never gated: a
word the lists do not hold leaves the turn to the slots. Measurement counts the agent asking the customer
again after a gated turn, apart, so the lists are calibrated by what they cost.
"""

from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache

from niadra.domain.serve.turn import fold

MAX_WORDS = 8
"""A closed-form turn is short; a longer one says something the lists cannot vouch for."""


class ClosedForm(StrEnum):
    THANKS = "thanks"
    CONFIRMATION = "confirmation"
    REPEAT = "repeat"
    GREETING = "greeting"


NONE = "none"
"""The intent a gated turn records (`detail.intent` of the slots receipt)."""

_FORMS: dict[ClosedForm, tuple[str, ...]] = {
    ClosedForm.THANKS: (
        # Portuguese
        "obrigado", "obrigada", "obrigadao", "muito obrigado", "muito obrigada", "brigado", "brigada",
        "obg", "vlw", "valeu", "valeu mesmo", "agradeco", "grato", "grata",
        # English
        "thanks", "thank you", "thanks a lot", "thank you so much", "thank you very much", "thanks so much",
        "many thanks", "thx", "ty", "cheers",
        # Spanish
        "gracias", "muchas gracias", "mil gracias", "muchisimas gracias",
    ),
    ClosedForm.CONFIRMATION: (
        "ok", "okay", "okey", "certo", "ta certo", "esta certo", "ta bom", "esta bom", "ta", "beleza", "blz",
        "sim", "isso", "isso mesmo", "perfeito", "combinado", "entendi", "entendido", "show", "claro",
        "pode ser", "tudo bem", "tudo certo", "otimo", "maravilha", "fechado", "certinho",
        "yes", "yeah", "yep", "sure", "got it", "alright", "all right", "right", "great", "perfect",
        "sounds good", "understood", "fine", "cool", "okay then",
        "si", "vale", "de acuerdo", "perfecto", "listo", "dale", "bueno", "esta bien", "muy bien",
    ),
    ClosedForm.REPEAT: (
        "pode repetir", "poderia repetir", "repete", "repita", "repete por favor", "como", "como e",
        "o que", "oi o que", "nao entendi", "nao ouvi", "desculpa nao entendi", "fala de novo",
        "can you repeat", "could you repeat", "can you repeat that", "could you repeat that",
        "repeat that", "repeat please", "say that again", "say again", "sorry", "pardon", "come again",
        "what", "sorry what", "i did not hear", "i didn t catch that",
        "puede repetir", "podria repetir", "repite", "repita por favor", "perdon", "no entendi",
        "no escuche", "otra vez",
    ),
    ClosedForm.GREETING: (
        "oi", "ola", "bom dia", "boa tarde", "boa noite", "e ai", "alo", "oi tudo bem", "ola tudo bem",
        "tudo bem", "tudo bom", "hello", "hi", "hey", "good morning", "good afternoon", "good evening",
        "hi there", "hello there", "hola", "buenos dias", "buenas tardes", "buenas noches", "buenas",
        "que tal", "hola que tal",
    ),
}  # fmt: skip

_FILLERS = frozenset(
    {"entao", "then", "pues", "por favor", "please", "porfa", "pf", "pls", "ah", "ahn", "hum"}
)
"""Words that may sit between closed forms without making the turn say more."""


def _index() -> dict[tuple[str, ...], ClosedForm | None]:
    found: dict[tuple[str, ...], ClosedForm | None] = {}
    for form, phrases in _FORMS.items():
        for phrase in phrases:
            found.setdefault(tuple(phrase.split()), form)
    for filler in _FILLERS:
        found.setdefault(tuple(filler.split()), None)
    return found


_PHRASES = _index()
_LONGEST = max(len(p) for p in _PHRASES)


@dataclass(frozen=True, slots=True)
class Gate:
    """A gated turn: the closed forms it is made of, in order, never its words."""

    forms: tuple[ClosedForm, ...]

    def manifest(self) -> dict[str, object]:
        return {"intent": NONE, "forms": [f.value for f in dict.fromkeys(self.forms)]}


@lru_cache(maxsize=4096)
def gate(text: str | None) -> Gate | None:
    """The closed forms the whole turn is made of, or None when it may need memory. Same text, same
    answer: a cover of the words by the lists, tried longest phrase first, left to right."""
    if not text:
        return None
    words = tuple(fold(text).split())
    if not words or len(words) > MAX_WORDS or any(ch.isdigit() for w in words for ch in w):
        return None
    forms = _cover(words)
    return Gate(tuple(f for f in forms if f is not None)) if forms and any(forms) else None


def _cover(words: tuple[str, ...]) -> tuple[ClosedForm | None, ...] | None:
    """Every word inside a known phrase, or None; the forms of the phrases, fillers as None."""
    covered: list[tuple[ClosedForm | None, ...] | None] = [None] * (len(words) + 1)
    covered[len(words)] = ()
    for start in range(len(words) - 1, -1, -1):
        for size in range(min(_LONGEST, len(words) - start), 0, -1):
            phrase = words[start : start + size]
            rest = covered[start + size]
            if phrase in _PHRASES and rest is not None:
                covered[start] = (_PHRASES[phrase], *rest)
                break
    return covered[0]
