"""The tenant's extraction schema as the prompt and the validation see it (5.7).

Built by the app from the signed configuration for one session's source: tenant predicates plus
the source's own (rev. 9), each with the sensitivity of its category.
"""

import hashlib
import re
from dataclasses import dataclass

from niadra.domain.vocabulary import Sensitivity

EXTRACTOR_VERSION = "x4"
"""x4: an item's one id is whose it is, its content words, due date and object, and its conversation only when
nothing else pins it (`item_id_for`)."""
PROMPT_VERSION = "p26"
"""p26: the hours a customer says they may be contacted in (`hours`: channel, not_before, not_after, days,
lift), asked for only when a customer turn names a time of day and a way to be contacted; kept as a
suppression of every purpose outside those hours (the suppression-list spec, 6.1) and in the person's block.
"Não liguem antes das 9h" had been kept as a fact's words that held nothing (juridico-zero, 09/10/2026). Every
other batch reads the prompt of p25.
p25: operations_seen close the user part again, after the batch, as in p23. p24 put them at its head, for
the cache, and the model tagged fewer items with the operation a meaning names: on the 32 promise_action
cases of dataset v2 with the space's observed meanings, 3 runs each, the item the billing agent's action
does was closable (its operation of the action's family, or none) in 86 of 96 with p23 and 74 with p24
(discount, recorded as redeliver: 9 and 3 of 18; reimbursement 18 and 11), and the benchmark's
promise_action fell from 95.7% to 89.3% (run 2026-10-10-ac701b).
p24: a rule that cannot apply is not sent: the instructions (and their place in the answer) when the input
has neither an object nor a link to cite, the object offered without a tag when it has no object, the staff
rule for a customer's session, a predicate flag no predicate of the space carries, and operations_seen when
the input quotes none; and the space's operations_seen open the user part, ahead of the batch's reference
time, so they stay in the cached prefix. Every rule that applies reads word for word as in p23. The
extraction prompt had doubled since 30/09 (about 1,900 to 2,950 input tokens a call on the extraction eval's
144 sessions of dataset v2).
p23: a space without profile slots is offered none, in the response schema or the rules. Offered `slots`
with no name to pick from, the model filed a customer's "prefiro ser procurado por e-mail; não liguem antes
das 9h" there under the predicates' names, the validation dropped both, and the preference never reached
memory (juridico-zero, 09/10/2026, 2 of 3 long e-mail turns). Replayed on that 7,600-character turn with the
space's configuration: the channel preference was lost in 3 of 58 runs with p22, in 0 of 48 with p23.

p22: an agent saying it will check with, ask or tell its own people about a matter, whatever their role or
title, to get back to the customer or so they confirm what comes next, is no item unless it gives the
customer a time; one of its people doing something for the customer is a promise like any other. p20 named
only "a specialist, a supervisor, another area", and "Vou comunicar o advogado para confirmar os próximos
passos" was an item again in the final zero-touch round (09/10/2026). Replayed on short paraphrased
sessions, valid answers only (the model account's credit ran out mid-run): an item from telling, checking
with the role and checking with "him", 1 of 13, 0 of 13 and 0 of 11 with p21, 0 of 10, 0 of 10 and 0 of 6
with p22; with a doctor or a technician 0 of 20 and 0 of 9; promises, one made by one of the company's
people, and a check with a time kept their item, 17 of 17 and 12 of 12.

p20: the rules' examples come from no one domain (an order, a claim, a bill, a line), never from the legal
pilot's (a case, a settlement, a wife, a trial), and `only_with` is a name without a title. Replayed on four
juridico-zero sessions of round 4 (08/10/2026), 6 times each with p20 and 5 with p19, read raw: a request
and a promise keep their stated dues, a reason keeps who decided ("a diretoria"), a compound turn gives both
items, a contact preference is marked `contact` and a restriction holds only with "Clara", all in every run
of both; the first p20 wording, with no word on titles, wrote "Sra. Clara" in 3 of 5.

p19: a predicate that observes a field holds each value a customer or staff turn gives for it, even when
the summary keeps it: a lawyer's "O prazo do processo ... é 02/10/2026", extracted with another session of his
in one call, was left in the summary only in 4 of 40 replays, in 0 of 80 now (and in 0 of 20 alone). An agent
turn giving it is a claim, never the person's word: validation drops such a value (the model wrote one in 2
of 20 replays before, 17 of 20 with this wording).

p17: an instruction about the organization's objects in general cites the link, even in a conversation
about one object (the round-9 "não falar de valores dos processos com Clara" was stored on one case only).

p16: an instruction's reason in the turns' own words, with who decided ("a diretoria da Rota Sul decidiu
levar a julgamento"): reworded, it lost who decided in 3 of 6 replays of the round-8 session, 0 of 6 now; a
contact preference is marked `contact` (6 of 6 replays, never Clara's restriction), so it reads as how to
reach a person and not as a limit on what may be said to them.
p15: an instruction or contact preference stands whatever the agent answers (the firm's assistant declined
to note "não falar de valores com a Clara" and the instruction was lost: 1 of 4 replays, both instructions in
6 of 6 with the rule); a written date beside a relative day is the due; a staff source's person is the team
in summaries, never the client.
p14: an instruction keeps the reason its turns gave ("não propor acordo; a diretoria decidiu levar a
julgamento"): without it a client's agent could not say why (juridico-sandbox, Q9 of 05/10/2026); kept in 9
of 10 replays against 0 of 5 before. A contact preference names its person, not an example's manager.
p13: items carry a kind (promise, dispute, request); instructions about one object; natural keys as object
aliases."""
OBSERVATION = "observation"
"""The predicate the product ships for what was said or done about an object that no predicate of the space
holds: text, never single-valued, only with the object."""
OBJECT_CATEGORY = "object"


