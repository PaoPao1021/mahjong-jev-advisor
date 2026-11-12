import pytest

from mahjong_jev_advisor.rules import candidates, discard_comparison, NoDecision, UncertainState
from mahjong_jev_advisor.state import GameState


@pytest.mark.parametrize('hand,count', [('123m405p789s123z55m', 4), ('19m123456p123s114z', 3)])
def test_comparison_covers_all_distinct_tiles_and_preserves_online_shortlist(hand, count):
    state = GameState.from_dict({'hand': hand, 'player_count': count})
    rows = discard_comparison(state)
    assert {row.tile for row in rows} == set(state.hand)
    assert len(rows) == len(set(state.hand))
    assert candidates(state) == rows[:3]
    assert all(row.action == 'discard' for row in rows)


def test_red_five_is_a_separate_choice():
    state = GameState.from_dict({'hand': '123m405p789s123z55m'})
    rows = {row.tile: row for row in discard_comparison(state)}
    assert rows['0p'].dora_loss == rows['5p'].dora_loss + 1
    assert rows['0p'].shanten == rows['5p'].shanten


def test_comparison_requires_drawn_hand():
    state = GameState.from_dict({'hand': '123m456p789s123z5m'})
    with pytest.raises(NoDecision):
        discard_comparison(state)


def test_comparison_rejects_uncertain_state():
    state = GameState.from_dict({'hand': '123m456p789s123z55m', 'observation_confidence': 0.5})
    with pytest.raises(UncertainState):
        discard_comparison(state)


def test_comparison_and_simulation_reuse_analysis_but_refresh_changed_state(monkeypatch):
    from dataclasses import replace
    from mahjong_jev_advisor import rules

    rules.discard_candidate.cache_clear()
    rules.discard_comparison.cache_clear()
    original = rules.ukeire_details
    calls = []

    def counted_ukeire(hand, state):
        calls.append(state)
        return original(hand, state)

    monkeypatch.setattr(rules, "ukeire_details", counted_ukeire)
    state = GameState.from_dict({
        "hand": "123m405p789s123z55m", "riichi": [False] * 4,
    })
    try:
        rows = rules.discard_comparison(state)
        first_calls = len(calls)
        assert first_calls == len(set(state.hand))
        assert rules.discard_comparison(replace(state)) == rows
        for row in rows:
            assert rules.discard_candidate(state, row.tile) == row
        assert len(calls) == first_calls

        threatened = replace(state, riichi=(False, True, False, False))
        updated = rules.discard_candidate(threatened, "4p")
        assert len(calls) == first_calls + 1
        assert updated.danger > rules.discard_candidate(state, "4p").danger

        # Confidence is deliberately absent from GameState.identity(), but
        # must still invalidate a previously trusted cached calculation.
        uncertain = replace(state, observation_confidence=0.5)
        assert uncertain.identity() == state.identity()
        with pytest.raises(UncertainState):
            rules.discard_comparison(uncertain)
        with pytest.raises(UncertainState):
            rules.discard_candidate(uncertain, "4p")
    finally:
        rules.discard_candidate.cache_clear()
        rules.discard_comparison.cache_clear()


def test_single_discard_requires_drawn_hand():
    from mahjong_jev_advisor.rules import discard_candidate

    state = GameState.from_dict({"hand": "123m456p789s123z5m"})
    with pytest.raises(NoDecision):
        discard_candidate(state, "1m")
