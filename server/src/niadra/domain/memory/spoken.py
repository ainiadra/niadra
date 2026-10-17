"""Whose words a conversation holds, for the mirror rule (5.12).

The policy hands back to the customer, from V1, what they told the company themselves; what the company
holds about them keeps its category's level. A conversation is the customer's (`customer`) when only they
spoke, `mixed` when the company answered without stating anything it holds about them (the protocol it
gave for a dispute is the receipt of the conversation, not data about the person), and the company's
(`company`) when the customer never spoke or the company stated what it holds: an amount (a balance, a
limit, a debt), a postal code or a code, or a fact whose first evidence is the company's turn.

Derived from the evidence only, never from a model's opinion, and conservative on purpose: a value a
customer turn states first is the customer's; one the company states first makes the conversation the
company's, even when the customer repeats it later.
"""

from collections.abc import Iterable

from niadra.domain.memory.values import ValueType
from niadra.domain.vocabulary import Speaker, SpokenBy

COMPANY_HELD_VALUES = frozenset({ValueType.AMOUNT, ValueType.POSTAL_CODE, ValueType.CODE})
"""Values that, stated by the company, are what it holds about the customer: a balance or an amount due,
an address, a code. Protocols, tickets, orders, records, receipts and dates it gives are receipts of the
conversation."""


def conversation_spoken_by(
    turns: Iterable[Speaker],
    *,
    company_facts: int = 0,
    company_values: Iterable[ValueType] = (),
) -> SpokenBy:
    """Whose words a conversation holds, from the speakers of its message turns, how many facts it
    produced whose first evidence is a company turn, and the types of the values a company turn stated
    first."""
    speakers = set(turns)
    if Speaker.CUSTOMER not in speakers:
        return SpokenBy.COMPANY
    if company_facts or any(t in COMPANY_HELD_VALUES for t in company_values):
        return SpokenBy.COMPANY
    return SpokenBy.MIXED if speakers - {Speaker.CUSTOMER} else SpokenBy.CUSTOMER


def fact_spoken_by(speaker: Speaker) -> SpokenBy:
    """A fact is the customer's when its first evidence is a customer turn."""
    return SpokenBy.CUSTOMER if speaker is Speaker.CUSTOMER else SpokenBy.COMPANY
