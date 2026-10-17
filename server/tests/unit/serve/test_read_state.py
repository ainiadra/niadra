"""The read process's state (`app.read_state`): what it serves, and when it stops serving it."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from niadra.app.read_state import FRESH_FOR_S, GAP_S, SERVED_FOR_S, Change, ReadState, thread_key
from niadra.domain.vocabulary import Verification

SPACE = UUID(int=1)
HANDLE, OTHER_HANDLE = UUID(int=10), UUID(int=11)
PROFILE = UUID(int=20)
NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def state() -> tuple[ReadState, Clock]:
    clock = Clock()
    return ReadState(monotonic=clock), clock


def keep(read: ReadState, key: str = "k", started: float | None = None) -> None:
    read.store_target(
        SPACE,
        key,
        "target",
        handle_ids=(HANDLE, OTHER_HANDLE),
        profile_id=PROFILE,
        started=read.started() if started is None else started,
    )


def row(n: int, **ids: list[object]) -> dict[str, object]:
    return {"id": UUID(int=1000 + n), **ids}


def test_nothing_is_served_before_the_first_poll_which_vouches_only_for_recent_loads() -> None:
    read, clock = state()
    keep(read, "old")
    clock.t += GAP_S + 1
    keep(read, "recent")
    assert read.target(SPACE, "recent") is None
    read.polled(SPACE, [])  # its window covers the recent load, not the old one
    assert read.target(SPACE, "recent") == "target"
    assert read.target(SPACE, "old") is None


def test_a_space_polled_too_long_ago_serves_nothing_and_a_long_pause_drops_everything() -> None:
    read, clock = state()
    read.polled(SPACE, [])
    keep(read)
    clock.t += FRESH_FOR_S + 0.1
    assert read.target(SPACE, "k") is None
    read.polled(SPACE, [])
    assert read.target(SPACE, "k") == "target"
    clock.t += GAP_S + 0.1
    read.polled(SPACE, [])  # after a long pause, what was loaded before it is dropped
    assert read.target(SPACE, "k") is None


def test_a_change_to_any_member_or_to_the_profile_drops_the_target() -> None:
    for change in ({"handle_ids": [OTHER_HANDLE]}, {"profile_ids": [PROFILE]}):
        read, _ = state()
        read.polled(SPACE, [])
        keep(read)
        read.polled(SPACE, [row(1, **change)])
        assert read.target(SPACE, "k") is None


def test_a_row_already_applied_is_not_applied_again_and_other_rows_touch_nothing() -> None:
    read, clock = state()
    read.polled(SPACE, [])
    read.polled(SPACE, [row(1, handle_ids=[HANDLE])])
    clock.t += 0.01
    keep(read)
    read.polled(SPACE, [row(1, handle_ids=[HANDLE]), row(2, handle_ids=[UUID(int=99)])])
    assert read.target(SPACE, "k") == "target"


def test_a_load_that_started_before_a_change_is_refused() -> None:
    read, clock = state()
    read.polled(SPACE, [])
    started = read.started()
    clock.t += 0.01
    read.polled(SPACE, [row(1, handle_ids=[HANDLE])])
    keep(read, started=started)
    assert read.target(SPACE, "k") is None


def test_a_proof_serves_only_reads_that_need_no_more_and_only_while_valid() -> None:
    read, _ = state()
    read.polled(SPACE, [])
    thread = thread_key("call-1", None)
    assert thread == "c:call-1"
    read.store_proof(SPACE, thread, Verification.V2, NOW + timedelta(minutes=5), started=read.started())
    assert read.proof(SPACE, thread, Verification.V1, NOW) is Verification.V2
    assert read.proof(SPACE, thread, Verification.V3, NOW) is None  # a verify may have raised it
    assert read.proof(SPACE, thread, Verification.V1, NOW + timedelta(minutes=5)) is None
    read.polled(SPACE, [row(1, threads=[thread])])  # closed, or proven again
    assert read.proof(SPACE, thread, Verification.V1, NOW) is None


def test_a_task_names_its_session_before_the_conversation() -> None:
    assert thread_key("c", "t") == "t:t"
    assert thread_key(None, None) is None


def test_spaces_read_recently_are_polled_and_forgotten_after_a_while() -> None:
    read, clock = state()
    read.read(SPACE)
    assert read.spaces_to_poll([UUID(int=2)]) == [SPACE, UUID(int=2)]
    clock.t += SERVED_FOR_S + 1
    assert read.spaces_to_poll([]) == []


def test_the_synchronous_state_serves_without_polls_and_drops_on_invalidate() -> None:
    clock = Clock()
    read = ReadState(synchronous=True, monotonic=clock)
    keep(read)
    assert read.target(SPACE, "k") == "target"
    read.invalidate(SPACE, Change(handle_ids=frozenset({HANDLE})))
    assert read.target(SPACE, "k") is None
    assert len(read) == 0


async def test_the_in_memory_commit_tells_what_the_triggers_would_write() -> None:
    from niadra.adapters.memory.uow import InMemoryUnitOfWorkFactory
    from niadra.domain.identity.handles import HandleKey, NewHandle
    from niadra.domain.vocabulary import HandleStatus, HandleType, SubjectKind

    uow = InMemoryUnitOfWorkFactory()
    told: list[Change] = []
    uow.listeners.append(lambda space_id, change: told.append(change))
    key = HandleKey(HandleType.PHONE_E164, "", "+5511900001111")
    async with uow(SPACE) as tx:
        await tx.identity.add_handles([NewHandle(key, SubjectKind.PERSON, HandleStatus.ACTIVE)], NOW)
        handle = await tx.identity.find_handle(key.type, key.scope, key.value)
        assert handle is not None
        profile = await tx.identity.create_profile(PROFILE, SubjectKind.PERSON, NOW)
        await tx.identity.set_membership([handle.handle_id], PROFILE, profile.membership_version)
    assert told == [Change(frozenset({handle.handle_id}), frozenset({PROFILE}))]
    async with uow(SPACE) as tx:
        await tx.identity.touch_handles([handle.handle_id], NOW + timedelta(minutes=1))
    assert len(told) == 1  # what every batch touches fires nothing
    async with uow(SPACE) as tx:
        await tx.identity.set_status([handle.handle_id], HandleStatus.SUSPECTED_RECYCLED)
    assert told[-1] == Change(handle_ids=frozenset({handle.handle_id}))
