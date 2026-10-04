from dataclasses import replace

import pytest

from mahjong_jev_advisor.hook import HookStateBuilder
from mahjong_jev_advisor.jev import _request_body
from mahjong_jev_advisor.llm import _format_state_prompt
from mahjong_jev_advisor.rules import candidates, dora_from_indicator, dora_value, ukeire_details
from mahjong_jev_advisor.state import GameState
from mahjong_jev_advisor.yaku import estimate_tenpai_value

HAND = '19m123456p123s114z'


def sanma(**data):
    return GameState.from_dict({'player_count': 3, 'hand': HAND, **data})


def test_sanma_validation_and_roundtrip():
    state = sanma()
    assert len(state.scores) == 3
    assert GameState.from_dict(state.to_dict()) == state
    assert sanma(nuki=[0, 1, 0]).identity() != state.identity()
    for data in ({'hand': '123m123456p123s11z'}, {'buttons': ['chi']},
                 {'seat': 3}, {'seat_wind': 'N'}, {'round_number': 4},
                 {'nuki': [0, 4, 0]}):
        with pytest.raises(ValueError):
            sanma(**data)


def test_sanma_ukeire_dora_and_nuki_candidates():
    state = sanma(buttons=['nuki'], dora_indicators=['1m'])
    opts = candidates(state)
    assert any(c.action == 'nuki' for c in opts)
    assert not any(c.action == 'chi' for c in opts)
    assert all(t not in {f'{n}m' for n in range(2, 9)} for c in opts for t in c.improving_tiles)
    assert dora_from_indicator('1m', 3) == '9m'
    assert dora_from_indicator('9m', 3) == '1m'
    assert dora_from_indicator('1m') == '2m'
    assert dora_value('9m', state) == 1
    payload = _request_body(state, opts)['state']
    assert payload['player_count'] == 3
    assert 'three-player' in payload['game']
    assert '3人' in _format_state_prompt(state, opts[0])


def test_extracted_north_reduces_available_waits():
    state = sanma(hand='123456789p111s4z')
    count, waits = ukeire_details(state.hand, state)
    with_nuki = sanma(hand='123456789p111s4z', nuki=[0, 2, 0])
    reduced, waits_after = ukeire_details(with_nuki.hand, with_nuki)
    assert '4z' in waits
    assert reduced == count - 2
    assert waits_after == waits


def test_sanma_hand_value_includes_north_but_not_four_player_payment():
    state = sanma(hand='123456789p111s4z')
    base = estimate_tenpai_value(state, state.hand, ('4z',))
    bonus = estimate_tenpai_value(replace(state, nuki=(1, 0, 0)), state.hand, ('4z',))
    assert base[0] > 0
    assert bonus[0] == base[0] + 1
    assert bonus[2] == 0
    assert '拔北宝牌' in bonus[3]


def test_hook_new_round_nuki_replacement_and_winds():
    builder = HookStateBuilder()
    builder.own_seat = 0
    state = builder.apply('ActionNewRound', {'scores': [35000] * 3, 'ju': 1,
        'tiles': list(sanma().hand), 'operation': {'seat': 0, 'operationList': [{'type': 11}]}})
    assert state.player_count == 3 and state.seat_wind == 'W'
    assert state.buttons == frozenset({'nuki'})
    state = builder.apply('ActionBaBei', {'seat': 0})
    assert state.open_melds == 0 and state.nuki == (1, 0, 0)
    state = builder.apply('ActionDealTile', {'seat': 0, 'tile': '9s'})
    assert len(state.hand) == 14 and '4z' not in state.hand
    assert candidates(state)


def test_hook_restore_extracted_north_is_not_meld():
    builder = HookStateBuilder()
    state = builder.restore({'gameRestore': {'snapshot': {
        'indexPlayer': 0, 'hands': list(sanma(hand='123456789p111s4z').hand),
        'players': [{'score': 35000, 'mings': [{'tile': ['4z']}]},
                    {'score': 35000}, {'score': 35000}],
    }}})
    assert state.player_count == 3 and state.nuki == (1, 0, 0)
    assert state.open_melds == 0 and not state.melds[0]
