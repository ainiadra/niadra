"""What memory never keeps from an extraction, whatever the model wrote.

The measurement of 26/09 found the extraction models copying document numbers into fact values ("billing_issue
= 87798164") and summaries, and filing a diagnosis under an ordinary predicate. The validation checked only
the settled values. Here every text the extraction can store goes through the same rules as the settled
values and the ingest boundary, after the validation and before anything is written:

- a document, card or bank account number, a secret, a card's security code or a password becomes a typed
  marker, like `[retido:documento]`: found by its own words in the text itself (`values.withheld_numbers`,
  `masking.mask_text`), or because the session's turns said what the number is (a number said as "my ID
  number" is withheld wherever the model repeats it, even alone), or because the PII model redacted it as
  one (the placeholder comes back as a marker, never as the value);
- a fact, profile slot, promise or settled value that is only such a number is dropped and counted;
- content of a sensitive category (health, religion, politics, sexual life, union, biometrics, genetics,
  origin, criminal record) under an ordinary predicate moves to a sensitive predicate of the same category
  when the schema has one, and is dropped when it has none: the schema says what sensitive data the
  space collects, and never as ordinary data. The conversation's episode then goes under that category
  too, so its summary and promises follow the category's policy (`config/starter.py`); an episode is
  never refiled by its words alone, which name health plans and hospitals in ordinary billing. Which
  category a text speaks of is the caller's `topics`: a new extraction asks the decision model
  (`sensitive_candidates`, `label_topics`), the pass over stored rows reads the word lists.

Protocol, order and ticket numbers, postal codes, amounts and dates are untouched. Pure and idempotent:
what was withheld stays a marker, and a marker is never withheld again.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from niadra.domain.extract.result import (
    AgentClaim,
    EpisodeDraft,
    ExtractionResult,
    FactCommand,
    FactOp,
    InstructionDraft,
    OpenItemDraft,
    SlotDraft,
    ValueDraft,
)
from niadra.domain.extract.rules import ExtractionRules, PredicateRule
from niadra.domain.ingest.masking import Held, marker, mask_text
from niadra.domain.memory.values import MARKER, card_number, fold, label_word, withheld_numbers
from niadra.domain.policy.sensitive import is_product_sensitive, sensitive_topics, topic_of
from niadra.domain.vocabulary import Sensitivity


class Topics(Protocol):
    def __call__(self, text: str, /) -> Sequence[str]:
        """The sensitive categories a text speaks of, by the product's names (`health`, ...); empty for
        none."""
        ...


UNKNOWN_TOPIC = "unclassified"
"""A sensitive category the decision model could not name with confidence: the text is kept out of ordinary
memory, and the episode keeps its category."""

LABEL_TOPICS: Mapping[str, tuple[str, ...]] = {
    "health": ("health",),
    "religion": ("religion", "philosophical_belief"),
    "politics": ("political_opinion",),
    "union": ("union_membership",),
    "sexual_orientation": ("sexual_orientation", "sexual_life"),
    "ethnicity": ("racial_or_ethnic_origin",),
    "genetic_biometric": ("genetic", "biometric"),
    "criminal_record": ("criminal_record",),
}


def label_topics(label: str) -> tuple[str, ...]:
    """The product's categories for a `sensitive_category` decision; any label but `none` is sensitive."""
    if label == "none":
        return ()
    return LABEL_TOPICS.get(label, (UNKNOWN_TOPIC,))


def no_topics(_: str) -> tuple[str, ...]:
    return ()


WITHHOLD_VERSION = 1
"""Bumped when a rule withholds more: what memory stored before is scrubbed again (`app/withhold.py`)."""

MIN_HELD_DIGITS = 5
"""A number the session said is a document is withheld wherever it appears only from five digits: shorter
ones would collide with amounts and years, and their own words still withhold them."""

