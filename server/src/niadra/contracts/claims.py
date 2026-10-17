"""The claim contract (24.3.3): categories of claims, the evidence each one needs in the turn record, the
three natures of a number, roles, graded actions, the text anchor and the negative corpus; the part of it the
SDK keeps in its local cache (`GET /v1/sdk/profile`); and the verdict the SDK reports for each claim in the
turn record.

The format is the `claim-contract.v0` spec, and the signed document that holds it is `config/claims.py`. The
server runs no claim check in a turn: it receives the verdicts and adds them up.
"""

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from niadra.contracts.common import IdStr, Model, ShortStr

Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
"""A category, a role, an output context, a type or a field: `price`, `price_full`, `contestacao`."""
ToolName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")]
Term = Annotated[str, StringConstraints(min_length=1, max_length=80)]
Phrase = Annotated[str, StringConstraints(min_length=1, max_length=500)]
ClaimClass = Literal["money", "percent", "date", "duration", "quantity", "count", "dosage"]
"""What a number measures. A number that names instead (an order, a postal code, a case number, a size, a
statute's article) is a `label`, which no category detects and nothing ever rewrites."""
Language = Literal["pt", "en", "es"]
Nature = Literal["computed", "quoted", "model"]
Action = Literal["block", "warn", "count", "rewrite_if_unequivocal", "discard_anchor_and_count"]
Verdict = Literal[
    "matched",
    "mismatch",
    "stale",
    "gap_not_stated",
    "role_ambiguous",
    "unsupported",
    "no_evidence",
    "quoted_found",
    "quoted_missing",
    "anchored",
    "below_threshold",
    "source_exists_claim_unverified",
    "source_missing",
    "not_checked",
    "internal_text_found",
]
MIN_ANCHOR_MATCH = 0.90
"""A text anchor holds at this normalized similarity or above; a contract may ask for more, never less."""


class Detect(Model):
    """How a category finds its claims in the text, without a model: numbers of these classes, the words
    around them that give their role, terms of the trade, named patterns, or sections of a document."""

    classes: list[ClaimClass] = Field(default_factory=list, max_length=7)
    roles: dict[Name, list[Term]] = Field(
        default_factory=dict,
        max_length=32,
        description="The words that give a number its role, synonyms and every language in one list.",
    )
    terms: list[Term] = Field(
        default_factory=list,
        max_length=100,
        description="With `classes`, a number counts only in a sentence that holds one of them; alone, the "
        "term is the claim.",
    )
    patterns: list[Literal["article_citation", "precedent_citation"]] = Field(default_factory=list)
    document_sections: list[Name] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _detects_something(self) -> Self:
        if not (self.classes or self.terms or self.patterns or self.document_sections):
            raise ValueError("a category detects numbers, terms, patterns or document sections")
        if self.roles and not self.classes:
            raise ValueError("roles qualify numbers: `roles` needs `classes`")
        if any(not terms or len(terms) > 50 for terms in self.roles.values()):
            raise ValueError("each role has between 1 and 50 terms")
        return self


class ValueEvidence(Model):
    """A value in the turn record, from a tool's result or from state, that the number is checked against."""

    same_role: bool = Field(default=False, description="The value's role is the number's role.")
    fresh_for: Literal["claim"] | None = Field(
        default=None, description="The value is within its type's `claim_max_age`, or the claim is `stale`."
    )
    type: Name | None = None
    value: Name | None = Field(default=None, description="A field or a computed value of `type`.")
    must_state_gaps: bool = Field(
        default=False, description="The value's declared gaps must be said with it, or `gap_not_stated`."
    )
    gap_terms: dict[Name, list[Term]] = Field(
        default_factory=dict,
        max_length=32,
        description="The words that state each declared gap; a gap without them is stated by its name.",
    )


class AnchorEvidence(Model):
    min_match: float = Field(default=MIN_ANCHOR_MATCH, ge=MIN_ANCHOR_MATCH, le=1)
    coverage: Literal["fact", "law"] = Field(description="Counted apart: law and fact never add up.")


class ClaimEvidence(Model):
    """What the turn record must hold for a claim of the category to stand: exactly one kind."""

    value: ValueEvidence | None = None
    tool: ToolName | None = Field(default=None, description="A call of this tool in the turn.")
    tool_any: list[ToolName] = Field(default_factory=list, max_length=20)
    anchor: AnchorEvidence | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> Self:
        kinds = [self.value is not None, self.tool is not None, bool(self.tool_any), self.anchor is not None]
        if sum(kinds) != 1:
            raise ValueError("evidence is one of `value`, `tool`, `tool_any` or `anchor`")
        return self


class Natures(Model):
    """How a number is handled by where it came from: computed (a value with provenance in the turn, by rule
    or observed) is checked against it; quoted (inside quotation marks) is checked only for being in a source,
    as written, even when it is false; said by the model (no origin) takes this action, or the category's."""

    computed: Literal["check", "count"] = "check"
    quoted: Literal["verbatim", "count"] = "verbatim"
    model: Literal["block", "warn", "count"] | None = None


class Actions(Model):
    """What happens to a claim that does not stand, by the task's context (an output kind or a document kind);
    `default` for any other. A context the contract lists as immutable is never rewritten."""

    default: Action
    contexts: dict[Name, Action] = Field(default_factory=dict, max_length=32)
    replace_with: Annotated[str, StringConstraints(min_length=1, max_length=500)] | None = Field(
        default=None, description="The caveat a blocked passage gives way to, in a mutable output."
    )


