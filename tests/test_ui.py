from __future__ import annotations

import os
import hashlib
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mahjong_jev_advisor.app import (
    HUD_STYLESHEET, MainWindow, _demo_reference_state, overlay_size_for_screen,
    setup_chinese_font,
)
from mahjong_jev_advisor.ui_components import (
    BottomActionBar, FourPlayersRiverWidget, HandWidget, HUDTitleBar,
    DecisionHeroCard, RoundInfoBar,
)
from mahjong_jev_advisor.ui_tiles import CompactTileBadge, MahjongTileWidget
from mahjong_jev_advisor.tiles import RED_TILES, TILES


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


def test_every_tile_face_renders_as_a_distinct_nonempty_image(qapp):
    setup_chinese_font(qapp)
    hashes = set()
    for tile in TILES + RED_TILES:
        widget = MahjongTileWidget(tile=tile, width=42, height=58, show_code=False)
        widget.show()
        qapp.processEvents()
        image = widget.grab().toImage()
        assert not image.isNull()
        digest = hashlib.sha256(bytes(image.bits())).hexdigest()
        hashes.add(digest)
        widget.close()
    assert len(hashes) == len(TILES + RED_TILES)


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
    window._load_initial_preview()
    # Explicit preview is available for component inspection.
    assert window._current_chosen is not None
    # Simulate clicking '2s' in hand
    window.on_hand_tile_clicked("2s")
    assert window._current_chosen is not None
    assert window._current_chosen.tile == "2s"
    assert "模拟打 2s" in window._current_chosen.label
    assert window.hero_card.rec_tile == "2s"
    window.close()


def test_content_drag_does_not_move_window(qapp):
    window = MainWindow()
    window.show()
    qapp.processEvents()
    start = window.pos()
    QTest.mousePress(window.hero_card, Qt.MouseButton.LeftButton, pos=QPoint(45, 45))
    QTest.mouseMove(window.hero_card, QPoint(75, 65))
    QTest.mouseRelease(window.hero_card, Qt.MouseButton.LeftButton, pos=QPoint(75, 65))
    qapp.processEvents()
    assert window.pos() == start
    drag_area = window.title_bar.drag_area
    QTest.mousePress(drag_area, Qt.MouseButton.LeftButton, pos=QPoint(20, 10))
    QTest.mouseMove(drag_area, QPoint(70, 50))
    QTest.mouseRelease(drag_area, Qt.MouseButton.LeftButton, pos=QPoint(70, 50))
    qapp.processEvents()
    assert window.pos() != start
    from_status = window.title_bar.status_pill.mapTo(
        window.title_bar, window.title_bar.status_pill.rect().center()
    )
    second_start = window.pos()
    QTest.mousePress(drag_area, Qt.MouseButton.LeftButton, pos=from_status)
    QTest.mouseMove(drag_area, from_status + QPoint(30, 20))
    QTest.mouseRelease(drag_area, Qt.MouseButton.LeftButton, pos=from_status + QPoint(30, 20))
    qapp.processEvents()
    assert window.pos() != second_start
    window.close()


def test_overlay_fits_short_screen_and_keeps_controls_visible(qapp):
    assert overlay_size_for_screen(QRect(0, 0, 1280, 680)) == (390, 616)
    assert overlay_size_for_screen(QRect(0, 0, 1920, 1040)) == (390, 720)

    class ShortScreen:
        def availableGeometry(self):
            return QRect(0, 0, 1280, 680)

    previous_style = qapp.styleSheet()
    previous_font = qapp.font()
    setup_chinese_font(qapp)
    qapp.setStyleSheet(HUD_STYLESHEET)
    window = MainWindow()
    try:
        window.show()
        window._adapt_to_screen(ShortScreen())
        qapp.processEvents()
        assert window.height() == 616
        assert window.table_card.isHidden()
        assert window.hand_widget.btn_toggle.text() == "展开四家"
        assert window.bottom_bar.isVisible()
        assert window.content_scroll.widget().width() <= window.content_scroll.viewport().width()
    finally:
        window.close()
        qapp.setStyleSheet(previous_style)
        qapp.setFont(previous_font)


def test_missing_key_waits_for_jev_without_rule_advice(qapp):
    window = MainWindow()
    window.settings.api_key = ""
    state = _demo_reference_state()
    window.pending_identity = state.identity()
    window.start_advice(state)
    assert window._current_chosen is None
    assert "等待 API Key" in window.hero_card.source_label.text()
    assert window.bottom_bar.status_title.text() == "Jev 待连接"
    window.close()


def test_preview_cannot_be_sent_as_live_advice(qapp):
    window = MainWindow()
    assert not window._demo_preview
    assert window._current_chosen is None
    window._load_initial_preview()
    assert window._demo_preview
    window._force_recompute_advice()
    assert window.bottom_bar.status_title.text() == "等待真实牌局"
    window.close()


def test_dialog_button_sends_actual_http_and_receives_answer(qapp, http_server):
    from mahjong_jev_advisor.ui_dialogs import JevSettingsDialog
    from mahjong_jev_advisor.settings import Settings
    dialog = JevSettingsDialog(Settings(api_key="test", model_endpoint=http_server.endpoint))
    dialog.show()
    QTest.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
    for _ in range(100):
        QTest.qWait(20)
        if dialog.test_button.isEnabled():
            break
    assert len(http_server.received) == 1
    assert "连接成功" in dialog.result.toPlainText()
    assert "HTTP 200" in dialog.result.toPlainText()
    dialog.close()


def test_uncertain_frame_removes_previous_recommendation(qapp):
    from mahjong_jev_advisor.vision import Observation
    window = MainWindow()
    window._load_initial_preview()
    window.running = True
    window.on_observation(Observation(None, 0.1, ("手牌不确定",), {}))
    assert window._current_chosen is None
    assert not window.hero_card.rec_tile
    assert "暂停建议" in window.hero_card.source_label.text()
    assert not window.windowFlags() & Qt.WindowType.FramelessWindowHint
    window.close()


def test_real_decision_worker_updates_ui_from_http_reply(qapp, http_server):
    from mahjong_jev_advisor.state import GameState
    window = MainWindow()
    window.settings.api_key = "test"
    window.settings.model_endpoint = http_server.endpoint
    state = GameState.from_dict({"hand": "123m456p789s123z55m"})
    window.last_state = state
    window.pending_identity = state.identity()
    window.start_advice(state)
    for _ in range(100):
        QTest.qWait(20)
        if not window.advice_busy:
            break
    assert len(http_server.received) == 1
    assert window._current_chosen is not None
    assert "test-model" in window.hero_card.source_label.text()
    assert window.hero_card.bars_layout.count() == 0  # No invented probability bars.
    window.close()
