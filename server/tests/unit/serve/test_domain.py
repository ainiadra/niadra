"""Pure read-path pieces: recurrence, ranking, subject tokens, control group, privacy feed, receipts."""

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from niadra.config.models import Experiment
from niadra.domain.common.errors import InvalidInput, Unauthenticated
from niadra.domain.compile.model import ItemKind
from niadra.domain.policy import ItemAttrs
from niadra.domain.receipts import ReceiptDraft
from niadra.domain.serve.history import HistoryDoc, after_cursor, encode_cursor, newest_first
from niadra.domain.serve.holdout import assign, draw
from niadra.domain.serve.live import LiveLine, live_attrs
from niadra.domain.serve.privacy_guard import PrivacyGuard
from niadra.domain.serve.receipt_buffer import ReceiptBuffer
from niadra.domain.serve.recurrence import count_by_category, recurrence
from niadra.domain.serve.search import bm25, cosine, ranking, reciprocal_rank_fusion, tokenize
from niadra.domain.serve.subject_token import SubjectClaims, SubjectRef, SubjectTokens
from niadra.domain.vocabulary import (
    Audience,
    HandleType,
    ReceiptKind,
    ReceiptSurface,
    SubjectKind,
    Verification,
)
from tests.unit.compile.factories import T0, episode

SPACE, SOURCE = uuid4(), uuid4()


def test_recurrence_counts_episodes_per_normalized_category() -> None:
    episodes = [
        episode(1, "technical_visit", days_ago=1, outcome="unresolved"),
        episode(2, "technical_visit", days_ago=40, resolution="crédito na fatura"),
        episode(3, "technical_visit", days_ago=400),
        episode(4, "billing", days_ago=10),
    ]
    counts = count_by_category(episodes)
    assert [(c.category, c.occurrences) for c in counts] == [("technical_visit", 3), ("billing", 1)]
    # 90 days by default, the window every recurrence count shares.
    recent = recurrence(episodes, "technical_visit", now=T0)
    assert recent is not None
    assert (recent.occurrences, recent.last_outcome, recent.last_at) == (
        2,
        "unresolved",
        episodes[0].started_at,
    )
    assert recurrence(episodes, "delivery", now=T0) is None


def test_tokenize_folds_accents_and_case() -> None:
    assert tokenize("Visita TÉCNICA, remarcação!") == ["visita", "tecnica", "remarcacao"]


def test_bm25_prefers_rare_matching_terms_and_skips_non_matches() -> None:
    docs = {"a": "visita técnica remarcada", "b": "fatura de agosto contestada", "c": "fatura paga"}
    scores = bm25("fatura contestada", docs)
    assert set(scores) == {"b", "c"}
    assert ranking(scores)[0] == "b"


def test_rank_fusion_and_cosine() -> None:
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]])
    assert ranking(fused) == ["b", "a", "c"]
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def claims(expires_at: datetime) -> SubjectClaims:
    return SubjectClaims(
        SPACE,
        SOURCE,
        SubjectRef(HandleType.PHONE_E164, "+5511999990001"),
        Verification.V2,
        expires_at,
        conversation_id="conv-1",
    )


def test_subject_token_round_trip_seals_the_handle() -> None:
    tokens = SubjectTokens(b"s" * 32)
    minted = claims(T0.replace(microsecond=0) + timedelta(minutes=15))
    token = tokens.mint(minted)
    assert "5511999990001" not in token
    assert tokens.verify(token, now=T0) == minted


def test_subject_token_binds_the_organization_sealed_like_the_subject() -> None:
    tokens = SubjectTokens(b"s" * 32)
    acme = SubjectRef(HandleType.EMAIL_DOMAIN, "acme.com.br", subject_kind=SubjectKind.ACCOUNT)
    minted = replace(claims(T0.replace(microsecond=0) + timedelta(minutes=15)), about=acme)
    token = tokens.mint(minted)
    assert "acme" not in token
    assert tokens.verify(token, now=T0).about == acme


def test_subject_token_rejects_tampering_other_secrets_and_expiry() -> None:
    tokens = SubjectTokens(b"s" * 32)
    token = tokens.mint(claims(T0 + timedelta(minutes=15)))
    prefix, payload, signature = token.split(".")
    for forged in (f"{prefix}.{payload}x.{signature}", f"{prefix}.{payload}.{signature[:-2]}aa", "garbage"):
        with pytest.raises(Unauthenticated):
            tokens.verify(forged, now=T0)
    with pytest.raises(Unauthenticated):
        SubjectTokens(b"t" * 32).verify(token, now=T0)
    with pytest.raises(Unauthenticated):
        tokens.verify(token, now=T0 + timedelta(minutes=16))


def experiment(**extra: object) -> Experiment:
    values: dict[str, object] = {
        "name": "pilot",
        "fraction_with_memory": 0.5,
        "salt": "s1",
        "starts_at": T0 - timedelta(days=1),
    } | extra
    return Experiment(**values)  # type: ignore[arg-type]


@given(st.uuids())
def test_the_draw_is_stable_and_in_the_unit_interval(handle: UUID) -> None:
    assert draw("s1", handle) == draw("s1", handle)
    assert 0 <= draw("s1", handle) < 1


