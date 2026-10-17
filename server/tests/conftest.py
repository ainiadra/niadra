import pytest

from niadra.testing.cell import Cell


@pytest.fixture
def cell() -> Cell:
    return Cell()
