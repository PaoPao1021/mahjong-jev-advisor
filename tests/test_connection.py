import pytest
from mahjong_jev_advisor.jev import ModelConnection, JevError, JevAuthError, test_connection as probe
from mahjong_jev_advisor.settings import Settings


@pytest.mark.parametrize("protocol", ["vercel", "typesafe", "openrouter", "openai"])
def test_actual_http_request_and_parsing(http_server, protocol):
    if protocol == "openai":
        http_server.reply = {"model": "test-model", "choices": [{"message": {"content": '{"choice":"a0"}'}}]}
    config = ModelConnection(http_server.endpoint, "test-model", protocol)
    advice = probe("test-credential", config)
    path, headers, body = http_server.received[0]
    headers = {key.lower(): value for key, value in headers.items()}
    assert path == "/inference"
    assert headers["authorization"] == "Bearer test-credential"
    assert advice.selected.label == "连接成功"
    assert advice.source == "jev"
    if protocol == "vercel":
        assert headers["ai-model-id"] == "test-model"
        assert headers["ai-gateway-protocol-version"] == "0.0.1"
        assert "questions" in body and "model" not in body
    elif protocol in ("typesafe", "openrouter"):
        assert body["model"] == "test-model" and "questions" in body
    else:
        assert body["model"] == "test-model" and body["stream"] is False
        assert body["messages"][1]["role"] == "user"
        assert advice.probabilities == {} and advice.confidence is None


def test_real_http_billing_error_is_not_key_error(http_server):
    http_server.status = 403
    http_server.reply = {"error": {"type": "customer_verification_required", "message": "requires a valid credit card"}}
    with pytest.raises(JevAuthError, match="账单验证"):
        probe("test-credential", ModelConnection(http_server.endpoint))


def test_http_200_is_not_success_without_valid_choice(http_server):
    http_server.reply["answers"]["action"]["choice"] = "invented-action"
    with pytest.raises(JevError, match="候选列表以外"):
        probe("test-credential", ModelConnection(http_server.endpoint))


def test_custom_configuration_survives_restart():
    settings = Settings(api_key="test", model_endpoint="https://example.com/custom", model_name="custom",
                        model_protocol="openai", model_timeout=8.5)
    settings.save()
    restored = Settings.load()
    assert restored.api_key == "test"
    assert restored.connection() == settings.connection()
