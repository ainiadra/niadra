"""The episodes written without the extraction model (5.7).

- A short session the decision model called answered on the spot (a general question the agent answered,
  a copy it sent: nothing left open, nothing learned about the customer) keeps its own turns as its summary,
  with the category the decision model gave it. Nothing in it can be wrong: it says what the turns say, and
  the settled values come from the deterministic net as for every session.
- A trivial session (greetings, thanks and confirmations only, `is_trivial`) keeps its own turns too: there
  is nothing to extract, and "content not extracted" said of it read as a failure on the agent's block, for a
  customer who only said hello (juridico-zero, 09/10/2026, final round).
- A session whose extraction failed gets a template that says only what the log proves (channel, size),
  which the nightly job replaces with a real extraction when the run is marked `failed`.
"""

from collections.abc import Sequence

from niadra.domain.extract.prompt import short_summary
from niadra.domain.extract.result import EpisodeDraft, ExtractionResult
from niadra.domain.records import EventRecord
from niadra.domain.vocabulary import EventKind, Speaker

MINIMAL_INTENT = "other"
MINIMAL_CATEGORY = "uncategorized"
MINIMAL_OUTCOME = "unknown"
ANSWERED_OUTCOME = "resolved"
ASKED_INTENT = "question"
REQUEST_INTENT = "request"

_TEMPLATES = {
    "pt": "Sessão em {channel} com {messages} mensagens e {others} eventos de sistema; "
    "conteúdo não extraído.",
    "es": "Sesión en {channel} con {messages} mensajes y {others} eventos de sistema; contenido no extraído.",
    "en": "Session on {channel} with {messages} messages and {others} system events; content not extracted.",
}


def minimal_result(channel: str, events: Sequence[EventRecord], language: str) -> ExtractionResult:
    messages = sum(1 for e in events if e.kind is EventKind.MESSAGE)
    template = _TEMPLATES.get(language.split("-")[0].lower(), _TEMPLATES["en"])
    summary = template.format(channel=channel, messages=messages, others=len(events) - messages)
    return ExtractionResult(
        episode=EpisodeDraft(
            summary=summary, intent=MINIMAL_INTENT, category=MINIMAL_CATEGORY, outcome=MINIMAL_OUTCOME
        )
    )


def trivial_result(events: Sequence[EventRecord], language: str, *, staff: bool = False) -> ExtractionResult:
    """A trivial session (`is_trivial`) told by its own turns: what the customer said, and when, with no
    category, intent or outcome a model would have had to guess."""
    return ExtractionResult(
        episode=EpisodeDraft(
            summary=short_summary(events, language, staff=staff),
            intent=MINIMAL_INTENT,
            category=MINIMAL_CATEGORY,
            outcome=MINIMAL_OUTCOME,
        )
    )


def answered_result(
    events: Sequence[EventRecord],
    language: str,
    category: str | None,
    intents: Sequence[str],
    outcomes: Sequence[str],
    *,
    staff: bool = False,
) -> ExtractionResult:
    """A short session answered on the spot (`prompt.is_short`): its turns, the decision model's category, a
    question when the customer only asked, and the outcome the answer gave it."""
    asked = [e.text or "" for e in events if e.speaker is Speaker.CUSTOMER and e.text]
    intent = ASKED_INTENT if asked and all(t.rstrip().endswith("?") for t in asked) else REQUEST_INTENT
    return ExtractionResult(
        episode=EpisodeDraft(
            summary=short_summary(events, language, staff=staff),
            intent=intent if intent in intents else MINIMAL_INTENT,
            category=category or MINIMAL_CATEGORY,
            outcome=ANSWERED_OUTCOME if ANSWERED_OUTCOME in outcomes else MINIMAL_OUTCOME,
        )
    )


# fmt: off
_TRIVIAL_WORDS = frozenset(
    {
        "oi", "ola", "olá", "bom", "boa", "dia", "tarde", "noite", "tudo", "bem", "obrigado", "obrigada",
        "valeu", "ok", "okay", "certo", "tchau", "até", "mais", "sim", "não", "nao", "teste", "testando",
        "hi", "hello", "hey", "thanks", "thank", "you", "bye", "good", "morning", "afternoon", "evening",
        "yes", "no", "test", "hola", "buenos", "buenas", "días", "dias", "gracias", "adiós", "adios",
        "sí", "si", "prueba",
    }
)
# fmt: on


def is_agent_task(kind: str, events: Sequence[EventRecord]) -> bool:
    """An internal agent's task that only acted (actions and system events, no message): no conversation with
    the customer, so no episode. Its actions are memory already; labeled as a conversation, it read as one
    the customer abandoned and crowded the customer's history."""
    return kind == "task" and bool(events) and all(e.kind is not EventKind.MESSAGE for e in events)


def is_trivial(events: Sequence[EventRecord]) -> bool:
    """Nothing to remember and no model to ask: only greetings, thanks and confirmations, no number."""
    said = [e.text or "" for e in events if e.kind is EventKind.MESSAGE and (e.text or "").strip()]
    if len(said) != len(events):
        return False  # an action or a system event always says something
    for text in said:
        if any(ch.isdigit() for ch in text):
            return False
        words = "".join(c if c.isalpha() else " " for c in text.lower()).split()
        if any(w not in _TRIVIAL_WORDS for w in words):
            return False
    return True
