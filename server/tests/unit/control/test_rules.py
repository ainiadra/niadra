from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from niadra.domain.common.errors import Conflict, Forbidden, InvalidInput, Unavailable, VerificationNotAllowed
from niadra.domain.control.records import Cell, CellStatus, ConfigDiff, DiffStatus
from niadra.domain.control.rules import canonical_json, check_approval, check_ceiling, choose_cell, space_slug
from niadra.domain.vocabulary import Audience, Environment, Verification

T0 = datetime(2026, 9, 1, tzinfo=UTC)


def _cell(
    cell_id: str,
    capacity: int,
    *,
    region: str = "sa-east-1",
    status: CellStatus = CellStatus.ACTIVE,
    age: int = 0,
) -> Cell:
    return Cell(cell_id, region, f"https://{cell_id}", capacity, status, T0 - timedelta(days=age))


def test_placement_picks_the_cell_with_the_most_free_capacity() -> None:
    cells = [_cell("a", 10), _cell("b", 10), _cell("c", 100, region="us-east-2")]
    assert choose_cell(cells, {"a": 8, "b": 3}, "sa-east-1").cell_id == "b"


def test_placement_skips_full_and_draining_cells_and_breaks_ties_by_age() -> None:
    cells = [
        _cell("full", 2),
        _cell("draining", 50, status=CellStatus.DRAINING),
        _cell("new", 5),
        _cell("old", 5, age=30),
    ]
    assert choose_cell(cells, {"full": 2}, "sa-east-1").cell_id == "old"


def test_placement_fails_when_the_region_has_no_room() -> None:
    with pytest.raises(Unavailable):
        choose_cell([_cell("a", 1)], {"a": 1}, "sa-east-1")


def _diff(author: object, status: DiffStatus = DiffStatus.PENDING) -> ConfigDiff:
    return ConfigDiff(uuid4(), uuid4(), "policies", {}, "why", status, 0, T0, author_id=author)  # type: ignore[arg-type]


def test_the_author_cannot_approve_while_another_person_can() -> None:
    author = uuid4()
    with pytest.raises(Forbidden):
        check_approval(_diff(author), author, other_approvers=1)
    check_approval(_diff(author), uuid4(), other_approvers=1)


def test_a_lone_approver_may_approve_the_own_diff() -> None:
    author = uuid4()
    check_approval(_diff(author), author, other_approvers=0)


def test_a_decided_diff_cannot_be_approved_again() -> None:
    with pytest.raises(Conflict):
        check_approval(_diff(uuid4(), DiffStatus.APPLIED), uuid4(), other_approvers=1)


def test_space_slugs_keep_sandbox_and_production_apart() -> None:
    assert space_slug("acme", Environment.PRODUCTION) == "acme"
    assert space_slug("acme", Environment.SANDBOX) == "acme-sandbox"
    with pytest.raises(InvalidInput):
        space_slug("Acme_Corp", Environment.PRODUCTION)


def test_a_space_never_takes_a_name_the_edge_serves() -> None:
    # `mtls.<region>.api.<domain>` is the generic webhook's mTLS address: a project named `mtls` would share
    # it, and `scripts/cell.sh route` would point it at a dedicated cell, taking it from every tenant.
    with pytest.raises(InvalidInput, match="reserved"):
        space_slug("mtls", Environment.PRODUCTION)
    assert space_slug("mtls", Environment.SANDBOX) == "mtls-sandbox"


def test_canonical_json_is_stable_under_key_order() -> None:
    assert canonical_json({"b": 1, "a": [1, {"d": 2, "c": "é"}]}) == canonical_json(
        {"a": [1, {"c": "é", "d": 2}], "b": 1}
    )
    assert canonical_json({"a": 1}) == b'{"a":1}'


@pytest.mark.parametrize(
    ("audience", "ceiling", "allowed"),
    [
        (Audience.CUSTOMER_AGENT, Verification.V2, True),
        (Audience.CUSTOMER_AGENT, Verification.V3, True),  # it runs an OTP
        (Audience.CUSTOMER_AGENT, Verification.V4, False),  # a leaked agent key cannot declare V4
        (Audience.CUSTOMER_AGENT, Verification.NO_CUSTOMER, False),
        (Audience.HUMAN, Verification.V4, True),  # a human desk checks documents
        (Audience.INTERNAL_AGENT, Verification.V4, True),  # a system of record matches its records
        (Audience.INTERNAL_AGENT, Verification.NO_CUSTOMER, True),
        (Audience.ANALYST, Verification.V4, False),
    ],
)
def test_a_sources_ceiling_follows_its_audience(
    audience: Audience, ceiling: Verification, allowed: bool
) -> None:
    if allowed:
        check_ceiling(audience, ceiling)
    else:
        with pytest.raises(VerificationNotAllowed):
            check_ceiling(audience, ceiling)
