"""The `claim-contract` document (24.3.3): what an agent may state and the evidence each claim needs in the
turn record. The categories, evidence, natures and actions are the public `claim-contract.v0` format
(`contracts/claims.py`); the document adds what holds across categories. A space without one declares
nothing, and the SDK checks nothing.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from niadra.contracts.claims import (
    INTERNAL_TEXT,
    ClaimCategory,
    ClaimContractSummary,
    ClaimOutputs,
    InternalText,
    Language,
    NegativeCorpus,
)
from niadra.contracts.common import ShortStr

MAX_CATEGORIES = 64
DEFAULT_LANGUAGES: tuple[Language, ...] = ("pt",)


class ClaimContract(BaseModel):
    # The same as `config.models.Doc`, which imports this module for its section.
    model_config = ConfigDict(extra="forbid", frozen=True, defer_build=True)

    version: ShortStr = "0"
    languages: list[Language] = Field(
        default_factory=lambda: list(DEFAULT_LANGUAGES),
        min_length=1,
        max_length=3,
        description="The languages the outputs are in; the first reads a number whose language is unknown.",
    )
    categories: list[ClaimCategory] = Field(default_factory=list, max_length=MAX_CATEGORIES)
    negative_corpus: NegativeCorpus | None = None
    internal_text: InternalText | None = None
    outputs: ClaimOutputs = Field(default_factory=ClaimOutputs)

    @model_validator(mode="after")
    def _across_categories(self) -> Self:
        if len(set(self.languages)) != len(self.languages):
            raise ValueError("each language is listed once")
        ids = [c.id for c in self.categories]
        if len(set(ids)) != len(ids):
            raise ValueError("each category has its own id")
        if INTERNAL_TEXT in ids:
            raise ValueError(
                f"`{INTERNAL_TEXT}` is the category of a redacted internal text: name it otherwise"
            )
        for category in self.categories:
            for context, action in category.actions.contexts.items():
                if action == "rewrite_if_unequivocal" and context not in self.outputs.mutable:
                    # Never rewrite what is immutable, nor what the contract does not say is mutable (24.3.3).
                    raise ValueError(f"category {category.id}: `{context}` is no mutable output to rewrite")
            if category.actions.replace_with is not None and not self.outputs.mutable:
                raise ValueError(f"category {category.id}: `replace_with` needs a mutable output")
        if self.categories and self.negative_corpus is None:
            raise ValueError("a contract with categories comes with its negative corpus")
        return self

    def summary(self) -> ClaimContractSummary:
        return ClaimContractSummary(
            version=self.version,
            languages=self.languages,
            categories=self.categories,
            negative_corpus_version=self.negative_corpus.version if self.negative_corpus else None,
            internal_text=self.internal_text,
            outputs=self.outputs,
        )
