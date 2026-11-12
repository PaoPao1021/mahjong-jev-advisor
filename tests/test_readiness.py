import json
import pytest

from mahjong_jev_advisor.settings import Settings
from mahjong_jev_advisor import settings as settings_module
from mahjong_jev_advisor.jev import JevError, ModelConnection, test_connection as probe
from mahjong_jev_advisor.rules import candidates, UncertainState
from mahjong_jev_advisor.state import GameState
from mahjong_jev_advisor.settings import config_dir as captured_config_dir


def test_configuration_alias_also_uses_isolated_root(tmp_path):
    assert captured_config_dir() == tmp_path


def test_provider_credentials_are_isolated(monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "vercel-only")
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-only")
    settings = Settings(model_protocol="openrouter", model_endpoint="https://openrouter.ai/api/v1/systemone")
    settings.save()
    assert Settings.load().api_key == "router-only"
    settings.api_key = "explicit"
    settings.save()
    assert Settings.load().api_key == "explicit"
    settings.api_key = ""
    settings.model_endpoint = "https://custom.example/api/v1/systemone"
    settings.save()
    assert Settings.load().api_key == ""


@pytest.mark.parametrize("invalid", [[], {"model_timeout": "oops"}, {"steam_capture_mode": "invalid"},
                                      {"local_fallback": "false"}, {"steam_proxy_port": -1}])
def test_invalid_settings_preserved_and_backed_up(invalid):
    path = settings_module.config_dir() / "settings.json"
    original = json.dumps(invalid)
    path.write_text(original, encoding="utf-8")
    settings = Settings.load()
    assert settings.load_warning and path.read_text(encoding="utf-8") == original
    settings.save()
    assert (settings_module.config_dir() / "settings.invalid.json").read_text(encoding="utf-8") == original
    assert not Settings.load().load_warning


@pytest.mark.parametrize("fields", [{"round_wind": ""}, {"seat_wind": "ES"}, {"riichi": ["false", False, False, False]}])
def test_state_rejects_malformed_values(fields):
    with pytest.raises(ValueError):
        GameState.from_dict(fields)


@pytest.mark.parametrize("button", ["pon", "kan"])
def test_contradictory_call_is_never_suggested(button):
    state = GameState.from_dict({"hand": "123m456p789s123z5m", "buttons": [button, "pass"], "last_discard": "5m"})
    with pytest.raises(UncertainState):
        candidates(state)


@pytest.mark.parametrize("probabilities", [{"a0": 0.8}, {"a0": 0.8, "a1": 0.8}, {"a0": float("nan"), "a1": 0.2}])
def test_invalid_distributions_rejected(http_server, probabilities):
    http_server.reply["answers"]["action"]["probabilities"] = probabilities
    with pytest.raises(JevError):
        probe("fake", ModelConnection(http_server.endpoint))


def test_probe_requires_correct_ping_answer(http_server):
    http_server.reply["answers"]["action"]["choice"] = "a1"
    with pytest.raises(JevError, match="ping"):
        probe("fake", ModelConnection(http_server.endpoint))