_LABELS: dict[str, tuple[str, dict[str, str]]] = {
    "pt": ("retido", {"document": "documento", "account": "conta"}),
    "en": ("withheld", {"document": "document", "account": "account"}),
    "es": ("retenido", {"document": "documento", "account": "cuenta"}),
}
_DIGIT_RUN = re.compile(r"(?<!\d)\d(?:[.\-/]?\d)*(?!\d)")
_LETTERS = re.compile(r"[^\W\d_]+")
# Words that only say whose number it was or that it was given.
_FILLER = frozenset(
    {
        "numero", "number", "num", "no", "nr", "n", "de", "do", "da", "dos", "das", "del", "of", "the", "o",
        "a", "os", "as", "el", "la", "los", "las", "my", "meu", "minha", "mi", "seu", "sua", "su", "your",
        "his", "her", "their", "is", "e", "es", "era", "was", "for", "para", "por", "and", "y", "ou", "or",
        "cliente", "customer", "client", "titular", "holder", "receiver", "recebedor", "destinatario",
        "recipient", "informado", "informada", "informou", "provided", "given", "gave", "novo", "nova",
        "new", "atualizado", "atualizada", "updated", "s",
    }
)  # fmt: skip


def marker_for(kind: str, lang: str = "pt", last4: str | None = None) -> str:
    """The marker left where a number of `kind` was: `[retido:documento]`, `[withheld:account]`, and the
    ingest boundary's own for a card and a secret."""
    if kind == "card":
        return marker(Held.CARD, lang, last4)
    if kind == "secret":
        return marker(Held.SECRET, lang)
    word, labels = _LABELS.get(lang, _LABELS["en"])
    return f"[{word}:{labels.get(kind, labels['document'])}]"


def held_numbers(texts: Iterable[str], redacted: Mapping[str, str] | None = None) -> dict[str, str]:
    """The digits of every number the session's own turns say memory must not keep, with what each is,
    plus the values the PII model redacted as a document, card or account (`Redactor.withheld`)."""
    found: dict[str, str] = {}
    for text in texts:
        for number in withheld_numbers(text):
            digits = _digits(number.raw)
            if len(digits) >= MIN_HELD_DIGITS:
                found.setdefault(digits, number.kind)
    for value, kind in (redacted or {}).items():
        digits = _digits(value)
        if len(digits) >= MIN_HELD_DIGITS:
            found.setdefault(digits, kind)
    return found


@dataclass(frozen=True, slots=True)
class Scrubbed:
    text: str
    withheld: int = 0


def withhold_text(text: str, held: Mapping[str, str] | None = None, lang: str = "pt") -> Scrubbed:
    """The text with every number memory must not keep replaced by its marker, and how many were."""
    if not text or (not any(ch.isdigit() for ch in text) and not _maybe_secret(text)):
        return Scrubbed(text)
    masked = mask_text(text, lang)
    out, count = masked.text, len(masked.held)
    spans = [(w.start, w.end, w.kind, w.raw) for w in withheld_numbers(out)]
    if held:
        taken = [(s, e) for s, e, _, _ in spans] + [(m.start(), m.end()) for m in MARKER.finditer(out)]
        for run in _DIGIT_RUN.finditer(out):
            if any(run.start() < e and s < run.end() for s, e in taken):
                continue
            kind = _held_kind(_digits(run.group(0)), held)
            if kind is not None:
                spans.append((run.start(), run.end(), kind, run.group(0)))
    for start, end, kind, raw in sorted(spans, reverse=True):
        digits = _digits(raw)
        last4 = digits[-4:] if kind == "card" and card_number(digits) else None
        out = out[:start] + marker_for(kind, lang, last4) + out[end:]
        count += 1
    return Scrubbed(out, count)


def only_withheld(text: str) -> bool:
    """Nothing left but markers and the words that name them ("CPF [retido:documento]"): a fact, slot or
    promise like that is only a document number, and memory drops it."""
    if not MARKER.search(text):
        return False
    rest = fold(MARKER.sub(" ", text))
    if any(ch.isdigit() for ch in rest):
        return False
    return all(w in _FILLER or label_word(w) for w in _LETTERS.findall(rest))


