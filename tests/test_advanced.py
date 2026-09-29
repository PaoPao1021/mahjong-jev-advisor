from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from mahjong_jev_advisor.llm import (
    LLMAuthError, LLMError, _format_state_prompt, query_llm_analysis,
)
from mahjong_jev_advisor.rules import (
    UncertainState, _call_yaku_available, _chi_options, candidates, danger,
    dora_from_indicator, shanten_text, ukeire_details,
)
from mahjong_jev_advisor.settings import Settings
from mahjong_jev_advisor.state import Candidate, GameState
from mahjong_jev_advisor.tiles import parse_tiles
from mahjong_jev_advisor.vision import TemplateStore, VisionReader
from mahjong_jev_advisor.yaku import (
    analyze_yaku_potential, estimate_tenpai_value, evaluate_call_safety,
)


def make_state(**overrides) -> GameState:
    data = {
        "hand": "123m456p789s123z55m",
        "rivers": [
            ["4m"],  # Player 0 river has 4m -> 1m and 7m are suji for player 0
            ["4m", "5p"],  # Player 1 river
            [],
            [],
        ],
        "melds": [[], [], [], []],
        "dora_indicators": ["7p"],
        "buttons": [],
        "scores": [25000] * 4,
        "riichi": [False, True, False, False],  # Player 1 in riichi
        "seat": 0,
    }
    data.update(overrides)
    return GameState.from_dict(data)


def test_shanten_text_formatting():
    assert shanten_text(0) == "听牌"
    assert shanten_text(1) == "一向听"
    assert shanten_text(2) == "二向听"
    assert shanten_text(3) == "三向听"
    assert shanten_text(4) == "4向听"


def test_ukeire_details_returns_improving_tiles():
    state = make_state(hand="123m456p789s11z45m")
    # Tenpai waiting for 3m or 6m
    hand_tuple = parse_tiles("123m456p789s11z45m")
    total, imp_tiles = ukeire_details(hand_tuple, state)
    assert total > 0
    assert "3m" in imp_tiles
    assert "6m" in imp_tiles


def test_enhanced_danger_suji_and_genbutsu():
    # Player 1 is in riichi, river has 4m and 5p
    state = make_state(
        riichi=[False, True, False, False],
        rivers=[[], ["4m", "5p", "1z"], [], []],
        seat=0,
    )
    # 1z is genbutsu in Player 1's river -> danger is 0
    assert danger("1z", state) == 0

    # 4m is in Player 1's river -> 1m is suji 1/9 (danger 1), 7m is suji 3/7 (danger 2)
    assert danger("1m", state) == 1
    assert danger("7m", state) == 2

    # 5p is in river -> 2p is suji (danger 2)
    assert danger("2p", state) == 2

    # 2s has no suji discarded -> non-suji 2/8 (danger 4)
    assert danger("2s", state) == 4

    # 5s has no suji discarded -> non-suji dangerous central tile (danger 5)
    assert danger("5s", state) == 5

    # 8p is dora (indicator is 7p) and suji -> base 2 + 2 dora = 4
    assert danger("8p", state) == 4


def test_call_yaku_available_with_non_zero_seat_and_hand_pair():
    # Player seat is 2 (West), has Chun pair in hand (13 tiles)
    state = make_state(
        seat=2,
        hand="123m456p78s77z111p",
        melds=[[], [], [], []],
    )
    cand_chi = Candidate("chi", "9s", "吃 7s 8s 9s", "", meld_tiles=("7s", "8s", "9s"))
    # Holding 7z 7z (Chun pair) provides potential yaku!
    assert _call_yaku_available(state, cand_chi) is True


def test_call_yaku_rejected_when_tanyao_broken_by_existing_melds():
    # Player already called 1m-2m-3m (contains terminal 1m), open_melds=1, hand has 10 tiles
    state = make_state(
        seat=0,
        hand="234m567p23s88p",
        open_melds=1,
        melds=[["1m", "2m", "3m"], [], [], []],
    )
    cand_chi = Candidate("chi", "4s", "吃 2s 3s 4s", "", meld_tiles=("2s", "3s", "4s"))
    assert _call_yaku_available(state, cand_chi) is False