def test_assignment_follows_fraction_scope_shadow_and_critical_sources() -> None:
    handle = uuid4()
    everyone = assign(
        [experiment(fraction_with_memory=1.0)],
        source_id=SOURCE,
        critical_source=False,
        oldest_handle_id=handle,
        now=T0,
    )
    nobody = assign(
        [experiment(fraction_with_memory=0.0)],
        source_id=SOURCE,
        critical_source=False,
        oldest_handle_id=handle,
        now=T0,
    )
    assert everyone is not None
    assert everyone.group == "memory"
    assert not everyone.holdout
    assert nobody is not None
    assert nobody.label == "pilot:control"
    assert nobody.holdout
    shadow = assign(
        [experiment(shadow_source_ids=frozenset({SOURCE}))],
        source_id=SOURCE,
        critical_source=False,
        oldest_handle_id=handle,
        now=T0,
    )
    assert shadow is not None
    assert shadow.group == "shadow"
    assert (
        assign(
            [experiment(fraction_with_memory=0.0)],
            source_id=SOURCE,
            critical_source=True,
            oldest_handle_id=handle,
            now=T0,
        )
        is None
    )
    assert (
        assign(
            [experiment(source_ids=frozenset({uuid4()}))],
            source_id=SOURCE,
            critical_source=False,
            oldest_handle_id=handle,
            now=T0,
        )
        is None
    )
    assert (
        assign(
            [experiment(ends_at=T0 - timedelta(hours=1))],
            source_id=SOURCE,
            critical_source=False,
            oldest_handle_id=handle,
            now=T0,
        )
        is None
    )


def test_privacy_guard_keeps_the_highest_version_dedupes_and_expires() -> None:
    guard = PrivacyGuard()
    profile, handle = uuid4(), uuid4()
    row = {
        "id": uuid4(),
        "kind": "erasure",
        "profile_ids": [profile],
        "handle_ids": [handle],
        "privacy_version": 3,
        "created_at": T0,
    }
    assert guard.due(T0)
    assert guard.stale(T0)
    guard.apply([row, {**row, "id": uuid4(), "privacy_version": 2}], T0)
    assert guard.required_version(profile) == 3
    mark = guard.handle_mark(handle)
    assert mark is not None
    assert mark.kind == "erasure"
    assert not guard.due(T0 + timedelta(seconds=1))
    assert guard.due(T0 + timedelta(seconds=2))
    assert guard.window_start(T0) == T0 - timedelta(seconds=120)
    guard.apply([row], T0 + timedelta(hours=73))
    assert guard.required_version(profile) is None
    assert guard.handle_mark(handle) is None
    assert guard.stale(T0 + timedelta(hours=73, seconds=31))


def draft(n: int) -> ReceiptDraft:
    return ReceiptDraft(
        uuid4(), SPACE, "p1", n, ReceiptKind.READ, ReceiptSurface.PACK, Audience.CUSTOMER_AGENT, T0
    )


def test_receipt_buffer_marks_full_then_exhausted() -> None:
    buffer = ReceiptBuffer(capacity=2)
    buffer.add([draft(1)])
    assert not buffer.full
    buffer.add([draft(2)])
    assert buffer.full
    assert not buffer.exhausted
    buffer.add([draft(3), draft(4)])
    assert buffer.exhausted
    assert [d.producer_seq for d in buffer.take()] == [1, 2, 3, 4]
    assert len(buffer) == 0


def test_timeline_cursor_pages_strictly_older_rows() -> None:
    docs = newest_first(
        [
            HistoryDoc(
                f"episode:{i}", ItemKind.EPISODE, "x", T0 - timedelta(days=i), ItemAttrs("episode", "c")
            )
            for i in range(5)
        ]
    )
    page = after_cursor(docs, encode_cursor(docs[1]))
    assert [d.id for d in page] == ["episode:2", "episode:3", "episode:4"]
    with pytest.raises(InvalidInput):
        after_cursor(docs, "!!!")


def test_a_live_turn_is_filed_like_the_memory_item_it_will_become() -> None:
    handle = uuid4()

    def attrs(doc: dict[str, object], topics: tuple[str, ...] = ()) -> ItemAttrs:
        return live_attrs(
            LiveLine(uuid4(), {"visibility": "public", **doc}, (handle,)), SubjectKind.PERSON, topics
        )

    message = attrs({"kind": "message", "visibility": "internal"}, ("health",))
    assert (message.category, message.topics, message.visibility.value) == (
        "history",
        ("health",),
        "internal",
    )
    assert attrs({"kind": "action", "visibility": "public"}).category == "action"
    invoice = attrs(
        {"kind": "system_event", "object_refs": [{"type": "invoice", "namespace": "erp", "id": "7"}]}
    )
    assert (invoice.category, invoice.handle_ids) == ("invoice", (handle,))
    refs = [
        {"type": "invoice", "namespace": "erp", "id": "7"},
        {"type": "order", "namespace": "shop", "id": "9"},
    ]
    contest = attrs({"kind": "message", "object_refs": refs}, ("health",))
    assert (contest.category, contest.topics) == ("invoice", ("health", "order"))
    assert attrs({"kind": "action", "object_refs": refs}).category == "action"
    assert attrs({"kind": "system_event"}).category == "history"
