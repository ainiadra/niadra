"""Whose words a conversation holds (the mirror rule's input): the customer's, both sides with nothing the
company holds about them, or the company's."""

from niadra.domain.memory.spoken import conversation_spoken_by, fact_spoken_by
from niadra.domain.memory.values import ValueType
from niadra.domain.vocabulary import Speaker, SpokenBy

CUSTOMER, AGENT, SYSTEM = Speaker.CUSTOMER, Speaker.AI_AGENT, Speaker.SYSTEM


def test_the_customer_alone_is_the_customers() -> None:
    assert conversation_spoken_by([CUSTOMER, CUSTOMER]) is SpokenBy.CUSTOMER


def test_an_answer_that_gives_a_receipt_is_mixed() -> None:
    # "O protocolo do seu atendimento é 98429": the receipt of the conversation, not data about the person.
    given = [ValueType.PROTOCOL, ValueType.TICKET, ValueType.ORDER, ValueType.RECORD, ValueType.DATE]
    assert conversation_spoken_by([CUSTOMER, AGENT], company_values=given) is SpokenBy.MIXED


def test_the_company_stating_what_it_holds_makes_it_the_companys() -> None:
    for held in (ValueType.AMOUNT, ValueType.POSTAL_CODE, ValueType.CODE):
        assert conversation_spoken_by([CUSTOMER, AGENT], company_values=[held]) is SpokenBy.COMPANY
    assert conversation_spoken_by([CUSTOMER, AGENT], company_facts=1) is SpokenBy.COMPANY


def test_without_the_customer_it_is_the_companys() -> None:
    assert conversation_spoken_by([AGENT, SYSTEM]) is SpokenBy.COMPANY
    assert conversation_spoken_by([]) is SpokenBy.COMPANY


def test_a_fact_is_the_customers_when_they_said_it_first() -> None:
    assert fact_spoken_by(CUSTOMER) is SpokenBy.CUSTOMER
    assert fact_spoken_by(Speaker.HUMAN_AGENT) is SpokenBy.COMPANY
    assert fact_spoken_by(SYSTEM) is SpokenBy.COMPANY
