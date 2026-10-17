"""Rules of the decision models (2.7, 5.6): what each task may answer and when doubt wins.

Every task has a conservative label ("extract", "injection", "unclassified", ...). A model answer
other than that label is accepted only at or above the task's threshold; below it, or when the
model is slow or down, the conservative label is the decision.

Two respondents stand behind the port. The decision model, an external endpoint the deployment names,
answers the tasks in `MODEL_TASKS`, on masked texts, off the read path. The in-cell models pod answers
PII spans.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from niadra.domain.common.text import pieces


class DecisionTask(StrEnum):
    WORTH_EXTRACTING = "worth_extracting"
    INJECTION = "injection"
    PII = "pii"
    QUESTION_TYPE = "question_type"
    ACTION_FULFILS_ITEM = "action_fulfils_item"
    ITEM_RESTATES_ITEM = "item_restates_item"
    """Whether a new item on an object tells again an open item another conversation bound to it."""
    TURN_DELIVERS_ITEM = "turn_delivers_item"
    """Whether the customer-facing agent's own message gives the customer what an open item promised, now."""
    TURN_ASKS_ITEM = "turn_asks_item"
    """Whether the customer-facing agent's own message asks the customer for what an open request asks."""
    SENSITIVE_CATEGORY = "sensitive_category"
    CONTRADICTION = "contradiction"
    """Whether a pair the contradiction band drew really conflicts."""


@dataclass(frozen=True, slots=True)
class DecisionRule:
    conservative: str
    threshold: float
    labels: frozenset[str]


RULES: dict[DecisionTask, DecisionRule] = {
    DecisionTask.WORTH_EXTRACTING: DecisionRule("extract", 0.8, frozenset({"extract", "skip"})),
    # "safe" needs only 0.2: the decision model's probability of an injection above 0.8 flags a text.
    # Calibrated on 27/09: 100% precision on two labeled sets, 85% recall against the
    # retired rules' 58%; an ordinary message scored at most 0.69.
    DecisionTask.INJECTION: DecisionRule("injection", 0.2, frozenset({"injection", "safe"})),
    DecisionTask.PII: DecisionRule("sensitive", 1.0, frozenset({"sensitive", "clean"})),
    DecisionTask.QUESTION_TYPE: DecisionRule(
        "ambiguous", 0.6, frozenset({"asks_data", "confirms", "verifies", "ambiguous"})
    ),
    # Asked only when an action or a system event is on the item's object, says one of its words besides the
    # act, and the words alone do not settle it: a customer-facing agent's action with the item's operation,
    # or any closing of an item that waits for no operation or one of the closing's family
    # (`domain.memory.items.to_judge`). "fulfils" must be confident to close it. Measured on 05/10/2026
    # (legal-domain sandbox pairs): the real ones at 0.91 to 0.96, the wrong ones of phase 2 at 0.28 at most.
    DecisionTask.ACTION_FULFILS_ITEM: DecisionRule(
        "does_not_fulfil", 0.8, frozenset({"fulfils", "does_not_fulfil"})
    ),
    # Asked only when a new item is on an open item's object, its operation the same, of one family, done to
    # the same kind of thing or either waiting for none, its due a day from the open item's (any due, when
    # a customer-facing agent said it), says one of its words besides the act, and the words alone do not
    # settle it (`domain.memory.items.to_judge_retelling`). Measured on 06/10/2026 with legal-domain sandbox
    # wordings, two runs each: the same thing told again at 0.73 to 0.94 ("enviar ao financeiro comprovante
    # do pagamento das custas" for "enviar comprovante do pagamento ao financeiro do cliente", 0.73), two
    # things to do at 0.04 to 0.25 (two filings on one "termo de entrega", 0.25); on 07/10/2026 across
    # operations, 0.68 to 0.89 against 0.08 at most. 0.6 keeps every distinct pair out with a margin.
    DecisionTask.ITEM_RESTATES_ITEM: DecisionRule("distinct", 0.6, frozenset({"restates", "distinct"})),
    # Asked before a customer-facing agent's declared action closes an item, about the agent's own reply in
    # the conversation (`app.memory`, the delivery gate). Measured on 06/10/2026 on legal-domain sandbox
    # turns, two runs each: replies that only say it was, will be or can be done, or leave the content out
    # ("Já estou enviando para você", "Já te avisei sobre isso", "Posso te passar a data?") at 0.05 to 0.40;
    # replies that tell the result or the date, or carry the document, at 0.82 to 0.95. 0.6 keeps a margin on
    # both sides.
    DecisionTask.TURN_DELIVERS_ITEM: DecisionRule("undelivered", 0.6, frozenset({"delivers", "undelivered"})),
    # The same gate for an item that asks the customer for something: the reply that passes the request on
    # ("o advogado pediu para enviar os extratos ... até 06/10") at 0.67 to 0.84; one that says a request will
    # come, came before, or asks for another thing at 0.05 to 0.10.
    DecisionTask.TURN_ASKS_ITEM: DecisionRule("unasked", 0.6, frozenset({"asks", "unasked"})),
    # The band only warns; "compatible" drops a warning, so it must be confident.
    DecisionTask.CONTRADICTION: DecisionRule("contradicts", 0.8, frozenset({"contradicts", "compatible"})),
    # "none" must be confident; any doubt reads as a sensitive category of unknown kind (5.12, 20.4).
    DecisionTask.SENSITIVE_CATEGORY: DecisionRule(
        "unclassified",
        0.8,
        frozenset(
            {
                "health",
                "religion",
                "politics",
                "union",
                "sexual_orientation",
                "ethnicity",
                "genetic_biometric",
                "criminal_record",
                "none",
                "unclassified",
            }
        ),
    ),
}

