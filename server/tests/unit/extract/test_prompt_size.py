"""The extraction prompt's size, part by part (p24): a rule that cannot apply is not sent, and every rule
that applies reads word for word.

The prompt doubled between 30/09 and 09/10/2026. On the extraction eval's 144 sessions of dataset v2 (no
object, no link): about 1,920 input tokens a call on 30/09, 2,950
with p23, 2,270 with p24, with the same facts asked for, values kept, wrong numbers and leaks. The budgets
below are in characters, so no tokenizer is needed, a little above what p24 sends; raising one is a decision
to make, with its measurement.
"""

import json
from dataclasses import replace
from typing import Any

from niadra.domain.common.ids import uuid7
from niadra.domain.extract import prompt
from niadra.domain.extract.prompt import KnownObject, build_prompt, output_schema, system_message
from niadra.domain.extract.rules import OBSERVATION, PredicateRule
from niadra.domain.records import LinkRecord, ObjectKey
from niadra.domain.vocabulary import Speaker
from tests.unit.extract.helpers import HANDLE, T0, event, material, rules

PLAIN_INSTRUCTIONS_CHARS = 7_100
"""A customer's batch with no object and no link: 6,845 characters with p24 (9,468 with p23)."""
FULL_INSTRUCTIONS_CHARS = 9_700
"""Every rule applicable: an object, a link, a staff session, observed operations and every predicate flag."""
PLAIN_SCHEMA_CHARS = 2_500
FULL_SCHEMA_CHARS = 3_000
ORDER = ObjectKey("order", "shop", "o-1")


def every_flag() -> Any:
    found = rules()
    extra = (
        PredicateRule(OBSERVATION, "object"),
        PredicateRule("org_email", "contact", organization=True),
        PredicateRule("order_deadline", "object", observes="deadline"),
    )
    return replace(found, predicates=(*found.predicates, *extra), staff=True)


def instructions_of(system: str) -> str:
    return system[: system.index("\nPredicates (name")]


def schema_chars(schema: dict[str, Any]) -> int:
    return len(json.dumps(schema, separators=(",", ":")))


def test_a_customers_batch_with_nothing_to_cite_stays_within_its_budget() -> None:
    p = build_prompt(material([event(Speaker.CUSTOMER, "oi", 1)]), rules(), flagged=frozenset(), spans={})
    assert len(instructions_of(p.system)) <= PLAIN_INSTRUCTIONS_CHARS
    assert schema_chars(p.schema) <= PLAIN_SCHEMA_CHARS


def test_a_batch_where_every_rule_applies_stays_within_its_budget() -> None:
    link = LinkRecord(uuid7(), HANDLE, uuid7(), "client_contact", False, T0, None)
    batch = replace(
        material([event(Speaker.CUSTOMER, "Sobre o pedido, entregar só de manhã", 1)]),
        objects=(KnownObject(ORDER, T0, True),),
        links=(link,),
        observed_meanings=(("refund", "devolução do valor pago"),),
    )
    p = build_prompt(batch, every_flag(), flagged=frozenset(), spans={})
    assert len(instructions_of(p.system)) <= FULL_INSTRUCTIONS_CHARS
    assert schema_chars(p.schema) <= FULL_SCHEMA_CHARS


def test_every_rule_reads_word_for_word_when_it_applies() -> None:
    system = system_message(every_flag())
    for rule in (
        prompt._OBJECT_RULE,
        prompt._STAFF_RULE,
        prompt._ORGANIZATION_RULE,
        prompt._OBSERVES_RULE,
        prompt._OBSERVATION_RULE,
        prompt._SEEN_RULE,
        prompt._INSTRUCTIONS_RULE,
    ):
        assert rule in system
    assert "instructions" in output_schema(every_flag())["properties"]


def test_a_rule_that_cannot_apply_is_not_sent() -> None:
    system = system_message(rules(), objects=False, links=False, seen=False)
    for rule in (
        prompt._OBJECT_RULE,
        prompt._STAFF_RULE,
        prompt._ORGANIZATION_RULE,
        prompt._OBSERVES_RULE,
        prompt._OBSERVATION_RULE,
        prompt._SEEN_RULE,
        prompt._INSTRUCTIONS_RULE,
    ):
        assert rule not in system
    # What every batch needs stays.
    assert "a turn that commits to several" in system
    assert "is no item unless it gives the customer a time" in system
    assert "An instruction or contact preference a person states stands as they said it" in system
    assert "instructions" not in output_schema(rules(), instructions=False)["properties"]


def test_the_spaces_observed_operations_close_the_user_part_after_the_batch() -> None:
    """p25: right before the answer, as in p23. At the head of the user part (p24) the model tagged fewer
    items with the operation a meaning names, so the billing agent's action could not close them: with the
    space's observed meanings and the customer's shipment, the discount a voice agent promised was tagged
    `redeliver` (what this space records a discount as) in 18 of 18 runs with p23, 8 of 18 with p24 and 18 of
    18 with p25 (pt-075, pt-080, pt-085 of dataset v2, 6 runs each); promise_action fell from 95.7% to 89.3%
    in the region (run 2026-10-10-ac701b)."""
    link = LinkRecord(uuid7(), HANDLE, uuid7(), "client_contact", False, T0, None)
    meanings = (("redeliver", "O agente de cobrança solicita um abatimento no valor"),)
    batch = replace(
        material([event(Speaker.CUSTOMER, "A caixa chegou amassada", 1)]),
        objects=(KnownObject(ORDER, T0, True),),
        links=(link,),
        observed_meanings=meanings,
    )
    p = build_prompt(batch, rules(), flagged=frozenset(), spans={})
    lines = p.user.splitlines()
    assert lines[0].startswith("reference_time: ")
    seen = lines.index("operations_seen:")
    assert lines[seen:] == [
        "operations_seen:",
        '- redeliver: "O agente de cobrança solicita um abatimento no valor"',
    ]
    assert seen > lines.index("objects:")
    assert seen > lines.index("links:")
    assert prompt._SEEN_RULE in p.system


HOURS_RULE_CHARS = 560
"""The hours rule (p26): 531 characters, sent only with a customer's batch that names a time of day and a way
to be contacted."""
HOURS_SCHEMA_CHARS = 480
"""Its place in the answer: 454 characters."""


def test_the_hours_come_only_with_a_customers_hours_and_within_their_budget() -> None:
    def built(text: str) -> Any:
        return build_prompt(
            material([event(Speaker.CUSTOMER, text, 1)]), rules(), flagged=frozenset(), spans={}
        )

    plain, hours = built("oi"), built("não me liguem antes das 9h")
    assert 0 < len(instructions_of(hours.system)) - len(instructions_of(plain.system)) <= HOURS_RULE_CHARS
    assert 0 < schema_chars(hours.schema) - schema_chars(plain.schema) <= HOURS_SCHEMA_CHARS
