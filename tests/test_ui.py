from __future__ import annotations

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtWidgets import QApplication

from mahjong_jev_advisor.app import MainWindow, _demo_reference_state
from mahjong_jev_advisor.ui_components import (
    BottomActionBar, FourPlayersRiverWidget, HandWidget, HUDTitleBar,
    DecisionHeroCard, RoundInfoBar,
)
from mahjong_jev_advisor.ui_tiles import CompactTileBadge, MahjongTileWidget


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_mahjong_tile_widget_rendering(qapp):
    widget = MahjongTileWidget(tile="1p", glowing=True)
    assert widget.tile == "1p"
    assert widget.glowing is True
    # Test setting other suits and red fives
    widget.set_tile("0m", glowing=False)
    assert widget.tile == "0m"
    widget.set_tile("1s")
    widget.set_tile("7z")
    widget.set_tile_size(30, 42, show_code=False)
    assert widget.tile_w == 30


def test_compact_tile_badge(qapp):
    badge = CompactTileBadge("1p", highlighted=True)
    assert badge.tile == "1p"
    assert badge.highlighted is True
    badge.set_tile("5z", highlighted=False)
    assert badge.tile == "5z"


def test_hand_widget_and_glow(qapp):
    hand = HandWidget()
    demo_tiles = ["4m", "4m", "1p", "1p", "4p", "4p", "4p", "2s", "2s", "2s", "7s", "8s", "9s", "1p"]
    hand.set_hand(demo_tiles, highlight_tile="1p")
    # Verify drawn slot is not hidden and glowing
    assert not hand.draw_slot.isHidden()
    assert hand.draw_slot.glowing is True
    assert hand.draw_slot.tile == "1p"


def test_hero_card_update(qapp):
    hero = DecisionHeroCard()
    probabilities = [
        ("1p", 83, True),
        ("2s", 7, False),
        ("4p", 5, False),
    ]
    hero.update_decision(
        action_name="切牌",
        rec_tile="1p",
        shanten_num=2,
        confidence=0.78,
        probabilities=probabilities,
        fold_rate=28,
        danger_score=1.03,
        draw_info="摸切 6",
        melds_count=2,
        alternatives=["2s", "4p"],
    )
    assert hero.shanten_label.text() == "2向听"
    assert "83%" in hero.stats_line_1.text() or "0.83" in hero.stats_line_1.text()


def test_main_window_instantiation(qapp):
    window = MainWindow()
    window.show()
    assert window.title_bar.title_label.text() == "雀魂牌面"
    assert window.last_state is not None
    # Test toggling table
    window.toggle_table_visibility()
    assert window.table_card.isHidden() is True
    window.toggle_table_visibility()
    assert window.table_card.isHidden() is False
    # Test clearing state
    window.clear_state()
    assert window.last_state.hand == ()
    window.close()


def test_interactive_hand_simulation(qapp):
    window = MainWindow()
    window.show()
    # Initial state has 1p recommended
    assert window._current_chosen is not None
    # Simulate clicking '2s' in hand
    window.on_hand_tile_clicked("2s")
    assert window._current_chosen is not None
    assert window._current_chosen.tile == "2s"
    assert "模拟打 2s" in window._current_chosen.label
    assert window.hero_card.rec_tile == "2s"
    window.close()