PAIR_SEPARATOR = " [SEP] "
FALLBACK_MODEL = "conservative-default"
DECISION_MODEL = "decision-model"

ANSWERED_THRESHOLD = 0.7
"""A short session is kept as its own turns, without the extraction model, when the decision model gives it at
least this probability of being answered on the spot. Calibrated on 28/09 on every conversation of benchmark
dataset v2, a customer's sessions asked together: no session that carried what a probe asks (a complaint, a
deadline, a promise, a changed postal code, a stated value) scored above 0.29, and 85% of the general
questions scored above 0.7."""
INJECTION_SCREEN = 0.3
"""The batch's turns are checked one by one (`INJECTION`, as calibrated on 27/09) only when the decision
model gives at least this probability that some message tries to instruct the system. On 28/09 every one of
the 30 hand-labeled injections of the calibration set, each inside an ordinary conversation, scored
0.6 or more; the 45 ordinary messages that use the attack's words scored up to 0.51."""
SENSITIVE_SCREEN = 0.3
"""The texts an extraction keeps are asked their sensitive category (`SENSITIVE_CATEGORY`) only when the
decision model gives at least this probability that some message reveals a special category of personal data,
and no session of such a batch is kept without the extraction model. On 28/09 the 37 special-category messages
of the evaluation set scored 0.6 or more inside a conversation; the 19 ordinary ones up to 0.83."""


@dataclass(frozen=True, slots=True)
class Triage:
    """The decision model's one answer about a batch of closed sessions of one customer: for each session
    the probability that it was answered on the spot and its category, and for the batch the probability
    that some message tries to instruct the system and that some message reveals a special category of
    personal data."""

    answered: tuple[float, ...]
    categories: tuple[str | None, ...]
    injection: float
    sensitive: float
    model_version: str

    @classmethod
    def conservative(cls, sessions: int) -> "Triage":
        """What a model that did not answer means: every session goes to the extraction model and every
        check runs."""
        return cls((0.0,) * sessions, (None,) * sessions, 1.0, 1.0, FALLBACK_MODEL)

    def light(self, index: int) -> bool:
        """Whether the session may be kept as its own turns: answered on the spot, and nothing in the batch
        that the full checks must see."""
        return (
            self.answered[index] >= ANSWERED_THRESHOLD
            and self.injection < INJECTION_SCREEN
            and self.sensitive < SENSITIVE_SCREEN
        )


MODEL_TASKS: frozenset[DecisionTask] = frozenset(
    {
        DecisionTask.SENSITIVE_CATEGORY,
        DecisionTask.INJECTION,
        DecisionTask.QUESTION_TYPE,
        DecisionTask.CONTRADICTION,
        DecisionTask.ACTION_FULFILS_ITEM,
        DecisionTask.ITEM_RESTATES_ITEM,
        DecisionTask.TURN_DELIVERS_ITEM,
        DecisionTask.TURN_ASKS_ITEM,
    }
)
"""The tasks the decision model decides one text at a time; `WORTH_EXTRACTING` is decided per
session by the triage (`Triage`) and recorded under its name. Exact rules (masking, temporal parse, lexicon,
guards, backing, sampling) are never a model's, and nothing here runs on the read path."""


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    decision_id: UUID
    task: DecisionTask
    input_hash: bytes
    model: str
    model_version: str
    label: str
    probability: float
    threshold: float
    created_at: datetime
    subject_ref: str | None = None


def resolve(task: DecisionTask, label: str, probability: float, threshold: float | None = None) -> str:
    rule = RULES[task]
    if label not in rule.labels or label == rule.conservative:
        return rule.conservative
    return label if probability >= (rule.threshold if threshold is None else threshold) else rule.conservative


def pair(first: str, second: str) -> str:
    """The input of pair classifiers such as `action_fulfils_item`: "<open item> [SEP] <action>"."""
    return f"{first}{PAIR_SEPARATOR}{second}"


WINDOWED: dict[DecisionTask, str] = {
    DecisionTask.ACTION_FULFILS_ITEM: "fulfils",
    DecisionTask.ITEM_RESTATES_ITEM: "restates",
    DecisionTask.TURN_DELIVERS_ITEM: "delivers",
    DecisionTask.TURN_ASKS_ITEM: "asks",
    DecisionTask.CONTRADICTION: "contradicts",
}
"""The pair tasks whose texts are read in windows, each with the label one window is enough for: an action
whose middle says the thing was done fulfils the item, a pair with one conflicting window conflicts. Memory
keeps every text whole (an action's result, a reply, an item) and the question reads all of it."""
DECISION_WINDOW = 6_000
"""Characters of each text of a pair one question reads at most."""
DECISION_OVERLAP = 500
"""What a window repeats of the one before, so a sentence cut by a window's end is read whole."""
MOST_WINDOWS = 40
"""Questions one pair may take: the longest action result an agent may send (200,000 characters) against a
short item is 37 windows. A pair past it is not asked and keeps the conservative label, logged."""


def pair_windows(text: str) -> list[str]:
    """A pair as its questions read it: itself when both of its texts fit a window, else every window of
    the first against every window of the second, so no part of either is left unread."""
    first, sep, second = text.partition(PAIR_SEPARATOR)
    if not sep or (len(first) <= DECISION_WINDOW and len(second) <= DECISION_WINDOW):
        return [text]
    return [
        pair(a, b)
        for a in pieces(first, DECISION_WINDOW, DECISION_OVERLAP)
        for b in pieces(second, DECISION_WINDOW, DECISION_OVERLAP)
    ]


def input_hash(text: str) -> bytes:
    return hashlib.sha256(text.encode()).digest()
