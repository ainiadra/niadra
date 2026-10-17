"""The coordinate tables on PostgreSQL (24.3.4, 6.7): `contact_log` takes its rows by week through
`ensure_week_partition`, which now gives a parent without one its DEFAULT partition on the first call, and
every table of the module hides another space's rows. The partition upkeep of a database still on the
release before the module skips the parents it does not have yet (N-1)."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text

from niadra.domain.common.ids import uuid7
from niadra.domain.spaces.directory import db_name_for
from tests.integration.conftest import CloningUnitOfWork, PgServer, owner_sql

WEEK_MS = 7 * 24 * 3600 * 1000
TABLES = (
    "ownership_claims",
    "effects",
    "contact_log",
    "suppressions",
    "commitments",
    "handoffs",
    "coord_reports",
)
INSERT = text(
    "INSERT INTO contact_log (space_id, contact_id, purpose, channel, direction, source_id, state) "
    "VALUES (:space, :contact, 'marketing', 'whatsapp', 'outbound', :source, 'reserved')"
)
WHERE = text("SELECT contact_id, tableoid::regclass::text FROM contact_log")


async def test_contact_log_takes_rows_by_week_and_a_late_week_takes_in_the_default(
    pg_uow: CloningUnitOfWork,
) -> None:
    space = uuid4()
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    week = (now_ms // WEEK_MS + 10) * WEEK_MS
    async with pg_uow.system(space) as uow:
        await uow.session.execute(text("SELECT ensure_week_partition('contact_log', :ms)"), {"ms": week})
    in_week, skewed = uuid7(week + 1000), uuid7(week + 20 * WEEK_MS)
    async with pg_uow(space) as uow:
        for contact in (in_week, skewed):
            await uow.session.execute(INSERT, {"space": space, "contact": contact, "source": uuid4()})
    async with pg_uow(space) as uow:
        where = dict((await uow.session.execute(WHERE)).tuples().all())
    assert where == {in_week: f"contact_log_w{week // WEEK_MS}", skewed: "contact_log_default"}
    skewed_week = (week + 20 * WEEK_MS) // WEEK_MS * WEEK_MS
    async with pg_uow.system(space) as uow:
        await uow.session.execute(
            text("SELECT ensure_week_partition('contact_log', :ms)"), {"ms": skewed_week}
        )
    async with pg_uow(space) as uow:
        where = dict((await uow.session.execute(WHERE)).tuples().all())
    assert where[skewed] == f"contact_log_w{skewed_week // WEEK_MS}"


async def test_a_parent_without_a_default_partition_gets_one_on_the_first_week(
    pg_uow: CloningUnitOfWork, server: PgServer
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    database = db_name_for(space)
    await owner_sql(
        server,
        database,
        "CREATE TABLE week_probe (space_id uuid, probe_id uuid, PRIMARY KEY (space_id, probe_id)) "
        "PARTITION BY RANGE (probe_id)",
    )
    week = int(datetime.now(UTC).timestamp() * 1000) // WEEK_MS * WEEK_MS
    await owner_sql(server, database, "SELECT ensure_week_partition('week_probe', :ms)", ms=week)
    parts = await owner_sql(
        server,
        database,
        "SELECT inhrelid::regclass::text FROM pg_inherits"
        " WHERE inhparent = 'week_probe'::regclass ORDER BY 1",
    )
    assert [name for (name,) in parts] == ["week_probe_default", f"week_probe_w{week // WEEK_MS}"]


async def test_every_coordinate_table_hides_another_space(
    pg_uow: CloningUnitOfWork, server: PgServer
) -> None:
    space = uuid4()
    await pg_uow.ensure(space)
    secured = await owner_sql(
        server,
        db_name_for(space),
        "SELECT relname FROM pg_class WHERE relname = ANY(:tables) AND relrowsecurity",
        tables=list(TABLES),
    )
    assert {name for (name,) in secured} == set(TABLES)
    await owner_sql(
        server,
        db_name_for(space),
        "INSERT INTO contact_log (space_id, contact_id, purpose, channel, direction, source_id, state) "
        "VALUES (:other, :contact, 'marketing', 'whatsapp', 'outbound', :source, 'made')",
        other=uuid4(),
        contact=uuid7(),
        source=uuid4(),
    )
    async with pg_uow(space) as uow:
        assert (await uow.session.execute(WHERE)).all() == []


async def test_weeks_past_retention_are_dropped_whole_unless_a_held_subject_is_in_them(
    pg_uow: CloningUnitOfWork,
) -> None:
    space = uuid4()
    now = datetime.now(UTC)
    this_week = int(now.timestamp() * 1000) // WEEK_MS * WEEK_MS
    old, held_week = this_week - 20 * WEEK_MS, this_week - 19 * WEEK_MS
    async with pg_uow.system(space) as uow:
        for week in (old, held_week, this_week):
            await uow.session.execute(text("SELECT ensure_week_partition('contact_log', :ms)"), {"ms": week})
    held = b"held-subject-hmac"
    insert = text(
        "INSERT INTO contact_log (space_id, contact_id, subject_hmac, purpose, channel, direction, source_id,"
        " state) VALUES (:space, :contact, :subject, 'marketing', 'whatsapp', 'outbound', :source,"
        " 'reserved')"
    )
    async with pg_uow(space) as uow:
        for week, subject in ((old, b"other"), (held_week, held), (this_week, b"other")):
            row = {"space": space, "contact": uuid7(week + 1000), "subject": subject, "source": uuid4()}
            await uow.session.execute(insert, row)
    async with pg_uow(space) as uow:
        dropped = await uow.coordinate.drop_expired_contact_weeks(now, {held})
    assert dropped == 1
    async with pg_uow(space) as uow:
        left = sorted(
            (await uow.session.execute(text("SELECT tableoid::regclass::text FROM contact_log"))).scalars()
        )
        gone = await uow.session.scalar(
            text("SELECT to_regclass(:name)"), {"name": f"contact_log_w{old // WEEK_MS}"}
        )
    assert left == [f"contact_log_w{held_week // WEEK_MS}", f"contact_log_w{this_week // WEEK_MS}"]
    assert gone is None
    async with pg_uow(space) as uow:
        assert await uow.coordinate.drop_expired_contact_weeks(now, set()) == 1
