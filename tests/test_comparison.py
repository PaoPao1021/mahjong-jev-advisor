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
