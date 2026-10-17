"""The `recording` document (24.3.5): where a source's recorded values may live, and which changes need the
security role on top of integration."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from niadra.config.recording import RecordingSettings, SourceRecording, escalate
from niadra.domain.control.records import Role

SOURCE = uuid4()


def test_a_source_keeps_its_mode_or_less() -> None:
    settings = RecordingSettings(
        content_mode="stored", sources=[SourceRecording(source_id=SOURCE, content_mode="pointer")]
    )
    assert settings.mode_for(SOURCE) == "pointer"
    assert settings.mode_for(uuid4()) == "stored"
    assert [m for m in ("stored", "pointer", "hash_only") if settings.accepts(SOURCE, m)] == [
        "pointer",
        "hash_only",
    ]
    with pytest.raises(ValidationError, match="two lines"):
        RecordingSettings(sources=[SourceRecording(source_id=SOURCE, content_mode="pointer")] * 2)
    with pytest.raises(ValidationError):
        RecordingSettings(kept_days=6)


def test_recordings_are_kept_until_the_customer_sets_a_retention() -> None:
    settings = RecordingSettings()
    assert (settings.kept_days, settings.scenario_days) == (None, None)
    assert RecordingSettings(kept_days=3650).kept_days == 3650


def test_letting_more_in_or_keeping_more_needs_security() -> None:
    private = RecordingSettings(content_mode="pointer", kept_days=30, sample_rate=0.05)
    assert escalate(private, private) == frozenset()
    assert escalate(private, private.model_copy(update={"kept_days": 7, "sample_rate": 0.01})) == frozenset()
    assert escalate(private, private.model_copy(update={"content_mode": "hash_only"})) == frozenset()
    for looser in (
        {"content_mode": "stored"},
        {"kept_days": 60},
        {"kept_days": None},
        {"sample_rate": 0.1},
        {"sources": [SourceRecording(source_id=SOURCE, content_mode="stored")]},
    ):
        assert escalate(private, private.model_copy(update=looser)) == {Role.SECURITY}, looser
