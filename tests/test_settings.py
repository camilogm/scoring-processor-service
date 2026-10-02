import pytest
from pydantic import ValidationError

from app.settings import Settings

GATEWAY = "https://ai-gateway.vercel.sh/v1"


@pytest.fixture(autouse=True)
def _no_inherited_env(monkeypatch):
    """A key or price exported in the shell would hide what these tests check."""
    for name in ("AI_GATEWAY_API_KEY", "LLM_API_KEY", "LLM_BASE_URL", "LLM_PRICE_INPUT_PER_MTOK",
                 "LLM_PRICE_OUTPUT_PER_MTOK"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    "url", ["http://localhost:11434/v1", "http://127.0.0.1:11434/v1", "http://host.docker.internal:11434/v1"]
)
def test_local_endpoint_needs_no_key_or_prices(url):
    settings = Settings(llm_base_url=url, _env_file=None)

    assert settings.llm_price_input_per_mtok is None


def test_paid_endpoint_with_key_and_prices_starts():
    Settings(
        llm_base_url=GATEWAY, llm_api_key="vck_test", llm_price_input_per_mtok=0.3, llm_price_output_per_mtok=2.5,
        _env_file=None,
    )


def test_paid_endpoint_without_a_key_refuses_to_start():
    with pytest.raises(ValidationError, match="AI_GATEWAY_API_KEY"):
        Settings(
            llm_base_url=GATEWAY, llm_price_input_per_mtok=0.3, llm_price_output_per_mtok=2.5, _env_file=None
        )


def test_paid_endpoint_without_prices_refuses_to_start():
    # Otherwise a call whose cost the provider doesn't report is stored as $0.
    with pytest.raises(ValidationError) as exc:
        Settings(llm_base_url=GATEWAY, llm_api_key="vck_test", _env_file=None)

    message = str(exc.value)
    assert "LLM_PRICE_INPUT_PER_MTOK" in message
    assert "LLM_PRICE_OUTPUT_PER_MTOK" in message


def test_every_missing_setting_is_reported_at_once():
    with pytest.raises(ValidationError) as exc:
        Settings(llm_base_url="https://api.example.com/v1", _env_file=None)

    message = str(exc.value)
    for name in ("AI_GATEWAY_API_KEY", "LLM_PRICE_INPUT_PER_MTOK", "LLM_PRICE_OUTPUT_PER_MTOK"):
        assert name in message


def test_startup_errors_never_echo_secrets():
    # Pydantic prints the input it got; on a deployment that lands in the logs.
    with pytest.raises(ValidationError) as exc:
        Settings(
            llm_base_url=GATEWAY, llm_api_key="vck_secret_key", basic_auth_user="camilo",
            basic_auth_password="s3cret-password", _env_file=None,
        )

    assert "vck_secret_key" not in str(exc.value)
    assert "s3cret-password" not in str(exc.value)


def test_explicit_zero_prices_declare_a_free_endpoint():
    settings = Settings(
        llm_base_url="https://llm.internal.example/v1", llm_api_key="k", llm_price_input_per_mtok=0,
        llm_price_output_per_mtok=0, _env_file=None,
    )

    assert settings.llm_price_input_per_mtok == 0
