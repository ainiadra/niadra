"""Identity invariants (CODE_GUIDE "Tests"): unmerge exactly undoes merge; attach is idempotent."""

import asyncio
import copy

from hypothesis import given, settings
from hypothesis import strategies as st

from niadra.contracts.common import Handle
from niadra.domain.common.ids import uuid7
from niadra.domain.identity.model import AssertionState
from niadra.domain.tasks import TaskName
from niadra.domain.vocabulary import AssertionMethod, HandleType, SubjectKind, Verification
from niadra.testing.cell import SPACE_ID, Cell
from tests.unit.identity.conftest import Env, handle, make_env

POOL = [handle(HandleType.EMAIL, f"c{i}@acme.com.br") for i in range(6)] + [
    handle(HandleType.PHONE_E164, f"+55 11 9{i}{i}{i}1-000{i}") for i in range(6)
]
LEVELS = st.sampled_from([Verification.V0, Verification.V1, Verification.V2])
profiles_strategy = st.lists(
    st.lists(st.sampled_from(range(len(POOL))), min_size=1, max_size=3, unique=True),
    min_size=2,
    max_size=4,
)


async def partition(env: Env, handles: list[Handle]) -> dict[str, tuple[str, str]]:
    result = {}
    for h in handles:
        profile = await env.profile(h)
        result[h.value] = (str(profile.profile_id), profile.pseudonym)
    return result


@settings(max_examples=40, deadline=None)
@given(groups=profiles_strategy, pick=st.tuples(st.integers(0, 3), st.integers(0, 3)))
def test_unmerge_exactly_undoes_merge(groups: list[list[int]], pick: tuple[int, int]) -> None:
    async def scenario() -> None:
        env = make_env(Cell())
        seen: set[int] = set()
        clusters = []
        for group in groups:
            fresh = [i for i in group if i not in seen]
            if fresh:
                seen.update(fresh)
                clusters.append([POOL[i] for i in fresh])
                await env.attach(*clusters[-1])
        if len(clusters) < 2:
            return
        first, second = clusters[pick[0] % len(clusters)], clusters[pick[1] % len(clusters)]
        if first is second:
            second = clusters[(pick[0] + 1) % len(clusters)]
        everything = [h for c in clusters for h in c]
        before = await partition(env, everything)

        await env.service.create_assertion(
            SPACE_ID,
            [first[0], second[0]],
            method=AssertionMethod.EXPLICIT_IDENTIFY,
            subject_kind=SubjectKind.PERSON,
            actor="test",
            reason=None,
        )
        await env.drain()
        assert await env.same(*first, *second)
        applied = [
            a
            for a in await env.repo.list_assertions(None, AssertionState.APPLIED, None, 100)
            if a.method is AssertionMethod.EXPLICIT_IDENTIFY
        ]
        await env.service.retract(SPACE_ID, applied[0].assertion_id, actor="test", reason=None)
        await env.drain()
        assert await partition(env, everything) == before

    asyncio.run(scenario())


attach_calls = st.lists(
    st.tuples(st.lists(st.sampled_from(range(len(POOL))), min_size=1, max_size=4), LEVELS),
    min_size=1,
    max_size=6,
)


@settings(max_examples=40, deadline=None)
@given(calls=attach_calls)
def test_attach_is_idempotent(calls: list[tuple[list[int], Verification]]) -> None:
    async def scenario() -> None:
        env = make_env(Cell())
        for indexes, level in calls:
            handles = [POOL[i] for i in indexes]
            # Each call from its own event, as ingestion's turn and answer are: the second writes nothing.
            first = await env.attach(*handles, level=level, event_id=uuid7())
            state = copy.deepcopy(env.cell.uow.store.data)
            again = await env.attach(*handles, level=level, event_id=uuid7())
            assert again == first
            assert env.cell.uow.store.data == state
            env.outbox.take(TaskName.IDENTITY_APPLY)

    asyncio.run(scenario())


def test_new_ids_of_one_person_arriving_together_are_not_shared() -> None:
    async def scenario() -> None:
        env = make_env(Cell())
        phone, *emails = POOL[6], POOL[0], POOL[1], POOL[2]
        await env.attach(phone, *emails, level=Verification.V0)
        assert await env.same(phone, *emails)

    asyncio.run(scenario())
