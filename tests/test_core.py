from __future__ import annotations

from io import BytesIO
import json
import urllib.error
from unittest.mock import patch

import pytest

from mahjong_jev_advisor.jev import JevAuthError, JevError, JevUnavailableError, ModelConnection, advise, ask_jev
from mahjong_jev_advisor.rules import UncertainState, candidates, dora_from_indicator, shanten
from mahjong_jev_advisor.settings import Settings
from mahjong_jev_advisor.state import GameState
from mahjong_jev_advisor.tiles import normal, parse_tiles


def state(**overrides):
    data = {
        "hand": "123m456p789s123z55m",
        "rivers": [[], [], [], []],
        "melds": [[], [], [], []],
        "dora_indicators": ["7p"],
        "buttons": [],
        "scores": [25000] * 4,
        "riichi": [False] * 4,
    }
    data.update(overrides)
    return GameState.from_dict(data)


def test_tile_notation_and_red_five():
    assert parse_tiles("123m405p77z") == ("1m", "2m", "3m", "4p", "0p", "5p", "7z", "7z")
    assert normal("0p") == "5p"
    assert dora_from_indicator("9m") == "1m"
    assert dora_from_indicator("4z") == "1z"
    assert dora_from_indicator("7z") == "5z"


def test_public_table_rejects_more_than_four_physical_copies():
    with pytest.raises(ValueError, match="More than four copies visible"):
        state(
            hand="1111m234p567s123z",
            rivers=[["1m"], [], [], []],
        )


def test_known_tenpai_shanten():
    assert shanten(parse_tiles("123m123p123s77z45m")) == 0


def test_win_button_is_forced():
    result = candidates(state(buttons=["ron", "pass"]))
    assert len(result) == 1 and result[0].action == "ron"


def test_discard_options_are_from_hand_and_riichi_only_when_tenpai():
    current = state(buttons=["riichi"])
    options = candidates(current)
    assert all(x.tile in current.hand or x.tile is None for x in options)
    assert all(x.action in {"discard", "riichi"} for x in options)
    assert all(x.shanten == 0 for x in options if x.action == "riichi")


def test_call_options_only_when_button_is_visible():
    current = state(hand="123m456p789s12z55m", buttons=["pon", "pass"], last_discard="5m")
    assert {x.action for x in candidates(current)} == {"pon", "pass"}


def test_low_confidence_abstains():
    with pytest.raises(UncertainState):
        candidates(state(observation_confidence=0.7))


def test_api_failure_uses_local_action():
    current = state()
    options = candidates(current)
    with patch("mahjong_jev_advisor.jev.ask_jev", side_effect=Exception("network")):
        # Unexpected programming errors should propagate rather than be hidden.
        with pytest.raises(Exception, match="network"):
            advise(current, options, "dummy")
    with pytest.raises(JevError, match="API Key"):
        advise(current, options, "")
    with patch("mahjong_jev_advisor.jev.ask_jev", side_effect=JevUnavailableError("timeout")):
        fallback = advise(current, options, "dummy", connection=ModelConnection(local_fallback=True))
    assert fallback.source == "rules" and fallback.selected in options
    with patch("mahjong_jev_advisor.jev.ask_jev", side_effect=JevError("bad request")):
        with pytest.raises(JevError, match="bad request"):
            advise(current, options, "dummy")


def test_jev_choice_is_mapped_back_to_supplied_candidates():
    current = state()
    options = candidates(current)[:2]
    payload = b'{"answers":{"action":{"type":"choice","choice":"a1","probabilities":{"a0":0.2,"a1":0.8}}},"providerMetadata":{"typesafe":{"confidence":{"action":0.8}}}}'
    with patch("mahjong_jev_advisor.jev.urllib.request.OpenerDirector.open", return_value=BytesIO(payload)) as request:
        result = ask_jev(current, options, "test-key")
    assert result.selected == options[1]
    assert result.model == "typesafe-ai/jev-latest" and result.confidence == 0.8
    assert request.call_args.kwargs["timeout"] == 15.0
    wire = request.call_args.args[0]
    assert wire.full_url == "https://ai-gateway.vercel.sh/v4/ai/evaluation-model"
    assert wire.get_header("Ai-model-id") == "typesafe-ai/jev-latest"
    assert wire.get_header("Ai-gateway-protocol-version") == "0.0.1"
    assert wire.get_header("Authorization") == "Bearer test-key"
    body = json.loads(wire.data)
    assert set(body) == {"state", "questions"}
    assert body["questions"]["action"]["type"] == "choice"


def test_gateway_auth_failure_does_not_fall_back():
    current = state()
    options = candidates(current)
    failure = urllib.error.HTTPError("gateway", 401, "Unauthorized", {}, None)
    with patch("mahjong_jev_advisor.jev.urllib.request.OpenerDirector.open", side_effect=failure):
        with pytest.raises(JevAuthError, match="401"):
            advise(current, options, "bad-key")


def test_old_typesafe_key_is_not_reused_for_gateway(tmp_path, monkeypatch):
    monkeypatch.setattr("mahjong_jev_advisor.settings.config_dir", lambda: tmp_path)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    (tmp_path / "settings.json").write_text('{"api_key":"old-typesafe-key"}', encoding="utf-8")
    settings = Settings.load()
    assert settings.api_key == ""
    settings.api_key = "vercel-key"
    settings.save()
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["gateway_api_key"] == "vercel-key"
    assert "api_key" not in saved


def test_kan_and_abortive_draw_are_represented_when_buttons_show():
    options = candidates(state(buttons=["kan", "kyuushu"]))
    assert {"kan", "kyuushu"} <= {item.action for item in options}
