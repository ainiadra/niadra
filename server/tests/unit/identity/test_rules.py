from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from niadra.domain.identity.graph import connected_components
from niadra.domain.identity.model import pseudonym
from niadra.domain.identity.rules import (
    Rejection,
    can_assert,
    choose_anchor,
    fanout_exceeded,
    handle_weight,
    looks_recycled,
    merge_rejection,
    phones_to_demote,
    profile_kind,
)
from niadra.domain.identity.suggest import Candidate, suggest
from niadra.domain.records import HandleRecord
from niadra.domain.vocabulary import AssertionMethod, HandleStatus, HandleType, SubjectKind, Verification

T = HandleType
NOW = datetime(2026, 9, 22, 17, tzinfo=UTC)
M = AssertionMethod


def rec(
    n: int,
    type: HandleType = T.EMAIL,
    *,
    level: Verification = Verification.V0,
    scope: str = "",
    kind: SubjectKind = SubjectKind.PERSON,
    status: HandleStatus = HandleStatus.ACTIVE,
    seen: datetime = NOW,
    value: str | None = None,
) -> HandleRecord:
    return HandleRecord(UUID(int=n), type, scope, kind, value or f"v{n}", status, level, seen, seen)


def test_anchor_prefers_the_highest_level_then_the_smallest_id() -> None:
    assert choose_anchor(
        [rec(5, level=Verification.V2), rec(1), rec(9, level=Verification.V2)]
    ).handle_id == UUID(int=5)
    assert choose_anchor([rec(7), rec(3)]).handle_id == UUID(int=3)


def test_system_event_anchors_on_the_subjects_system_id() -> None:
    handles = [rec(1, level=Verification.V3), rec(8, T.SYSTEM_ID, scope="erp")]
    assert choose_anchor(handles, system_event=True).type is T.SYSTEM_ID
    assert choose_anchor(handles).handle_id == UUID(int=1)


def test_weak_or_inactive_handles_are_not_evidence() -> None:
    assert can_assert(rec(1))
    assert not can_assert(rec(2, T.EMAIL_DOMAIN, kind=SubjectKind.ACCOUNT))
    assert not can_assert(rec(3, T.ANON_ID))
    assert not can_assert(rec(4, status=HandleStatus.AMBIGUOUS))


@pytest.mark.parametrize(
    ("side_a", "side_b", "method", "force", "expected"),
    [
        ([rec(1)], [rec(2)], M.SAME_EVENT, False, None),
        (
            [rec(1, level=Verification.V3)],
            [rec(2, level=Verification.V3)],
            M.SAME_EVENT,
            False,
            Rejection.VERIFIED_PROFILES,
        ),
        ([rec(1, level=Verification.V3)], [rec(2, level=Verification.V3)], M.SAME_EVENT, True, None),
        ([rec(1, level=Verification.V3)], [rec(2, level=Verification.V2)], M.SAME_EVENT, False, None),
        (
            [rec(1, T.GOV_ID_HMAC, scope="BR")],
            [rec(2, T.GOV_ID_HMAC, scope="BR")],
            M.OTP,
            True,
            Rejection.ONE_PER_SCOPE,
        ),
        ([rec(1, T.GOV_ID_HMAC, scope="BR")], [rec(2, T.GOV_ID_HMAC, scope="PT")], M.OTP, False, None),
        ([rec(1, T.SYSTEM_ID, scope="crm")], [rec(2, T.SYSTEM_ID, scope="erp")], M.SAME_EVENT, False, None),
        ([rec(1)], [rec(2, kind=SubjectKind.ACCOUNT)], M.EXPLICIT_IDENTIFY, True, Rejection.SUBJECT_KIND),
        (
            [rec(1, kind=SubjectKind.ACCOUNT)],
            [rec(2, kind=SubjectKind.ACCOUNT)],
            M.SAME_EVENT,
            False,
            Rejection.ORGANIZATION,
        ),
        (
            [rec(1, kind=SubjectKind.ACCOUNT)],
            [rec(2, kind=SubjectKind.ACCOUNT)],
            M.SYSTEM_IMPORT,
            False,
            None,
        ),
        ([rec(1)], [rec(2)], M.CHANNEL_ROTATION, False, Rejection.ROTATION),
        ([rec(1, status=HandleStatus.BLOCKED)], [rec(2)], M.SAME_EVENT, True, Rejection.HANDLE_NOT_ACTIVE),
    ],
)
def test_merge_protections(
    side_a: list[HandleRecord],
    side_b: list[HandleRecord],
    method: AssertionMethod,
    force: bool,
    expected: Rejection | None,
) -> None:
    endpoints = [side_a[0], side_b[0]]
    assert merge_rejection(method, endpoints, side_a, side_b, force=force) is expected


