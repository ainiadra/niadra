"""Who a conversation is with decides its audience, and only ever narrows the key's."""

from uuid import UUID

import pytest

from niadra.domain.common.space import SourcePrincipal, SpaceRef
from niadra.domain.policy.audience import claimed_kind, conversation_audience, in_conversation, is_task
from niadra.domain.vocabulary import Audience, Environment, HandleType, SubjectKind, Verification

INTERNAL, CUSTOMER = Audience.INTERNAL_AGENT, Audience.CUSTOMER_AGENT


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        (SubjectKind.STAFF, INTERNAL),
        (SubjectKind.PERSON, CUSTOMER),
        (SubjectKind.ACCOUNT, CUSTOMER),
        (SubjectKind.PARTNER, CUSTOMER),
        (None, CUSTOMER),  # no profile yet: unproven is a customer
    ],
)
def test_an_internal_keys_conversation_is_internal_only_with_staff(
    subject: SubjectKind | None, expected: Audience
) -> None:
    assert conversation_audience(INTERNAL, subject, conversation=True) is expected


@pytest.mark.parametrize("subject", [*SubjectKind, None])
def test_a_task_keeps_the_keys_audience_whoever_it_is_about(subject: SubjectKind | None) -> None:
    assert conversation_audience(INTERNAL, subject, conversation=False) is INTERNAL


@pytest.mark.parametrize("audience", [CUSTOMER, Audience.ANALYST, Audience.HUMAN])
def test_the_subject_never_widens_a_declared_audience(audience: Audience) -> None:
    for conversation in (True, False):
        assert conversation_audience(audience, SubjectKind.STAFF, conversation=conversation) is audience


def test_a_customers_own_identifier_claiming_staff_reads_as_a_customer() -> None:
    for type in (HandleType.PHONE_E164, HandleType.EMAIL, HandleType.WA_ID, HandleType.APP_USER_ID):
        assert claimed_kind(type, SubjectKind.STAFF) is SubjectKind.PERSON
    assert claimed_kind(HandleType.SYSTEM_ID, SubjectKind.STAFF) is SubjectKind.STAFF
    assert claimed_kind(HandleType.SYSTEM_ID, None) is SubjectKind.PERSON
    assert claimed_kind(HandleType.ORG_REGISTRY_HMAC, None) is SubjectKind.ACCOUNT


def _caller(audience: Audience) -> SourcePrincipal:
    space = SpaceRef(UUID(int=1), UUID(int=2), "us-east-2", Environment.SANDBOX)
    return SourcePrincipal(space, UUID(int=3), "k", frozenset(), audience, Verification.V2, frozenset())


def test_the_caller_is_narrowed_only_in_a_conversation_with_someone_but_staff() -> None:
    internal = _caller(INTERNAL)
    assert in_conversation(internal, SubjectKind.STAFF, "chat-1") is internal
    assert in_conversation(internal, SubjectKind.PERSON, None) is internal
    narrowed = in_conversation(internal, SubjectKind.PERSON, "chat-1")
    assert narrowed.audience is CUSTOMER
    assert (narrowed.source_id, narrowed.key_id) == (internal.source_id, internal.key_id)
    customer = _caller(CUSTOMER)
    assert in_conversation(customer, SubjectKind.STAFF, "chat-1") is customer


@pytest.mark.parametrize(
    ("view", "task_id", "expected"),
    [("task:drafting", None, True), ("chat", "t-1", True), ("chat", None, False), (None, None, False)],
)
def test_a_task_is_a_task_view_or_a_task_id(view: str | None, task_id: str | None, expected: bool) -> None:
    assert is_task(view, task_id) is expected
