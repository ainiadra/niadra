"""Who a Console reader is to the policy (5.22): the audience class of the role, never a task's purpose."""

import pytest

from niadra.domain.common.errors import Forbidden
from niadra.domain.common.ids import uuid7
from niadra.domain.common.space import PersonPrincipal, SpaceRef
from niadra.domain.policy.console import console_reader
from niadra.domain.vocabulary import Audience, Environment, Scope
from niadra.testing.cell import BILLING_SOURCE, SPACE_ID, TENANT_ID, principal

SPACE = SpaceRef(SPACE_ID, TENANT_ID, "us-east-2", Environment.SANDBOX)
DECLARED = frozenset({"billing", "support"})


def person(*roles: str) -> PersonPrincipal:
    return PersonPrincipal(SPACE, uuid7(), frozenset(roles))


def test_the_most_revealing_role_decides_the_audience() -> None:
    assert console_reader(person("analysis", "security"), DECLARED).audience is Audience.HUMAN
    assert console_reader(person("review"), DECLARED).audience is Audience.REVIEWER
    assert console_reader(person("integration"), DECLARED).audience is Audience.ANALYST


def test_people_read_with_every_declared_purpose_and_keys_with_their_own() -> None:
    assert console_reader(person("admin"), DECLARED).purposes == DECLARED
    key = principal(BILLING_SOURCE, scopes=frozenset({Scope.ADMIN}))
    reader = console_reader(key, DECLARED)
    assert (reader.audience, reader.purposes, reader.source_id) == (
        key.audience,
        key.purposes,
        BILLING_SOURCE,
    )


def test_a_vendor_does_not_read_customer_memory() -> None:
    with pytest.raises(Forbidden):
        console_reader(person("vendor"), DECLARED)