def sensitive_home(
    rules: ExtractionRules, topics: Sequence[str], value: str | None = None
) -> PredicateRule | None:
    """The schema's sensitive predicate for one of `topics`, if the space collects that category at all."""
    for predicate in rules.predicates:
        if predicate.sensitivity is not Sensitivity.SENSITIVE or topic_of(predicate.category) not in topics:
            continue
        if predicate.enum is not None and value not in predicate.enum:
            continue
        return predicate
    return None


def misfiled(
    value: str, sensitivity: Sensitivity, category: str, topics: Topics = sensitive_topics
) -> tuple[str, ...]:
    """The sensitive categories a value under an ordinary predicate speaks of; empty when it is filed
    where it belongs."""
    if not ordinary(sensitivity, category):
        return ()
    return tuple(topics(value))


def ordinary(sensitivity: Sensitivity, category: str) -> bool:
    return sensitivity is not Sensitivity.SENSITIVE and not is_product_sensitive(category)


def sensitive_candidates(result: ExtractionResult, rules: ExtractionRules) -> list[str]:
    """The texts whose sensitive category decides where they are stored: fact values added under an ordinary
    predicate, profile slot values and agent claims about an ordinary predicate, once each."""
    texts = [f.value for f in result.facts if f.op is FactOp.ADD and ordinary(f.sensitivity, f.category)]
    texts += [s.value for s in result.profile]
    for claim in result.agent_claims:
        predicate = rules.predicate(claim.predicate) if claim.predicate else None
        if predicate is not None and ordinary(predicate.sensitivity, predicate.category):
            texts.append(claim.value)
    return list(dict.fromkeys(t for t in texts if t.strip() and not only_withheld(t)))


def episode_category(rules: ExtractionRules, current: str, topics: Sequence[str]) -> str:
    """Where an episode that touched `topics` is filed: its own category when that is sensitive already,
    else the schema's episode category of that topic, else the product's name for it (`health`), which the
    policy treats as sensitive even when the space never declared it."""
    known = [t for t in topics if t != UNKNOWN_TOPIC]
    if not known or is_product_sensitive(current):
        return current
    for name in rules.episode_categories:
        if topic_of(name) in known:
            return name
    return known[0]


def withhold_result(
    result: ExtractionResult,
    rules: ExtractionRules,
    held: Mapping[str, str],
    lang: str,
    topics: Topics = sensitive_topics,
) -> ExtractionResult:
    return _Withholder(rules, held, lang, topics).run(result)


