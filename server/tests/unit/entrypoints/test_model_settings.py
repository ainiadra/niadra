"""The deployment's model settings: the extraction model's endpoint and route, and the decision model's."""

import pytest

from niadra.entrypoints.cell import chat_settings, decider_settings
from niadra.entrypoints.settings import Settings

MODEL_VARIABLES = (
    "NIADRA_LLM_API_KEY",
    "NIADRA_LLM_BASE_URL",
    "NIADRA_LLM_MODEL",
    "NIADRA_LLM_PROVIDER_ORDER",
    "NIADRA_LLM_ZERO_RETENTION",
    "NIADRA_LLM_INCLUDE_USAGE",
    "NIADRA_DECISIONS_BASE_URL",
    "NIADRA_DECISIONS_MODEL",
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name, value in {
        "NIADRA_RABBITMQ_URL": "amqp://localhost",
        "NIADRA_KEY_PEPPER": "pepper",
        "NIADRA_SUBJECT_TOKEN_SECRET": "secret",
        "NIADRA_CELL_TOKEN": "token",
        "NIADRA_DB_HOST": "localhost",
        "NIADRA_DB_PASSWORD": "x",
        "NIADRA_S3_BUCKET": "bucket",
    }.items():
        monkeypatch.setenv(name, value)
    for name in MODEL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_without_an_endpoint_and_a_model_neither_model_is_configured(env: pytest.MonkeyPatch) -> None:
    settings = Settings.from_env()
    assert settings.llm_api_key is None
    assert chat_settings(settings) is None
    assert decider_settings(settings) is None
    env.setenv("NIADRA_LLM_BASE_URL", "https://llm.example.com/v1")
    env.setenv("NIADRA_DECISIONS_BASE_URL", "https://decisions.example.com/api")
    assert chat_settings(Settings.from_env()) is None
    assert decider_settings(Settings.from_env()) is None


def test_the_endpoints_and_model_ids_come_from_the_environment(env: pytest.MonkeyPatch) -> None:
    env.setenv("NIADRA_LLM_API_KEY", "sk-test")
    env.setenv("NIADRA_LLM_BASE_URL", "https://llm.example.com/v1")
    env.setenv("NIADRA_LLM_MODEL", "vendor/extraction-model")
    env.setenv("NIADRA_DECISIONS_BASE_URL", "https://decisions.example.com/api")
    env.setenv("NIADRA_DECISIONS_MODEL", "vendor/decision-model")
    settings = Settings.from_env()
    chat = chat_settings(settings)
    decider = decider_settings(settings)
    assert settings.llm_api_key == "sk-test"
    assert chat is not None
    assert (chat.base_url, chat.model) == ("https://llm.example.com/v1", "vendor/extraction-model")
    assert (chat.provider_order, chat.zero_retention, chat.include_usage) == ((), False, False)
    assert decider is not None
    assert (decider.base_url, decider.model) == ("https://decisions.example.com/api", "vendor/decision-model")
    assert not decider.zero_retention


def test_the_route_is_the_router_s_unless_the_deployment_pins_providers(env: pytest.MonkeyPatch) -> None:
    env.setenv("NIADRA_LLM_BASE_URL", "https://llm.example.com/v1")
    env.setenv("NIADRA_LLM_MODEL", "m")
    env.setenv("NIADRA_DECISIONS_BASE_URL", "https://decisions.example.com/api")
    env.setenv("NIADRA_DECISIONS_MODEL", "d")
    chat = chat_settings(Settings.from_env())
    assert chat is not None
    assert chat.provider_order == ()
    env.setenv("NIADRA_LLM_PROVIDER_ORDER", "openai, azure")
    env.setenv("NIADRA_LLM_ZERO_RETENTION", "true")
    env.setenv("NIADRA_LLM_INCLUDE_USAGE", "1")
    settings = Settings.from_env()
    chat = chat_settings(settings)
    decider = decider_settings(settings)
    assert chat is not None
    assert (chat.provider_order, chat.zero_retention, chat.include_usage) == (("openai", "azure"), True, True)
    assert decider is not None
    assert decider.zero_retention
