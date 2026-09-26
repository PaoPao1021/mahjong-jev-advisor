from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

import pytest

from mahjong_jev_advisor.jev import JevError, advise, ask_jev
from mahjong_jev_advisor.rules import UncertainState, candidates, dora_from_indicator, shanten
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
    current = state(hand="123m456p789s123z5m", buttons=["pon", "pass"], last_discard="5m")
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
    with patch("mahjong_jev_advisor.jev.ask_jev", side_effect=JevError("timeout")):
        fallback = advise(current, options, "dummy")
    assert fallback.source == "rules" and fallback.selected in options


def test_jev_choice_is_mapped_back_to_supplied_candidates():
    current = state()
    options = candidates(current)
    payload = b'{"model":"jev-1.13.0","answers":{"action":{"choice":"a1","confidence":0.8,"probabilities":{"a0":0.2,"a1":0.8}}}}'
    with patch("mahjong_jev_advisor.jev.urllib.request.urlopen", return_value=BytesIO(payload)) as request:
        result = ask_jev(current, options, "test-key")
    assert result.selected == options[1]
    assert result.model == "jev-1.13.0" and result.confidence == 0.8
    assert request.call_args.kwargs["timeout"] == 0.9


def test_kan_and_abortive_draw_are_represented_when_buttons_show():
    options = candidates(state(buttons=["kan", "kyuushu"]))
    assert {"kan", "kyuushu"} <= {item.action for item in options}