class _Withholder:
    def __init__(self, rules: ExtractionRules, held: Mapping[str, str], lang: str, topics: Topics) -> None:
        self._rules = rules
        self._held = held
        self._lang = lang
        self._topics = topics
        self.withheld = 0
        self.dropped = 0
        self.topics: dict[str, None] = {}

    def run(self, result: ExtractionResult) -> ExtractionResult:
        facts = [f for f in map(self._fact, result.facts) if f is not None]
        items = [i for i in map(self._item, result.open_items) if i is not None]
        slots = [s for s in map(self._slot, result.profile) if s is not None]
        claims = [self._claim(c) for c in result.agent_claims]
        values = [v for v in map(self._value, result.values) if v is not None]
        instructions = [i for i in map(self._instruction, result.instructions) if i is not None]
        episode = self._episode(result.episode) if result.episode is not None else None
        return replace(
            result,
            episode=episode,
            facts=tuple(facts),
            open_items=tuple(items),
            profile=tuple(slots),
            agent_claims=tuple(claims),
            values=tuple(values),
            instructions=tuple(instructions),
            dropped=result.dropped + self.dropped,
            withheld=result.withheld + self.withheld,
        )

    def _text(self, text: str) -> str:
        scrubbed = withhold_text(text, self._held, self._lang)
        self.withheld += scrubbed.withheld
        return scrubbed.text

    def _note(self, topics: Sequence[str]) -> None:
        """Sensitive content the model filed as ordinary: the conversation is about that category."""
        self.topics.update(dict.fromkeys(topics))

    def _episode(self, draft: EpisodeDraft) -> EpisodeDraft:
        summary = self._text(draft.summary)
        resolution = self._text(draft.resolution) if draft.resolution else draft.resolution
        category = episode_category(self._rules, self._text(draft.category), tuple(self.topics))
        keep = self._keeps_matter(draft, category)
        return replace(
            draft,
            summary=summary,
            resolution=resolution,
            intent=self._text(draft.intent),
            category=category,
            outcome=self._text(draft.outcome),
            matter=draft.matter if keep else None,
            matter_label=draft.matter_label if keep else None,
        )

    def _keeps_matter(self, draft: EpisodeDraft, category: str) -> bool:
        """The matter is stored in the clear and named in count lines: a conversation about sensitive content
        keeps none, so its key or label never says what the category's policy holds back."""
        if draft.matter is None:
            return False
        named = f"{draft.matter.replace('_', ' ')} {draft.matter_label or ''}"
        sensitive = {p.category for p in self._rules.predicates if p.sensitivity is Sensitivity.SENSITIVE}
        return not (self.topics or tuple(self._topics(named)) or category in sensitive)

    def _fact(self, cmd: FactCommand) -> FactCommand | None:
        value = self._text(cmd.value)
        if cmd.op is FactOp.RETIRE:
            return replace(cmd, value=value)  # the stored fact's own value; the target decides what retires
        if only_withheld(value):
            self.dropped += 1
            return None
        topics = misfiled(value, cmd.sensitivity, cmd.category, self._topics)
        if not topics:
            return replace(cmd, value=value)
        self._note(topics)
        home = sensitive_home(self._rules, topics, value)
        if home is None:
            self.dropped += 1
            return None
        return replace(
            cmd,
            value=value,
            predicate=home.name,
            namespace=home.namespace,
            category=home.category,
            sensitivity=Sensitivity.SENSITIVE,
        )

    def _item(self, draft: OpenItemDraft) -> OpenItemDraft | None:
        description = self._text(draft.description)
        if only_withheld(description):
            self.dropped += 1
            return None
        operation = self._text(draft.expected_operation) if draft.expected_operation else None
        return replace(draft, description=description, expected_operation=operation)

    def _instruction(self, draft: InstructionDraft) -> InstructionDraft | None:
        text = self._text(draft.text)
        topics = tuple(self._topics(text))
        if only_withheld(text) or topics:
            # An instruction reaches every participant of its object: sensitive content never goes into one.
            self._note(topics)
            self.dropped += 1
            return None
        return replace(draft, text=text)

    def _slot(self, draft: SlotDraft) -> SlotDraft | None:
        value = self._text(draft.value)
        topics = tuple(self._topics(value))
        if only_withheld(value) or topics:
            # A profile slot has no sensitivity of its own: sensitive content never goes into one.
            self._note(topics)
            self.dropped += 1
            return None
        return replace(draft, value=value)

    def _claim(self, claim: AgentClaim) -> AgentClaim:
        value = self._text(claim.value)
        predicate = self._rules.predicate(claim.predicate) if claim.predicate else None
        if predicate is not None and misfiled(value, predicate.sensitivity, predicate.category, self._topics):
            return replace(claim, value=value, predicate=None)
        return replace(claim, value=value)

    def _value(self, draft: ValueDraft) -> ValueDraft | None:
        if self._text(draft.raw) != draft.raw:
            self.dropped += 1
            return None
        return draft


def _held_kind(digits: str, held: Mapping[str, str]) -> str | None:
    if digits in held:
        return held[digits]
    for number, kind in held.items():
        if len(number) >= 8 and number in digits:
            return kind
    return None


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


_SECRET_WORDS = re.compile(r"senha|pass|contrase|clave|://|\bpin\b", re.IGNORECASE)


def _maybe_secret(text: str) -> bool:
    return bool(_SECRET_WORDS.search(text))