def test_chi_options_rejects_impossible_combination():
    # Last discard is 3m, but hand doesn't have 1m2m, 2m4m, or 4m5m
    state = make_state(
        hand="123p456p789s11z55z",
        buttons=["chi", "pass"],
        last_discard="3m",
    )
    with pytest.raises(UncertainState):
        _chi_options(state)


def test_template_store_batch_matching(tmp_path):
    store = TemplateStore(tmp_path)
    img1 = np.full((64, 48, 3), 180, dtype=np.uint8)
    img1[20:44, 15:33] = 40
    img2 = np.full((64, 48, 3), 100, dtype=np.uint8)
    img2[10:30, 10:40] = 230

    store.add("1m", img1)
    store.add("9p", img2)

    results = store.match_batch([img1, img2])
    assert len(results) == 2
    assert results[0].label == "1m" and results[0].confidence > 0.95
    assert results[1].label == "9p" and results[1].confidence > 0.95


def test_vision_noise_without_buttons_does_not_fail():
    reader = VisionReader({
        "hand": (0, 0, 0.5, 0.1),
        "draw": (0.5, 0, 0.05, 0.1),
        "buttons": (0.6, 0, 0.2, 0.1),
    })
    # Mock OCR returning noise with low confidence
    reader._ocr_text = lambda img: ("·", 0.45)
    reader._tiles = lambda frame, name: (["1m"] * (13 if name == "hand" else 1), 0.98, 0)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    obs = reader.analyze(frame)
    # Background noise like "·" without any button keywords should NOT flag OCR problems
    assert "操作按钮 OCR 置信度不足" not in obs.problems


def test_llm_format_prompt():
    state = make_state(hand="123m456p789s123z55m")
    candidate = Candidate(
        "discard", "5m", "打 5m", "听牌 · 待牌 5m 约 3 张",
        shanten=0, ukeire=3, danger=1, dora_loss=0,
        improving_tiles=("5m",),
    )
    prompt = _format_state_prompt(state, candidate)
    assert "打 5m" in prompt
    assert "听牌" in prompt
    assert "东1局" in prompt


def test_llm_query_analysis_auth_and_success():
    state = make_state()
    candidate = Candidate("discard", "1m", "打 1m", "切牌", shanten=1, ukeire=8)
    settings = Settings(llm_api_key="")

    # Missing API key raises LLMAuthError
    with pytest.raises(LLMAuthError, match="尚未配置"):
        query_llm_analysis(state, candidate, settings)

    # Valid response mock
    settings.llm_api_key = "test-key"
    mock_response = {
        "choices": [
            {"message": {"content": "【切牌分析】推荐切出 1m，保持一向听最大进张数。"}}
        ]
    }
    payload = json.dumps(mock_response).encode("utf-8")
    with patch("urllib.request.urlopen", return_value=BytesIO(payload)):
        result = query_llm_analysis(state, candidate, settings)
        assert "推荐切出 1m" in result


def test_yaku_tenpai_value_calculation_menzen_and_riichi():
    # Menzen hand in tenpai: 123m 456p 789s 11z 23m -> waits on 1m, 4m
    state = make_state(
        hand="123m456p789s11z23m",
        seat_wind="E",
        round_wind="E",
    )
    hand_tuple = parse_tiles("123m456p789s11z23m")
    min_h, max_h, score, yaku = estimate_tenpai_value(state, hand_tuple, ("1m", "4m"), is_riichi=False)
    assert min_h >= 1  # Menzen tsumo guaranteed if tsumo
    assert max_h >= 1
    assert score > 0
    assert "门清自摸" in yaku

    # With Riichi declared
    r_min, r_max, r_score, r_yaku = estimate_tenpai_value(state, hand_tuple, ("1m", "4m"), is_riichi=True)
    assert r_min == min_h + 1
    assert "立直" in r_yaku


def test_yaku_tenpai_value_with_dora_and_aka():
    # Hand with red 5 (0p) and dora indicator 9m (so 1m is dora)
    state = make_state(
        hand="123m40p6p789s11z23m",
        dora_indicators=["9m"],
        seat_wind="E",
        round_wind="E",
    )
    hand_tuple = parse_tiles("123m40p6p789s11z23m")
    min_h, max_h, score, yaku = estimate_tenpai_value(state, hand_tuple, ("1m", "4m"), is_riichi=False)
    assert "赤宝牌" in yaku
    assert "宝牌" in yaku
    assert max_h >= 3


