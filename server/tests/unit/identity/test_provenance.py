"""Whose rows a reader of a profile sees while it holds unconfirmed links (5.3): the pure rule."""

from dataclasses import replace
from uuid import UUID

from niadra.domain.common.space import PersonPrincipal
from niadra.domain.compile import Variant, untrimmed_key
from niadra.domain.identity.provenance import (
    compiled_for,
    derivation_view,
    governance_view,
    hidden_marker,
    only_unconfirmed,
    provisional_reader,
    reader_view,
    session_view,
)
from niadra.domain.records import ProfileRecord
from niadra.domain.vocabulary import SubjectKind
from niadra.testing.cell import principal

PHONE = UUID("0192f5a0-0000-7000-8000-00000000f001")
CRM = UUID("0192f5a0-0000-7000-8000-00000000f002")
CLAIMED_EMAIL = UUID("0192f5a0-0000-7000-8000-00000000f003")
CLAIMED_NUMBER = UUID("0192f5a0-0000-7000-8000-00000000f004")

PROFILE = ProfileRecord(
    profile_id=UUID("0192f5a0-0000-7000-8000-00000000f0ff"),
    kind=SubjectKind.PERSON,
    pseudonym="p_x",
    membership_version=3,
    privacy_version=1,
    handle_ids=(PHONE, CRM, CLAIMED_EMAIL, CLAIMED_NUMBER),
    provisional_handle_ids=(CLAIMED_EMAIL, CLAIMED_NUMBER),
)


def test_a_profile_without_unconfirmed_links_is_its_own_view() -> None:
    plain = replace(PROFILE, provisional_handle_ids=())
    assert reader_view(plain) is plain
    assert reader_view(plain, (PHONE,)) is plain
    assert hidden_marker(plain) == []


def test_a_confirmed_reader_and_a_read_by_profile_see_the_confirmed_handles_only() -> None:
    for view in (reader_view(PROFILE, (PHONE,)), reader_view(PROFILE)):
        assert view.handle_ids == (PHONE, CRM)
        assert set(view.hidden_handle_ids) == {CLAIMED_EMAIL, CLAIMED_NUMBER}
        assert view.provisional_handle_ids == ()


def test_a_reader_through_an_unconfirmed_link_sees_it_and_the_confirmed_ones_never_another_link() -> None:
    view = reader_view(PROFILE, (CLAIMED_EMAIL,))
    assert view.handle_ids == (PHONE, CRM, CLAIMED_EMAIL)
    assert view.hidden_handle_ids == (CLAIMED_NUMBER,)
    assert provisional_reader(view, CLAIMED_EMAIL) == CLAIMED_EMAIL
    assert provisional_reader(view, PHONE) is None
    # A view is final: narrowing it again changes nothing.
    assert reader_view(view) is view


def test_a_conversation_only_on_an_unconfirmed_link_writes_against_its_rows_alone() -> None:
    assert session_view(PROFILE, {CLAIMED_EMAIL}).handle_ids == (CLAIMED_EMAIL,)
    # One that names a confirmed handle too is read like any reader through its handles.
    assert session_view(PROFILE, {CLAIMED_EMAIL, PHONE}).handle_ids == (PHONE, CRM, CLAIMED_EMAIL)
    assert session_view(PROFILE, {PHONE}).handle_ids == (PHONE, CRM)


def test_a_pack_serves_only_a_view_that_hides_the_same_handles() -> None:
    confirmed = reader_view(PROFILE)
    manifest = {"provenance": {"hidden_handles": hidden_marker(confirmed)}}
    assert compiled_for(manifest, confirmed)
    assert not compiled_for(manifest, reader_view(PROFILE, (CLAIMED_EMAIL,)))
    # A manifest that names no hidden handles never serves a view with something to hide, and keeps
    # serving every profile without unconfirmed links.
    assert not compiled_for({}, confirmed)
    assert compiled_for({}, replace(PROFILE, provisional_handle_ids=()))


def test_only_the_roles_that_confirm_links_see_their_rows_in_the_console() -> None:
    space = principal().space
    for roles, whole in (({"admin"}, True), ({"security"}, True), ({"review", "analysis"}, False)):
        person = PersonPrincipal(space, UUID(int=1), frozenset(roles))
        assert (governance_view(person, PROFILE) is PROFILE) is whole
    # No source key does, whatever its scopes.
    assert governance_view(principal(), PROFILE).handle_ids == (PHONE, CRM)


def test_the_reader_through_an_unconfirmed_link_has_a_key_of_its_own() -> None:
    plain = Variant(view="voice", slotted=True)  # a reader's, as stored
    theirs = replace(plain, reader_handle_id=CLAIMED_EMAIL)
    lean = replace(theirs, trim=frozenset({"fact:contract"}))
    assert theirs.key == f"{plain.key}|@{CLAIMED_EMAIL}"
    assert lean.key == f"{theirs.key}|fact:contract"
    for variant in (plain, theirs, lean):
        assert Variant.parse(variant.key) == variant
    # The trim may change under a pin; the reader never does.
    assert untrimmed_key(lean.key) == theirs.key
    assert untrimmed_key(theirs.key) != untrimmed_key(plain.key)


def test_what_the_profile_derives_counts_its_confirmed_handles_only() -> None:
    derived = derivation_view(PROFILE)
    assert derived.handle_ids == (PHONE, CRM)
    assert set(derived.hidden_handle_ids) == {CLAIMED_EMAIL, CLAIMED_NUMBER}
    plain = replace(PROFILE, provisional_handle_ids=())
    assert derivation_view(plain) is plain


def test_a_conversation_is_unconfirmed_only_when_every_handle_it_names_is() -> None:
    provisional = PROFILE.provisional_handle_ids
    assert only_unconfirmed([CLAIMED_EMAIL], provisional)
    assert only_unconfirmed([CLAIMED_EMAIL, CLAIMED_NUMBER], provisional)
    # The declaring call names the caller's own number too: that conversation is the caller's.
    assert not only_unconfirmed([PHONE, CLAIMED_EMAIL], provisional)
    assert not only_unconfirmed([], provisional)
