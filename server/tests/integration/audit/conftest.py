"""`rls_uow`: the factory of the tests, which connects as `niadra_app` under row-level security."""

import pytest

from tests.integration.conftest import CloningUnitOfWork


@pytest.fixture
def rls_uow(pg_uow: CloningUnitOfWork) -> CloningUnitOfWork:
    return pg_uow