class ClaimCategory(Model):
    id: Name
    agents: list[ShortStr] = Field(default_factory=list, max_length=50, description="Empty: every agent.")
    detect: Detect
    evidence: ClaimEvidence
    natures: Natures = Field(default_factory=Natures)
    actions: Actions

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        value = self.evidence.value
        if value is not None and not self.detect.classes:
            raise ValueError(f"category {self.id}: value evidence checks numbers: it needs `detect.classes`")
        if value is not None and value.same_role and not self.detect.roles:
            raise ValueError(f"category {self.id}: `same_role` needs `detect.roles`")
        if self.evidence.anchor is not None and not (self.detect.patterns or self.detect.document_sections):
            raise ValueError(f"category {self.id}: an anchor checks patterns or document sections")
        actions = [self.actions.default, *self.actions.contexts.values()]
        if "discard_anchor_and_count" in actions and self.evidence.anchor is None:
            raise ValueError(f"category {self.id}: `discard_anchor_and_count` is an action of anchors only")
        if self.actions.default == "rewrite_if_unequivocal":
            raise ValueError(f"category {self.id}: a rewrite is named per mutable context, never as default")
        return self


class NegativeCorpus(Model):
    """Phrases of the trade that must never trigger the contract, versioned: `niadra contract test` fails when
    one does."""

    version: ShortStr
    phrases: list[Phrase] = Field(min_length=1, max_length=5000)


INTERNAL_TEXT = "internal_text"
"""The category a redaction of the company's own prompt is recorded under; no contract may declare it."""


class InternalText(Model):
    """Fingerprints of the company's own prompt: hashes of every `n` words, computed by the SDK, never the
    prompt itself. An output that repeats one gives way to `redact`, recorded as a claim of the category
    `internal_text` with the verdict `internal_text_found`, the action taken and, as evidence, the
    `shingle_hashes_ref` in `document`."""

    shingle_hashes_ref: ShortStr = Field(description="The prompt version the hashes were computed from.")
    n: int = Field(default=8, ge=4, le=32)
    redact: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class ClaimOutputs(Model):
    immutable: list[Name] = Field(default_factory=lambda: ["document"], max_length=32)
    mutable: list[Name] = Field(default_factory=lambda: ["chat"], max_length=32)

    @model_validator(mode="after")
    def _apart(self) -> Self:
        if set(self.immutable) & set(self.mutable):
            raise ValueError("an output context is either immutable or mutable")
        return self


class ClaimContractSummary(Model):
    """The contract as the SDK keeps it: everything but the negative corpus's phrases, which only the CI's
    `niadra contract test` reads, from the company's own copy."""

    version: ShortStr
    languages: list[Language]
    categories: list[ClaimCategory] = Field(default_factory=list)
    negative_corpus_version: ShortStr | None = None
    internal_text: InternalText | None = None
    outputs: ClaimOutputs = Field(default_factory=ClaimOutputs)


Decimal = Annotated[str, StringConstraints(pattern=r"^-?(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,6})?$")]
"""A number as the parser normalizes it: a decimal string, so no language rounds it."""
IsoDate = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[0-9]{4}-[0-9]{2}(?:-[0-9]{2})?|--[0-9]{2}-[0-9]{2}|---[0-9]{2})$"),
]
"""ISO 8601, leaving out what the text leaves out: `2026-10`, `--10-05` (no year), `---20` (a day)."""


class ClaimValue(Model):
    """A number the parser read, normalized: an amount, or a range from `min` to `max`, with its unit (the
    currency code of money, `%`, a time unit, a unit of measure or of dose), or a date, or a range of
    dates."""

    amount: Decimal | None = None
    min: Decimal | None = None
    max: Decimal | None = None
    unit: Annotated[str, StringConstraints(min_length=1, max_length=16)] | None = None
    date: IsoDate | None = None
    date_from: IsoDate | None = None
    date_to: IsoDate | None = None

    @model_validator(mode="after")
    def _one_shape(self) -> Self:
        shapes = [
            self.amount is not None,
            self.min is not None and self.max is not None,
            self.date is not None,
            self.date_from is not None and self.date_to is not None,
        ]
        present = [self.amount, self.min, self.max, self.date, self.date_from, self.date_to]
        if sum(shapes) != 1 or sum(v is not None for v in present) != (1 if shapes[0] or shapes[2] else 2):
            raise ValueError("a value is an amount, a range of amounts, a date or a range of dates")
        return self


class ClaimEvidenceRef(Model):
    """Where a claim's evidence is in the turn record: a call's field, an object's field, or a document."""

    call_id: IdStr | None = None
    ref: IdStr | None = Field(default=None, description="An object, `type:namespace:id`.")
    field: Name | None = None
    document: IdStr | None = Field(default=None, description="The document an anchor cites.")
    anchor: IdStr | None = Field(default=None, description="The blob that keeps the quoted text.")
    score: float | None = Field(default=None, ge=0, le=1, description="The anchor's similarity.")


class ClaimRecord(Model):
    """One claim the SDK found in an output, as the turn record carries it: kinds, spans and verdicts."""

    category: Name
    class_: ClaimClass | None = Field(default=None, alias="class")
    nature: Nature | None = None
    role: Name | None = None
    span: tuple[int, int] = Field(description="Code point offsets of the claim in the output, end exclusive.")
    value: ClaimValue | None = None
    evidence: ClaimEvidenceRef | None = None
    verdict: Verdict
    action: Literal["none", "block", "warn", "count", "rewrite", "discard_anchor"] = Field(
        description="What was done, which the contract's configured action decides: `rewrite_if_unequivocal` "
        "is `rewrite` when the rewrite is unequivocal and `warn` otherwise, `discard_anchor_and_count` is "
        "`discard_anchor`, and a claim that stands (`matched`, `quoted_found`, `anchored`) is `none`."
    )

    @model_validator(mode="after")
    def _span(self) -> Self:
        if not 0 <= self.span[0] < self.span[1]:
            raise ValueError("a span starts before it ends")
        return self