def test_yaku_tenpai_keiten_detection():
    # Open hand with 1m2m3m meld, closed: 234p 567s 22z 45m (waiting on 3m, 6m).
    # Has terminal 1m in meld (no tanyao), 2z is guest wind (no yakuhai), open hand (no menzen/pinfu).
    # All waits have 0 yaku -> 形式听牌 (无役)!
    state = make_state(
        hand="234p567s22z45m",
        melds=[["1m", "2m", "3m"], [], [], []],
        open_melds=1,
        seat=0,
        seat_wind="E",
        round_wind="E",
    )
    hand_tuple = parse_tiles("234p567s22z45m")
    min_h, max_h, score, yaku = estimate_tenpai_value(state, hand_tuple, ("3m", "6m"))
    assert min_h == 0
    assert max_h == 0
    assert score == 0
    assert "形式听牌(无役)" in yaku


def test_yaku_potential_analysis():
    # 1-shanten hand with clear Tanyao and Pinfu hints (13 tiles)
    state = make_state(hand="234m345p23s45s66p7s", open_melds=0)
    hand_tuple = parse_tiles("234m345p23s45s66p7s")
    hints = analyze_yaku_potential(state, hand_tuple)
    assert "门清" in hints
    assert "确定断幺九" in hints or "断幺九走向" in hints
    assert "平和倾向" in hints

    # Chiitoitsu potential (13 tiles, 5 pairs)
    state_chii = make_state(hand="11m33m55p77p99s22z4z", open_melds=0)
    hints_chii = analyze_yaku_potential(state_chii, parse_tiles("11m33m55p77p99s22z4z"))
    assert any("七对子" in h for h in hints_chii)


def test_evaluate_call_safety_various_cases():
    # 13-tile hands when waiting for other player's discard
    state = make_state(
        hand="234m567p23s88p1s1s2s",
        seat=0,
        seat_wind="E",
        round_wind="E",
    )
    # 1. Calling Chun (7z) Pon -> guaranteed yaku
    cand_chun = Candidate("pon", "7z", "碰 7z", "", meld_tiles=("7z", "7z", "7z"))
    safe, msg, yaku = evaluate_call_safety(state, cand_chun)
    assert safe is True
    assert "役牌:中" in msg

    # 2. Calling 4s for Chi 2s 3s 4s -> Hand is all similes, valid Tanyao
    cand_tanyao = Candidate("chi", "4s", "吃 2s 3s 4s", "", meld_tiles=("2s", "3s", "4s"))
    safe_t, msg_t, yaku_t = evaluate_call_safety(state, cand_tanyao)
    assert safe_t is True
    assert "断幺九" in msg_t

    # 3. Hand with terminal and guest winds calling without yaku (13 tiles)
    state_no_yaku = make_state(
        hand="123m456p1s9s2z3z4z88s",
        seat=0,
        seat_wind="E",
        round_wind="E",
    )
    cand_bad = Candidate("chi", "3m", "吃 1m 2m 3m", "", meld_tiles=("1m", "2m", "3m"))
    safe_bad, msg_bad, yaku_bad = evaluate_call_safety(state_no_yaku, cand_bad)
    assert safe_bad is False
    assert "无役死手" in msg_bad


def test_candidates_populates_expected_han_and_potential_yaku():
    # 14-tile hand in tenpai after discard, no riichi threats
    state = make_state(
        hand="123m456p789s11z23m9p",
        buttons=["riichi"],
        open_melds=0,
        seat_wind="E",
        round_wind="E",
        riichi=[False, False, False, False],
    )
    cands = candidates(state)
    # The best discard (discard 9p) leads to tenpai
    discard_9p = next((c for c in cands if c.action == "discard" and c.tile == "9p"), None)
    assert discard_9p is not None
    assert discard_9p.shanten == 0
    assert discard_9p.expected_han is not None and discard_9p.expected_han >= 1
    assert "门清自摸" in discard_9p.potential_yaku or "平和" in discard_9p.potential_yaku
    assert "预计" in discard_9p.rationale
