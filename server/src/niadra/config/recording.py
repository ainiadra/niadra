"""The `recording` document (24.3.5): how a space keeps its agents' turn records. Where a recorded value may
live, per source (the Turn Record's content modes), which pins make a turn replayable, how long the kept tier
and a replay scenario keep a turn, and how much the deterministic sample keeps. No tier expires a turn on its
own: a recording is kept until the retention the customer sets (`kept_days`, `scenario_days`) or an erasure.
"""

from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from niadra.contracts.turns import ContentMode, PinName, TurnRecordingSummary
from niadra.domain.control.records import Role

# How much of a recorded value a mode lets into Niadra: the value itself, a pointer to it, or only its hash.
_PRIVACY: dict[str, int] = {"stored": 0, "pointer": 1, "hash_only": 2}


class SourceRecording(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, defer_build=True)

    source_id: UUID
    content_mode: ContentMode


class RecordingSettings(BaseModel):
    # The same as `config.models.Doc`, which imports this module for its section.
    model_config = ConfigDict(extra="forbid", frozen=True, defer_build=True)

    content_mode: ContentMode = Field(
        default="stored",
        description="Where the recorded values of a source without its own line live: `stored` in Niadra, "
        "encrypted; `pointer` in the company's storage, with only pointers and digests sent; `hash_only` "
        "nowhere, only digests. A source may always send a mode that lets less in.",
    )
    sources: list[SourceRecording] = Field(default_factory=list, max_length=500)
    required_pins: frozenset[PinName] = Field(
        default=frozenset({"prompts", "model"}),
        description="The pins a turn needs to be replayable; a turn without one is kept, marked not "
        "replayable.",
    )
    kept_days: int | None = Field(
        default=None,
        ge=7,
        description="The retention of turn recordings, in days: a turn older than this goes, in every tier. "
        "Unset, a recording is kept until an erasure removes it.",
    )
    scenario_days: int | None = Field(
        default=None,
        ge=7,
        description="How long a replay scenario keeps its turns after it last ran. Unset, they are kept "
        "until an erasure removes them.",
    )
    pii_model: bool = Field(
        default=False,
        description="After the rules, the PII model also reads the text of the tools' arguments in `stored` "
        "records, and the values it finds are replaced in the record.",
    )
    sample_rate: float = Field(
        default=0.05,
        ge=0,
        le=0.5,
        description="The share of conversations the kept tier takes whole, besides the flagged turns and the "
        "ones promoted on request.",
    )

    @model_validator(mode="after")
    def _one_line_per_source(self) -> Self:
        ids = [s.source_id for s in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("a source has two lines")
        return self

    def mode_for(self, source_id: UUID) -> ContentMode:
        return next((s.content_mode for s in self.sources if s.source_id == source_id), self.content_mode)

    def summary(self, source_id: UUID) -> TurnRecordingSummary:
        """What the SDK profile tells one source."""
        return TurnRecordingSummary(
            content_mode=self.mode_for(source_id), required_pins=sorted(self.required_pins)
        )

    def accepts(self, source_id: UUID, mode: str) -> bool:
        """A turn may keep less than its source's mode lets in, never more."""
        return _PRIVACY[mode] >= _PRIVACY[self.mode_for(source_id)]


def escalate(before: RecordingSettings, after: RecordingSettings) -> frozenset[Role]:
    """The roles a diff needs besides `integration` (24.2): `security` when it lets more of the recorded
    values into Niadra for a source, keeps more turns or keeps them longer, or stops the PII model's pass."""
    sources = {s.source_id for s in (*before.sources, *after.sources)}
    loosened = any(_PRIVACY[after.mode_for(s)] < _PRIVACY[before.mode_for(s)] for s in sources)
    loosened |= _PRIVACY[after.content_mode] < _PRIVACY[before.content_mode]
    loosened |= _longer(before.kept_days, after.kept_days) or after.sample_rate > before.sample_rate
    loosened |= _longer(before.scenario_days, after.scenario_days)
    loosened |= before.pii_model and not after.pii_model
    return frozenset({Role.SECURITY}) if loosened else frozenset()


def _longer(before: int | None, after: int | None) -> bool:
    """Whether a retention keeps longer: unset keeps forever."""
    if before is None:
        return False
    return after is None or after > before