def test_the_weakest_phone_beyond_the_limit_is_demoted() -> None:
    phones = [
        rec(1, T.PHONE_E164, level=Verification.V2),
        rec(2, T.PHONE_E164, seen=NOW - timedelta(days=30)),
        rec(3, T.PHONE_E164),
        rec(4, T.PHONE_E164),
        rec(5),
    ]
    assert [h.handle_id for h in phones_to_demote(phones)] == [UUID(int=2)]
    assert phones_to_demote(phones[:3]) == []


def test_fanout_counts_distinct_ids_of_one_type() -> None:
    assert not fanout_exceeded([rec(1), rec(2), rec(2), rec(3, T.GOV_ID_HMAC, scope="BR")])
    assert fanout_exceeded([rec(1), rec(2), rec(3)])


def test_a_long_silent_phone_looks_recycled() -> None:
    assert looks_recycled(rec(1, T.PHONE_E164, seen=NOW - timedelta(days=200)), NOW)
    assert not looks_recycled(rec(1, T.PHONE_E164, seen=NOW - timedelta(days=20)), NOW)
    assert not looks_recycled(rec(1, T.EMAIL, seen=NOW - timedelta(days=400)), NOW)


def test_profile_kind_comes_from_the_handles() -> None:
    assert profile_kind([rec(1, kind=SubjectKind.PARTNER)]) is SubjectKind.PARTNER
    with pytest.raises(ValueError, match="subject kind"):
        profile_kind([rec(1), rec(2, kind=SubjectKind.ACCOUNT)])


def test_components_split_on_a_bridge_into_three() -> None:
    a, b, c, d = (UUID(int=i) for i in (1, 2, 3, 4))
    assert connected_components([a, b, c, d], [(a, b), (b, c)]) == [frozenset({a, b, c}), frozenset({d})]
    assert connected_components([a, b, c], []) == [frozenset({a}), frozenset({b}), frozenset({c})]


def test_pseudonym_is_stable_and_prefixed() -> None:
    assert pseudonym(b"\x00" * 32) == pseudonym(b"\x00" * 32) == "p_aaaaaaaaaaaaaaaa"
    assert pseudonym(b"\x01" * 32) != pseudonym(b"\x00" * 32)


def test_suggestions_pair_gmail_aliases_and_ignore_homonyms() -> None:
    p1, p2, p3 = UUID(int=100), UUID(int=200), UUID(int=300)
    candidates = [
        Candidate(UUID(int=1), p1, T.EMAIL, "ana.silva+promo@gmail.com"),
        Candidate(UUID(int=2), p2, T.EMAIL, "anasilva@gmail.com"),
        Candidate(UUID(int=3), p3, T.EMAIL, "maria.souza@acme.com"),
        Candidate(UUID(int=4), p1, T.EMAIL, "maria.souza@other.com"),
    ]
    found = suggest(candidates)
    assert [(s.handle_a, s.handle_b) for s in found] == [(UUID(int=1), UUID(int=2))]
    assert found[0].features == ("gmail_canonical",)


def test_weak_agreements_of_two_profiles_add_up() -> None:
    """An e-mail local part (6.1) and a phone's subscriber digits (8.2) are each too weak for review; two
    profiles that agree on both are worth a look (14.4)."""
    p1, p2, p3 = UUID(int=100), UUID(int=200), UUID(int=300)
    candidates = [
        Candidate(UUID(int=1), p1, T.EMAIL, "mariana.souza@acme.com.br"),
        Candidate(UUID(int=2), p1, T.PHONE_E164, "+5511987654321"),
        Candidate(UUID(int=3), p2, T.EMAIL, "mariana.souza@outlook.com"),
        Candidate(UUID(int=4), p2, T.PHONE_E164, "+5521987654321"),
        Candidate(UUID(int=5), p3, T.EMAIL, "mariana.souza@other.com.br"),
    ]
    [found] = suggest(candidates)
    assert (found.handle_a, found.handle_b) == (UUID(int=2), UUID(int=4))  # the strongest agreement names it
    assert found.features == ("email_local", "phone_subscriber")
    assert found.score == pytest.approx(14.36, abs=0.01)


def test_suggestions_skip_huge_blocks() -> None:
    crowd = [
        Candidate(UUID(int=i), UUID(int=1000 + i), T.PHONE_E164, f"+55{i:02d}912345678") for i in range(30)
    ]
    assert suggest(crowd) == []


def test_the_month_window_takes_its_own_limit() -> None:
    four = [rec(1), rec(2), rec(3), rec(4)]
    assert fanout_exceeded(four)
    assert not fanout_exceeded(four, limit=5)
    assert fanout_exceeded([*four, rec(5)], limit=5)


def test_the_weight_of_an_identifier_follows_its_type_and_the_level_it_proved() -> None:
    # The labels the site names, as the API and the Console show them.
    assert handle_weight(rec(1, T.EMAIL_DOMAIN)) == "hint"
    assert handle_weight(rec(2, T.SYSTEM_ID, scope="crm")) == "system"
    assert handle_weight(rec(3)) == "declared"
    assert handle_weight(rec(4, level=Verification.V2)) == "verified"
    assert handle_weight(rec(5, level=Verification.V4)) == "authenticated"
