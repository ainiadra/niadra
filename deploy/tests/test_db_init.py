"""The roles and databases the chart creates before every deploy (files/db-init.sql).

The grants of the cell database must be the ones the cell's provisioner gives each space database,
so both kinds of database behave the same for niadra_app and niadra_system. Needs the server package:
`uv run --directory ../server --with pyyaml pytest ../deploy/tests`.
"""

from pathlib import Path

import pytest

DB_INIT = Path(__file__).parents[1] / "k8s" / "charts" / "niadra" / "files" / "db-init.sql"


def _lines(sql: str) -> list[str]:
    return [line.strip() for line in sql.splitlines() if line.strip() and not line.strip().startswith("--")]


def test_the_cell_database_gets_the_grants_of_a_space_database() -> None:
    provisioning = pytest.importorskip("niadra.adapters.outbound.postgres.provisioning")
    grants = getattr(provisioning, "DATABASE_GRANTS", None)
    if grants is None:
        pytest.skip("this server has no DATABASE_GRANTS yet")
    expected = _lines(grants.format(database="niadra_cell"))
    sql = _lines(DB_INIT.read_text())
    assert expected, "DATABASE_GRANTS is empty"
    starts = [i for i, line in enumerate(sql) if line == expected[0]]
    assert any(sql[i : i + len(expected)] == expected for i in starts), (
        "the niadra_cell grants in db-init.sql differ from DATABASE_GRANTS"
    )


def test_database_init_never_drops_a_database() -> None:
    sql = DB_INIT.read_text()
    assert not [line for line in _lines(sql) if "DROP DATABASE" in line.upper()]