@dataclass(frozen=True, slots=True)
class PredicateRule:
    name: str
    category: str
    namespace: str = "tenant"
    single_valued: bool = False
    enum: tuple[str, ...] | None = None
    sensitivity: Sensitivity = Sensitivity.NORMAL
    organization: bool = False
    """Holds facts about the organization a contact acts for (its e-mail, its hours), never the contact's."""
    observes: str | None = None
    """The field of an object's type whose value, as a turn says it, this predicate holds."""


@dataclass(frozen=True, slots=True)
class SlotRule:
    name: str
    type: str = "string"


@dataclass(frozen=True, slots=True)
class ExtractionRules:
    schema_version: str
    predicates: tuple[PredicateRule, ...]
    intents: tuple[str, ...]
    outcomes: tuple[str, ...]
    episode_categories: tuple[str, ...] = ()
    slots: tuple[SlotRule, ...] = ()
    language: str = "pt-BR"
    timezone: str = "UTC"
    stt_floor: float = 0.55
    operations: tuple[str, ...] = ()
    """Canonical operations an open item may wait for: the catalog's and the ones the tenant's sources
    and mappings record (18.4)."""
    operation_meanings: tuple[tuple[str, str], ...] = ()
    """(canonical operation, what it means in the company's words) for the declared operations that say it:
    the prompt shows each next to its name, which is what lets the model tag an item said in the company's
    language with an operation named in English (B1, 03/10/2026: 56% of such items tagged without the
    meanings, 97% with them)."""

    guidance: "Guidance | None" = None
    """The company's words on what to focus on and leave out; None when the space set none."""
    staff: bool = False
    """The source talks with the company's own people, not its customers (`domain.memory.bound.staff`): the
    person of its sessions is the team in what the extraction writes, never the client."""
    key_patterns: tuple[tuple[str, str, re.Pattern[str]], ...] = ()
    """(type, natural key, pattern) the space's types declare (`key.match`): what names an object in the
    text."""

    def predicate(self, name: str) -> PredicateRule | None:
        return next((p for p in self.predicates if p.name == name), None)

    @property
    def versions_key(self) -> str:
        """Idempotency of a run (5.5, L13): a new extractor, prompt or schema extracts again."""
        base = f"{EXTRACTOR_VERSION}.{PROMPT_VERSION}.{self.schema_version}"
        # Changed guidance or operation meanings extract again; a space without them keeps the key it had.
        if self.guidance:
            base = f"{base}.g{self.guidance.digest}"
        if self.operation_meanings:
            text = "\x1e".join(f"{name}\x1f{meaning}" for name, meaning in self.operation_meanings)
            base = f"{base}.o{hashlib.sha256(text.encode()).hexdigest()[:8]}"
        if self.staff:
            base = f"{base}.staff"
        return base


@dataclass(frozen=True, slots=True)
class Guidance:
    instructions: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.instructions or self.include or self.exclude)

    @property
    def digest(self) -> str:
        text = "\x1f".join(["\x1e".join(part) for part in (self.instructions, self.include, self.exclude)])
        return hashlib.sha256(text.encode()).hexdigest()[:8]
