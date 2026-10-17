"""A number many people use: marked ambiguous by three ids of one type in a day or five in a
month, and what it joined before is undone."""

from niadra.contracts.common import Handle
from niadra.domain.vocabulary import HandleStatus, HandleType
from tests.unit.identity.conftest import Env, handle

T = HandleType
STORE = handle(T.PHONE_E164, "+55 11 3456-7890")


def customer(n: int) -> Handle:
    return handle(T.EMAIL, f"cliente{n}@acme.com.br")


async def test_a_store_number_used_by_someone_new_each_day_is_shared_within_a_month(env: Env) -> None:
    people = [customer(n) for n in range(1, 6)]
    for person in people[:4]:
        await env.attach(STORE, person)
        env.cell.clock.advance(days=1)
    # Four people in four days never make three in a day: the number joined them all.
    assert (await env.find(STORE)).status is HandleStatus.ACTIVE
    assert await env.same(*people[:4])
    await env.attach(STORE, people[4])
    assert (await env.find(STORE)).status is HandleStatus.AMBIGUOUS
    await env.drain()
    profiles = {(await env.profile(p)).profile_id for p in people}
    assert len(profiles) == 5


async def test_one_person_with_three_emails_over_a_month_keeps_the_number(env: Env) -> None:
    for person in (customer(1), customer(2), customer(3)):
        await env.attach(STORE, person)
        env.cell.clock.advance(days=3)
    assert (await env.find(STORE)).status is HandleStatus.ACTIVE
    assert await env.same(STORE, customer(1), customer(3))
