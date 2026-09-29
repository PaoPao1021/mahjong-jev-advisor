import json
import numpy as np
import pytest

from mahjong_jev_advisor.settings import Settings
from mahjong_jev_advisor import settings as settings_module
from mahjong_jev_advisor.jev import JevError, ModelConnection, test_connection as probe
from mahjong_jev_advisor.rules import candidates, UncertainState
from mahjong_jev_advisor.state import GameState
from mahjong_jev_advisor.tiles import parse_tiles
from mahjong_jev_advisor.vision import VisionReader
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


@pytest.mark.parametrize("invalid", [[], {"model_timeout": "oops"}, {"regions": []},
                                      {"local_fallback": "false"}, {"game_box": [0, 0, -1, 1]}])
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


def test_low_draw_confidence_does_not_commit_history():
    reader = VisionReader({name: (0, 0, 1, 1) for name in ("hand", "draw", "buttons")})
    reader._ocr_text = lambda image: ("", 1.0)
    reader._ocr_sections = lambda image, names: {}
    confidence = [0.98]
    river = [["7z"]]
    def tiles(frame, name):
        if name == "hand":
            return list(parse_tiles("123m456p789s123z5m")), 0.99, 0
        if name == "draw":
            return ["5m"], confidence[0], 0
        if name == "river_1":
            return river[0], 0.99, 0
        return [], 1.0, 0
    reader._tiles = tiles
    frame = np.zeros((50, 50, 3), dtype=np.uint8)
    assert reader.analyze(frame).confidence > 0.9
    previous = reader._previous_rivers
    confidence[0] = 0.89
    river[0] = []
    assert reader.analyze(frame).confidence < 0.9
    assert reader._previous_rivers == previous
    confidence[0] = 0.99
    river[0] = ["7z", "6z"]
    assert reader.analyze(frame).state.last_discard == "6z"
