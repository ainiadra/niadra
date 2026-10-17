"""The unit-of-work factory of the tests connects as `niadra_app`, which row-level security restricts;
`restricted_uow` is kept as a name for the tests that are about isolation between spaces."""

import pytest

from tests.integration.conftest import CloningUnitOfWork


@pytest.fixture
def restricted_uow(pg_uow: CloningUnitOfWork) -> CloningUnitOfWork:
    return pg_uow
