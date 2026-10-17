"""What an undeclared field holds back from agents until it is declared (`domain.objects.undeclared.held`)."""

import pytest

from niadra.domain.objects.undeclared import held


@pytest.mark.parametrize(
    ("name", "value"),
    [
        # The company's own professional on the customer's matter: who handles it is the company's word,
        # and the customer is told it (zero-touch acceptance, Q7, 07/10/2026).
        ("responsible_lawyer_name", "Rafael Menezes"),
        ("assigned_agent_name", "Juliana Prado"),
        ("account_manager_name", "Carla Souza"),
        ("advogado_responsavel_nome", "Diego Assis"),
        ("product_name", "Dipirona 500 mg"),
        ("title", "Especificação de provas"),
    ],
)
def test_a_value_that_is_the_companys_is_served(name: str, value: str) -> None:
    assert held(name, value) is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("cliente_nome", "Marcos Lima"),
        ("customer_full_name", "Marcos Lima"),
        ("name", "Maria da Silva"),
        ("plan_name", "Plano Ouro"),
        # An employee's record, a patient's doctor, a contact: the person is the subject, or the name says
        # something about the customer.
        ("employee_name", "Rafael Menezes"),
        ("patient_doctor_name", "Ana Prado"),
        ("contact_lawyer_name", "Rafael Menezes"),
        # A staff role never lets a contact value through: its shape holds it, whatever the name.
        ("responsible_lawyer_email", "rafael@escritorio.com"),
        ("responsible_lawyer_name", "rafael@escritorio.com"),
    ],
)
def test_what_may_be_a_customers_personal_data_is_held(name: str, value: str) -> None:
    assert held(name, value) == "personal"
