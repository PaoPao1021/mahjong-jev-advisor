from __future__ import annotations

import os
import hashlib
import time
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



def wait_for_ui(qapp, predicate, *, timeout=10.0, diagnostic=lambda: ""):
    """Pump queued Qt signals while giving Python workers time to run.

    QTest.qWait alone is not a reliable scheduling point for Python QRunnables
    and the HTTP server on Windows. sleep explicitly releases the GIL, and a
    monotonic deadline bounds the wait without assuming a runner's speed.
    """
    deadline = time.monotonic() + timeout
    while True:
        qapp.processEvents()
        if predicate():
            return
        if time.monotonic() >= deadline:
            pytest.fail(f"UI did not complete within {timeout:g}s: {diagnostic()}")
        time.sleep(0.01)


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


@pytest.mark.parametrize("selected", ["0p", "5p"])
def test_hand_highlight_distinguishes_red_and_ordinary_five(qapp, selected):
    from mahjong_jev_advisor.state import GameState

    hand = HandWidget()
    state = GameState.from_dict({"hand": "123m405p789s123z55m"})
    hand.set_hand(state.hand, highlight_tile=selected)
    glowing = [s.tile for s in (*hand.hand_slots, hand.draw_slot) if s.glowing]
    assert glowing == [selected]


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
        danger_label="注意",
        danger_key="watch",
        threat_note="立直 1 家",
        draw_info="摸切 6",
        melds_count=2,
        alternatives=["2s", "4p"],
    )
    assert hero.shanten_label.text() == "2向听"
    assert "83%" in hero.stats_line_1.text() or "0.83" in hero.stats_line_1.text()
    assert hero.pill_danger.text() == "危险 注意"
    assert hero.pill_fold.text() == "立直 1 家"


@pytest.mark.parametrize("action,label", [("ron", "荣和"), ("tsumo", "自摸"), ("pass", "过")])
def test_non_discard_advice_is_displayed_as_a_completed_decision(qapp, action, label):
    from mahjong_jev_advisor.state import Advice, Candidate

    window = MainWindow()
    state = _demo_reference_state()
    chosen = Candidate(action, None, label, "已确认的动作")
    try:
        window.last_state = state
        window.pending_identity = state.identity()
        window.on_advice((state.identity(), Advice(chosen, source="jev", confidence=0.8), state))
        assert window.hero_card.action_badge.text() == f"AI {label}"
        assert window.hero_card.shanten_label.text() == "动作建议"
        assert not window.hero_card.confidence_pill.isHidden()
        assert "0.80" in window.hero_card.stats_line_1.text()
        assert "等待" not in window.hero_card.stats_line_1.text()
        assert window.bottom_bar.status_summary.text().startswith(label)
        window.clear_state()
        assert window.hero_card.confidence_pill.isHidden()
        assert "等待" in window.hero_card.stats_line_1.text()
    finally:
        window.close()


def test_riichi_and_discard_remain_distinct_in_probabilities_and_alternatives(qapp):
    from PySide6.QtWidgets import QLabel
    from mahjong_jev_advisor.rules import candidates
    from mahjong_jev_advisor.state import Advice, GameState

    state = GameState.from_dict({"hand": "123456m123p456s55z", "buttons": ["riichi"]})
    options = candidates(state)
    chosen = next(c for c in options if c.action == "riichi")
    discard = next(c for c in options if c.action == "discard" and c.tile == chosen.tile)
    probabilities = {f"a{options.index(chosen)}": 0.75, f"a{options.index(discard)}": 0.25}
    window = MainWindow()
    try:
        window.last_state = state
        window.pending_identity = state.identity()
        window.on_advice((state.identity(), Advice(chosen, (discard,), source="jev", probabilities=probabilities), state))
        bars = [window.hero_card.bars_layout.itemAt(i).widget() for i in range(window.hero_card.bars_layout.count())]
        assert [(b.label, b.is_top) for b in bars[:2]] == [(chosen.label, True), (discard.tile, False)]
        assert isinstance(window.hero_card.alt_badges_layout.itemAt(0).widget(), CompactTileBadge)

        window.on_advice((state.identity(), Advice(discard, (chosen,), source="rules"), state))
        alternate = window.hero_card.alt_badges_layout.itemAt(0).widget()
        assert isinstance(alternate, QLabel) and alternate.text() == chosen.label
        bars = [window.hero_card.bars_layout.itemAt(i).widget() for i in range(window.hero_card.bars_layout.count())]
        assert sum(b.is_top for b in bars) == 1
        assert next(b.label for b in bars if b.is_top) == discard.tile
    finally:
        window.close()


def test_danger_level_band_mapping(qapp):
    from mahjong_jev_advisor.rules import danger_level

    assert danger_level(None) == ("—", "unknown")
    assert danger_level(0) == ("安全", "safe")
    assert danger_level(1) == ("较安全", "low")
    assert danger_level(3) == ("注意", "watch")
    assert danger_level(4) == ("危险", "high")
    assert danger_level(9) == ("高危", "severe")


def test_table_seats_rotate_wind_labels_with_round(qapp):
    from mahjong_jev_advisor.ui_components import FourPlayersRiverWidget

    card = FourPlayersRiverWidget()
    scores = [25000, 25000, 25000, 25000]
    card.update_table(scores, [False] * 4, [()] * 4, [()] * 4, my_seat=1, seat_wind="S")
    assert [row.wind_label.text() for row in card.rows] == ["东", "南", "西", "北"]
    card.update_table(scores, [False] * 4, [()] * 4, [()] * 4, my_seat=0, seat_wind="N")
    # In East-2 the dealer moves to seat 1: the first seat becomes North.
    assert [row.wind_label.text() for row in card.rows] == ["北", "东", "南", "西"]
    # Sanma hides the fourth seat and never shows a North wind.
    card.update_table(scores[:3], [False] * 3, [()] * 3, [()] * 3, my_seat=0, seat_wind="E")
    assert [row.wind_label.text() for row in card.rows[:3]] == ["东", "南", "西"]
    assert card.rows[3].isHidden()


@pytest.mark.parametrize("count,seat,wind,expected", [
    (4, 0, "W", ["西", "北", "东", "南"]),
    (3, 1, "W", ["南", "西", "东"]),
    (4, 0, "?", ["?"] * 4),
    (3, 2, "?", ["?"] * 3),
])
def test_render_uses_observed_wind_and_does_not_invent_wall_count(qapp, count, seat, wind, expected):
    from mahjong_jev_advisor.state import GameState

    state = GameState.from_dict({
        "player_count": count, "seat": seat, "seat_wind": wind,
        "round_wind": "E", "round_number": 1,
    })
    window = MainWindow()
    try:
        window._render_state(state)
        assert [row.wind_label.text() for row in window.table_card.rows[:count]] == expected
        assert "余 " not in window.round_bar.round_text.text()
    finally:
        window.close()


@pytest.mark.parametrize("flags,label,key,note", [
    ((False, None, None, None), "—", "unknown", "立直状态未知"),
    ((False, True, None, False), "—", "unknown", "立直 1 家 · 1 家未知"),
    ((False, False, False, False), "未评估", "unknown", "未见对手立直"),
    ((None, False, False, False), "未评估", "unknown", "未见对手立直"),
    ((False, True, False, False), "注意", "watch", "立直 1 家"),
])
def test_danger_display_respects_unknown_opponents(qapp, flags, label, key, note):
    from dataclasses import replace
    from mahjong_jev_advisor.state import Advice, Candidate
    from mahjong_jev_advisor.ui_dialogs import DiscardComparisonDialog

    state = replace(_demo_reference_state(), riichi=flags)
    chosen = Candidate("discard", state.hand[-1], "切牌", "测试", danger=3)
    window = MainWindow()
    dialog = DiscardComparisonDialog(state, (chosen,))
    try:
        window.pending_identity = state.identity()
        window.on_advice((state.identity(), Advice(chosen), state))
        assert window.hero_card.pill_danger.text() == f"危险 {label}"
        assert window.hero_card.pill_fold.text() == note
        assert window._calculate_metrics(state, chosen)[:3] == (label, key, note)
        assert dialog.table.item(0, 3).text() == ("3" if key == "watch" else "—")
    finally:
        dialog.close()
        window.close()


@pytest.mark.parametrize("wind,number,expected", [
    ("?", None, "局况未知"), ("E", None, "东?局"),
    ("?", 2, "?2局"), ("S", 3, "南3局"),
])
def test_round_header_preserves_unknown_fields_and_shows_honba(qapp, wind, number, expected):
    from dataclasses import replace

    window = MainWindow()
    try:
        state = replace(_demo_reference_state(), round_wind=wind, round_number=number, honba=2, sticks=1)
        window._render_state(state)
        text = window.round_bar.round_text.text()
        assert text.startswith(expected)
        assert "2本场" in text and "供托1" in text
        assert "东家" in text
    finally:
        window.close()


def test_table_reports_flattened_melds_as_tiles_not_groups(qapp):
    card = FourPlayersRiverWidget()
    card.update_table([25000] * 4, [False] * 4, [()] * 4,
                      [("1m",) * 3, ("2p",) * 4, ("3s",) * 3 + ("4s",) * 4, ()])
    assert [r.meld_label.text() for r in card.rows] == ["副露 3 张", "副露 4 张", "副露 7 张", "门清"]


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


def test_teardown_event_after_scroll_area_deleted(qapp):
    from PySide6.QtCore import QEvent
    from shiboken6 import delete

    window = MainWindow()
    window.close()
    delete(window.content_scroll)
    assert window.eventFilter(window, QEvent(QEvent.Type.DeferredDelete)) is False


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
    try:
        dialog.show()
        QTest.mouseClick(dialog.test_button, Qt.MouseButton.LeftButton)
        wait_for_ui(qapp, dialog.test_button.isEnabled,
                    diagnostic=dialog.result.toPlainText)
        assert len(http_server.received) == 1
        assert "连接成功" in dialog.result.toPlainText()
        assert "HTTP 200" in dialog.result.toPlainText()
    finally:
        dialog.close()


def test_capture_failure_removes_previous_recommendation(qapp):
    window = MainWindow()
    window._load_initial_preview()
    window.running = True
    window.on_capture_failure("连接中断")
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
    try:
        window.start_advice(state)
        wait_for_ui(qapp, lambda: not window.advice_busy,
                    diagnostic=lambda: f"HTTP requests={len(http_server.received)}; "
                    f"status={window.hero_card.source_label.text()}")
        assert len(http_server.received) == 1
        assert window._current_chosen is not None
        assert "test-model" in window.hero_card.source_label.text()
        assert window.hero_card.bars_layout.count() == 0  # No invented probability bars.
    finally:
        window.close()


@pytest.mark.parametrize('failed', [False, True])
def test_hook_queues_latest_state_while_request_is_busy(qapp, monkeypatch, failed):
    from dataclasses import replace
    from mahjong_jev_advisor.state import Advice, Candidate, GameState
    window = MainWindow()
    window.running = True
    old = GameState.from_dict({'hand': '123m456p789s123z55m'})
    latest = replace(old, honba=1)
    window.last_state = old
    window.pending_identity = old.identity()
    window.advice_busy = True
    started = []
    monkeypatch.setattr(window, 'start_advice', started.append)
    window.on_hook_state(latest)
    assert window.pending_identity == latest.identity()
    assert not started
    if failed:
        window.on_advice_failure((old.identity(), 'old request failed'))
    else:
        window.on_advice((old.identity(), Advice(Candidate('discard', '1m', 'old', 'old')), old))
    assert started == [latest]
    assert window._current_chosen is None
    window.close()


def test_manual_sanma_mode_and_three_player_table(qapp):
    from mahjong_jev_advisor.ui_dialogs import ManualStateDialog
    from mahjong_jev_advisor.state import GameState
    state = GameState.from_dict({'player_count': 3, 'hand': '19m123456p123s114z'})
    dialog = ManualStateDialog(state)
    assert dialog.player_count.currentData() == 3
    dialog._validate()
    assert dialog.state.player_count == 3
    table = FourPlayersRiverWidget()
    table.update_table(state.scores, state.riichi, state.rivers, state.melds)
    assert table.rows[3].isHidden()
    four = GameState()
    table.update_table(four.scores, four.riichi, four.rivers, four.melds)
    assert not table.rows[3].isHidden()
    dialog.close()


def test_manual_sanma_riichi_uses_actual_seat(qapp):
    from mahjong_jev_advisor.ui_dialogs import ManualStateDialog
    from mahjong_jev_advisor.state import GameState
    state = GameState.from_dict({'player_count': 3, 'seat': 2, 'hand': '19m123456p123s114z'})
    dialog = ManualStateDialog(state)
    assert '自家' in dialog.riichi_checks[2].text()
    assert '自家' not in dialog.riichi_checks[0].text()
    dialog.action_buttons.setText('riichi nuki')
    dialog.riichi_checks[1].setChecked(True)
    dialog._validate()
    assert dialog.state.buttons == frozenset({'riichi', 'nuki'})
    assert dialog.state.riichi == (False, True, False)
    dialog.close()


def test_input_switch_isolates_browser_and_steam_protocol_events(qapp, monkeypatch):
    window = MainWindow()
    try:
        monkeypatch.setattr(window.steam, "start", lambda: None)
        monkeypatch.setattr(window.steam, "stop", lambda: None)
        window.set_input_source("steam")
        assert window.settings.input_source == "steam"
        assert not window.running
        window.on_hook_state(_demo_reference_state())
        window.on_hook_status("connected")
        assert not window.last_state.hand and not window._hook_active
        window.running = True
        window.on_steam_status("connected")
        assert window._hook_active
        window.on_steam_state(_demo_reference_state())
        assert window.last_state.hand == _demo_reference_state().hand
        window.set_input_source("browser")
        assert window.settings.input_source == "browser"
        window.clear_state()
        window.on_steam_state(_demo_reference_state())
        assert not window.last_state.hand
    finally:
        window.close()


def test_steam_protocol_status_and_state_lifecycle(qapp, monkeypatch):
    from mahjong_jev_advisor.settings import Settings
    Settings(input_source="steam").save()
    window = MainWindow()
    try:
        assert not window.hook.connected and not window.running
        monkeypatch.setattr(window.steam, "start", lambda: None)
        monkeypatch.setattr(window.steam, "stop", lambda: None)
        window.toggle_recognition()
        assert window.running
        window.on_steam_status("proxy:127.0.0.1:10999")
        assert "10999" in window.bottom_bar.status_title.text()
        window.on_steam_status("connected")
        assert window._hook_active
        demo = _demo_reference_state()
        window.on_steam_state(demo)
        assert window.last_state == demo
        window.on_steam_status("disconnected")
        assert not window._hook_active
        window.on_steam_status("error: 端口被占用")
        assert not window._hook_active
    finally:
        window.close()


def test_steam_supports_both_four_player_and_sanma_realtime_states(qapp, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QDialog
    from mahjong_jev_advisor import app as module
    from mahjong_jev_advisor.settings import Settings
    from mahjong_jev_advisor.state import GameState
    Settings(input_source="steam").save()
    window = MainWindow()
    monkeypatch.setattr(window.steam, "start", lambda: None)
    monkeypatch.setattr(window.steam, "stop", lambda: None)
    state = GameState.from_dict({"player_count": 3, "hand": "19m123456p123s114z"})
    dialog = SimpleNamespace(state=state, learn_and_resume=SimpleNamespace(isChecked=lambda: True),
                             exec=lambda: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(module, "ManualStateDialog", lambda *args: dialog)
    monkeypatch.setattr(window, "_start_latest_if_needed", lambda: None)
    try:
        window.manual_state(force_resume=True)
        assert not window.manual and window.running and window.last_state == state
        assert window.table_card.rows[3].isHidden()
        four_player = _demo_reference_state()
        window.on_steam_state(four_player)
        assert window.last_state == four_player
        assert not window.table_card.rows[3].isHidden()
    finally:
        window.close()


def test_steam_connection_dialog_applies_settings_and_copies_configs(qapp):
    from mahjong_jev_advisor.settings import Settings
    from mahjong_jev_advisor.ui_dialogs import SteamConnectionDialog
    settings = Settings(
        input_source="steam",
        steam_capture_mode="proxy",
        steam_process_name="Jantama_MahjongSoul",
        steam_proxy_port=10999,
        steam_upstream_proxy="",
    )
    dialog = SteamConnectionDialog(settings)
    try:
        dialog.show()
        assert dialog.proxy_port.value() == 10999
        dialog._copy_steam_launch_arg()
        assert "10999" in QApplication.clipboard().text()
        dialog._copy_pac_url()
        assert "proxy.pac" in QApplication.clipboard().text()
        dialog._copy_clash_rules()
        assert "maj-soul" in QApplication.clipboard().text()
        dialog.proxy_port.setValue(11088)
        dialog.upstream_proxy.setText("http://127.0.0.1:7890")
        assert dialog.proxy_port.value() == 11088
        assert dialog.upstream_proxy.text() == "http://127.0.0.1:7890"
    finally:
        dialog.close()


@pytest.mark.parametrize("edition", ["browser", "steam"])
def test_edition_entry_uses_own_mode_and_does_not_switch(qapp, monkeypatch, edition):
    monkeypatch.setenv("MAHJONG_ADVISOR_EDITION", edition)
    window = MainWindow()
    try:
        assert window.settings.input_source == edition
        assert ("Steam" in window.windowTitle()) == (edition == "steam")
        window.set_input_source("browser" if edition == "steam" else "steam")
        assert window.settings.input_source == edition
        if edition == "steam":
            assert not window.hook.connected and not window.running
    finally:
        window.close()


@pytest.mark.parametrize('failed', [False, True])
def test_pause_discards_inflight_result_and_does_not_restart(qapp, monkeypatch, failed):
    from mahjong_jev_advisor.state import Advice, Candidate
    window = MainWindow()
    state = _demo_reference_state()
    window.last_state = state
    window.pending_identity = state.identity()
    window.running = True
    window.advice_busy = True
    window.stable_count = 2
    started = []
    monkeypatch.setattr(window, 'start_advice', started.append)
    try:
        window.toggle_recognition()
        if failed:
            window.on_advice_failure((state.identity(), 'late failure'))
        else:
            window.on_advice((state.identity(), Advice(Candidate('discard', '2s', 'old', 'old')), state))
        assert not started and not window.running
        assert window.pending_identity is None
        assert window._current_chosen is None
        assert not window.hero_card.rec_tile
        assert '暂停' in window.hero_card.source_label.text()
    finally:
        window.close()


def test_changed_protocol_state_clears_old_advice_and_requests_fresh(qapp, monkeypatch):
    from dataclasses import replace
    window = MainWindow()
    window._load_initial_preview()
    old = window.last_state
    window.pending_identity = window.advised_identity = old.identity()
    window.running = True
    window.manual = False
    latest = replace(old, honba=old.honba + 1)
    started = []
    monkeypatch.setattr(window, 'start_advice', started.append)
    try:
        window.on_hook_state(latest)
        assert window._current_chosen is None and not window.hero_card.rec_tile
        assert started == [latest]
    finally:
        window.close()


def test_capture_failure_then_same_hand_requests_fresh_advice(qapp, monkeypatch):
    window = MainWindow()
    state = _demo_reference_state()
    window.running = True
    window.manual = False
    window.pending_identity = window.advised_identity = state.identity()
    started = []
    monkeypatch.setattr(window, 'start_advice', started.append)
    try:
        window.on_capture_failure('连接中断')
        window.on_hook_state(state)
        assert started == [state]
    finally:
        window.close()


def test_paused_hook_clears_old_candidate(qapp):
    from dataclasses import replace
    window = MainWindow()
    window._load_initial_preview()
    window.running = False
    try:
        window.on_hook_state(replace(window.last_state, honba=1))
        assert window._current_chosen is None and not window.hero_card.rec_tile
        assert window.pending_identity is None
    finally:
        window.close()


def test_simulation_has_no_invented_probability(qapp):
    window = MainWindow()
    try:
        window._load_initial_preview()
        window.on_hand_tile_clicked('2s')
        assert window.hero_card.bars_layout.count() == 0
        assert window.hero_card.confidence_pill.text() == '未提供置信度'
    finally:
        window.close()


def test_simulation_only_analyzes_the_clicked_tile(qapp, monkeypatch):
    from mahjong_jev_advisor import app as module

    def unexpected_candidates(*args):
        pytest.fail("A single-tile simulation must not compute all legal actions")

    window = MainWindow()
    try:
        window._load_initial_preview()
        monkeypatch.setattr(module, "candidates", unexpected_candidates)
        window.on_hand_tile_clicked("2s")
        assert window._current_chosen.tile == "2s"
        assert "危险级" not in window.bottom_bar.status_summary.text()
    finally:
        window.close()


@pytest.mark.parametrize("source", ["browser", "steam"])
@pytest.mark.parametrize("simulate", [False, True])
def test_repeated_state_preserves_selected_tile_and_highlight(qapp, source, simulate, monkeypatch):
    from mahjong_jev_advisor.state import Advice
    from mahjong_jev_advisor.rules import discard_candidate

    window = MainWindow()
    monkeypatch.setattr(window.steam, "start", lambda: None)
    monkeypatch.setattr(window.steam, "stop", lambda: None)
    window.settings.input_source = source
    state = _demo_reference_state()
    try:
        window.running = True
        window.last_state = state
        window.pending_identity = state.identity()
        window.on_advice((state.identity(), Advice(discard_candidate(state, "2s")), state))
        if simulate:
            window.on_hand_tile_clicked("2s")
        chosen = window._current_chosen
        if source == "browser":
            window.on_hook_state(state)
        else:
            window.on_steam_state(state)
        assert window._current_chosen == chosen
        glowing = [s.tile for s in (*window.hand_widget.hand_slots, window.hand_widget.draw_slot) if s.glowing]
        assert glowing and set(glowing) == {"2s"}
        assert window.hero_card.rec_tile == "2s"
    finally:
        window.close()


@pytest.mark.parametrize("failed", [False, True])
def test_background_result_does_not_replace_manual_simulation(qapp, monkeypatch, failed):
    from mahjong_jev_advisor import app as module
    from mahjong_jev_advisor.rules import discard_candidate
    from mahjong_jev_advisor.state import Advice

    window = MainWindow()
    logs = []
    monkeypatch.setattr(module, "append_decision_log", logs.append)
    try:
        window._load_initial_preview()
        state = window.last_state
        window.pending_identity = state.identity()
        window.advice_busy = True
        window.settings.logging_enabled = True
        window.on_hand_tile_clicked("2s")
        chosen = window._current_chosen
        if failed:
            window.on_advice_failure((state.identity(), "background failure"))
        else:
            window.on_advice((state.identity(), Advice(discard_candidate(state, "1p")), state))
        assert window._current_chosen == chosen
        assert window.hero_card.rec_tile == "2s"
        assert window.bottom_bar.status_title.text() == "手动模拟切牌"
        assert window.advised_identity == state.identity()
        assert not window.advice_busy
        assert len(logs) == (0 if failed else 1)
    finally:
        window.close()


@pytest.mark.parametrize("reason", ["pause", "capture", "steam_error", "changed"])
def test_invalidating_state_clears_simulation_and_highlights(qapp, reason, monkeypatch):
    from dataclasses import replace

    window = MainWindow()
    monkeypatch.setattr(window.steam, "start", lambda: None)
    monkeypatch.setattr(window.steam, "stop", lambda: None)
    try:
        window._load_initial_preview()
        window.running = True
        window.pending_identity = window.last_state.identity()
        window.on_hand_tile_clicked("2s")
        if reason == "pause":
            window.toggle_recognition()
        elif reason == "capture":
            window.on_capture_failure("采集失败")
        elif reason == "steam_error":
            window.settings.input_source = "steam"
            window.on_steam_status("error: 端口占用")
        else:
            window.on_hook_state(replace(window.last_state, honba=1))
        assert window._simulation_identity is None
        assert window._current_chosen is None
        assert not any(s.glowing for s in (*window.hand_widget.hand_slots, window.hand_widget.draw_slot))
    finally:
        window.close()


def test_explicit_ai_request_exits_simulation_and_reuses_inflight_request(qapp):
    from types import SimpleNamespace
    from mahjong_jev_advisor.rules import discard_candidate
    from mahjong_jev_advisor.state import Advice

    window = MainWindow()
    workers = []
    window.pool = SimpleNamespace(start=workers.append, waitForDone=lambda *args: None)
    try:
        window._load_initial_preview()
        window._demo_preview = False
        state = window.last_state
        window.pending_identity = state.identity()
        window.settings.api_key = "test"
        window.on_hand_tile_clicked("2s")
        window.start_advice(state)
        assert window.hero_card.rec_tile == "2s"
        assert window._simulation_identity == state.identity()
        assert len(workers) == 1
        window._force_recompute_advice()
        assert window._simulation_identity is None
        assert window._current_chosen is None
        assert not window.hero_card.rec_tile
        assert len(workers) == 1
        chosen = discard_candidate(state, "1p")
        window.on_advice((state.identity(), Advice(chosen), state))
        assert window._current_chosen == chosen
        assert window.hero_card.rec_tile == "1p"
    finally:
        window.close()


def test_ai_request_cannot_revive_state_after_capture_failure(qapp, monkeypatch):
    window = MainWindow()
    started = []
    monkeypatch.setattr(window, "start_advice", started.append)
    try:
        window._load_initial_preview()
        window._demo_preview = False
        window.on_capture_failure("采集失败")
        window._force_recompute_advice()
        assert not started
        assert window.pending_identity is None
        assert window.bottom_bar.status_title.text() == "暂时无法请求建议"
    finally:
        window.close()


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("changed", ["state", "connection"])
def test_explicit_request_queues_while_paused(qapp, failed, changed):
    from dataclasses import replace
    from types import SimpleNamespace
    from mahjong_jev_advisor.rules import discard_candidate
    from mahjong_jev_advisor.state import Advice

    window = MainWindow()
    workers = []
    window.pool = SimpleNamespace(start=workers.append, waitForDone=lambda *args: None)
    old = _demo_reference_state()
    latest = replace(old, honba=1) if changed == "state" else old
    try:
        window.last_state = old
        window._render_state(old)
        window.pending_identity = old.identity()
        window.settings.api_key = "test"
        window.start_advice(old)
        window.on_hook_state(latest)
        if changed == "connection":
            window._connection_revision += 1
        window._force_recompute_advice()
        # Duplicate snapshots arriving while paused must preserve this request.
        window.on_hook_state(latest)
        assert not window.running and window.stable_count == 0
        assert window.pending_identity == latest.identity()
        assert len(workers) == 1
        if failed:
            window.on_advice_failure((old.identity(), "old failure"))
        else:
            window.on_advice((old.identity(), Advice(discard_candidate(old, "1p")), old))
        assert len(workers) == 2
        assert workers[-1].state == latest
        chosen = discard_candidate(latest, "2s")
        window.on_advice((latest.identity(), Advice(chosen), latest))
        assert window._current_chosen == chosen
        assert window._explicit_request_identity is None
    finally:
        window.close()


@pytest.mark.parametrize("cancel", ["clear", "capture", "pause"])
def test_canceling_state_cancels_queued_explicit_request(qapp, cancel):
    from dataclasses import replace
    from types import SimpleNamespace

    window = MainWindow()
    workers = []
    window.pool = SimpleNamespace(start=workers.append, waitForDone=lambda *args: None)
    state = _demo_reference_state()
    try:
        window.last_state = state
        window._render_state(state)
        window.pending_identity = state.identity()
        window.settings.api_key = "test"
        window.start_advice(state)
        window.on_hook_state(replace(state, honba=1))
        window._force_recompute_advice()
        if cancel == "clear":
            window.clear_state()
        elif cancel == "capture":
            window.on_capture_failure("capture failed")
        else:
            window.running = True
            window.toggle_recognition()
        window.on_advice_failure((state.identity(), "old failure"))
        assert len(workers) == 1
        assert window._explicit_request_identity is None
        assert window._current_chosen is None
    finally:
        window.close()


@pytest.mark.parametrize("hand,confidence,available,reason", [
    ("123m456p789s123z5m", 1.0, True, "摸牌"),
    ("123m456p789s123z55m", 0.5, True, "置信度"),
    ("123m456p789s123z55m", 1.0, False, "画面不可用"),
])
def test_simulation_rejects_unavailable_or_incomplete_state(qapp, hand, confidence, available, reason):
    from mahjong_jev_advisor.state import GameState

    window = MainWindow()
    state = GameState.from_dict({"hand": hand, "observation_confidence": confidence})
    try:
        window.last_state = state
        window._render_state(state)
        window._comparison_state_available = available
        window.on_hand_tile_clicked("1m")
        assert window._current_chosen is None
        assert window.bottom_bar.status_title.text() == "暂时无法模拟"
        assert reason in window.bottom_bar.status_summary.text()
    finally:
        window.close()


def test_zero_ukeire_is_displayed_without_inventing_draw_status(qapp):
    from mahjong_jev_advisor.state import Advice, Candidate
    window = MainWindow()
    state = _demo_reference_state()
    window.pending_identity = state.identity()
    chosen = Candidate('discard', state.hand[-1], 'discard', 'test', shanten=0, ukeire=0)
    try:
        window.on_advice((state.identity(), Advice(chosen), state))
        assert window.hero_card.pill_draw_status.text() == '有效牌 0 张'
    finally:
        window.close()


def test_ctrl_wheel_changes_opacity_and_regular_wheel_does_not(qapp):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QWheelEvent
    window = MainWindow()
    try:
        window.show()
        window.set_opacity(0.8)
        for modifiers, expected in [(Qt.KeyboardModifier.NoModifier, 0.8),
                                    (Qt.KeyboardModifier.ControlModifier, 0.85)]:
            event = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, 120),
                                Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(window.content_scroll.viewport(), event)
            assert window.settings.opacity == pytest.approx(expected)
    finally:
        window.close()


def test_comparison_table_numeric_sort_and_copy(qapp):
    from mahjong_jev_advisor.rules import discard_comparison
    from mahjong_jev_advisor.ui_dialogs import DiscardComparisonDialog
    state = _demo_reference_state()
    rows = discard_comparison(state)
    dialog = DiscardComparisonDialog(state, rows)
    try:
        assert dialog.table.rowCount() == len(set(state.hand))
        assert [dialog.table.item(i, 0).text() for i in range(len(rows))] == [c.tile for c in rows]
        dialog.table.sortItems(2, Qt.SortOrder.AscendingOrder)
        counts = [int(dialog.table.item(i, 2).text()) for i in range(len(rows))]
        assert counts == sorted(c.ukeire for c in rows)
        dialog.copy_table()
        text = QApplication.clipboard().text()
        assert len(text.splitlines()) == len(rows) + 1
        assert '进张（张）' in text and '危险级' in text
        assert text.splitlines()[1].split('\t')[0] == dialog.table.item(0, 0).text()
    finally:
        dialog.close()


@pytest.mark.parametrize("column,order", [
    (2, Qt.SortOrder.DescendingOrder),
    (1, Qt.SortOrder.AscendingOrder),
])
def test_comparison_refresh_preserves_sort_and_exact_selected_tile(qapp, column, order):
    from dataclasses import replace
    from mahjong_jev_advisor.rules import discard_comparison
    from mahjong_jev_advisor.state import GameState
    from mahjong_jev_advisor.ui_dialogs import DiscardComparisonDialog

    state = GameState.from_dict({"hand": "123m405p789s123z55m"})
    dialog = DiscardComparisonDialog(state, discard_comparison(state))
    try:
        dialog.table.sortItems(column, order)
        red_row = next(r for r in range(dialog.table.rowCount()) if dialog.table.item(r, 0).text() == "0p")
        dialog.table.selectRow(red_row)
        changed = replace(state, rivers=(("1m",), (), (), ()))
        options = discard_comparison(changed)
        dialog.set_snapshot(changed, options)
        assert dialog.table.horizontalHeader().sortIndicatorSection() == column
        assert dialog.table.horizontalHeader().sortIndicatorOrder() == order
        values = [int(dialog.table.item(r, column).text()) for r in range(dialog.table.rowCount())]
        assert values == sorted(values, reverse=order == Qt.SortOrder.DescendingOrder)
        assert dialog.table.item(dialog.table.currentRow(), 0).text() == "0p"
        assert dialog.detail_label.text() == next(c.rationale for c in options if c.tile == "0p")

        # A removed red five must not silently select its ordinary-five counterpart.
        without_red = replace(changed, hand=tuple("5p" if t == "0p" else t for t in changed.hand))
        dialog.set_snapshot(without_red, discard_comparison(without_red))
        assert dialog.table.currentRow() == 0
        assert dialog.detail_label.text() == dialog.table.item(0, 0).data(Qt.ItemDataRole.UserRole)
        dialog.set_snapshot(without_red, ())
        assert dialog.table.rowCount() == 0
        assert dialog.detail_label.text() == "当前没有可比较的切法"
    finally:
        dialog.close()


@pytest.mark.parametrize("trigger", ["button", "enter", "double_click"])
def test_comparison_can_simulate_selected_red_five(qapp, trigger):
    from mahjong_jev_advisor.state import GameState

    window = MainWindow()
    state = GameState.from_dict({"hand": "123m405p789s123z55m"})
    try:
        window.last_state = state
        window._render_state(state)
        window.show_discard_comparison()
        dialog = window._comparison_dialog
        dialog.table.sortItems(2, Qt.SortOrder.DescendingOrder)
        row = next(r for r in range(dialog.table.rowCount()) if dialog.table.item(r, 0).text() == "0p")
        dialog.table.selectRow(row)
        dialog.table.scrollToItem(dialog.table.item(row, 0))
        dialog.table.setFocus()
        qapp.processEvents()
        if trigger == "button":
            QTest.mouseClick(dialog.simulate_button, Qt.MouseButton.LeftButton)
        elif trigger == "enter":
            QTest.keyClick(dialog.table, Qt.Key.Key_Return)
        else:
            point = dialog.table.visualItemRect(dialog.table.item(row, 0)).center()
            QTest.mouseClick(dialog.table.viewport(), Qt.MouseButton.LeftButton, pos=point)
            QTest.mouseDClick(dialog.table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        assert window._current_chosen is not None
        assert window._current_chosen.tile == "0p"
        assert window.hero_card.rec_tile == "0p"
        assert dialog.isVisible()
        QApplication.clipboard().clear()
        QTest.keyClick(dialog.table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert "切牌\t向听" in QApplication.clipboard().text()
    finally:
        window.close()


@pytest.mark.parametrize("available", [False, True])
def test_comparison_rejects_stale_simulation_even_if_already_queued(qapp, available):
    from dataclasses import replace

    window = MainWindow()
    try:
        window._load_initial_preview()
        window.show_discard_comparison()
        dialog = window._comparison_dialog
        old_identity = dialog.snapshot_identity
        if available:
            window.last_state = replace(window.last_state, honba=1)
            window._render_state(window.last_state)
        else:
            window.on_capture_failure("无法采集")
        assert not dialog.simulate_button.isEnabled()
        dialog.simulate_selected()
        assert window._current_chosen is None
        # Revalidate in the main window too, in case the state changed after emission.
        window._simulate_comparison_discard(old_identity, "2s")
        assert window._current_chosen is None
        assert "刷新" in dialog.feedback_label.text()
    finally:
        window.close()


def test_repeated_comparison_window_teardown_keeps_screen_alive(qapp):
    import gc

    for _ in range(4):
        window = MainWindow()
        window._load_initial_preview()
        window.show_discard_comparison()
        qapp.processEvents()
        window.close()
        del window
        gc.collect()
        qapp.processEvents()
        assert qapp.primaryScreen().availableGeometry().isValid()


def test_comparison_unavailable_hand_shows_notice_without_dialog(qapp, monkeypatch):
    from mahjong_jev_advisor import app as module
    from mahjong_jev_advisor.state import GameState
    window = MainWindow()
    notices = []
    monkeypatch.setattr(module.QMessageBox, 'information', lambda *args: notices.append(args))
    window.last_state = GameState.from_dict({'hand': '123m456p789s123z5m'})
    try:
        window.show_discard_comparison()
        assert notices and '暂时无法比较' in notices[0]
    finally:
        window.close()


@pytest.mark.parametrize('available,saved', [
    (QRect(0, 0, 1280, 720), QRect(3000, 2000, 450, 640)),
    (QRect(-1920, 0, 1920, 1080), QRect(-1800, 80, 450, 640)),
    (QRect(0, 0, 800, 600), QRect(-200, -100, 1600, 1200)),
])
def test_saved_window_fits_available_screen(available, saved):
    from mahjong_jev_advisor.app import fit_window_geometry
    fitted = fit_window_geometry(saved, available)
    assert available.adjusted(8, 32, -8, -8).contains(fitted)
    if available.adjusted(8, 32, -8, -8).contains(saved):
        assert fitted == saved


def test_window_preferences_survive_restart(qapp):
    from mahjong_jev_advisor.settings import Settings
    from mahjong_jev_advisor.app import fit_window_geometry
    window = MainWindow()
    window.show()
    qapp.processEvents()
    desired = fit_window_geometry(QRect(70, 90, 410, 510), window.screen().availableGeometry())
    window.setGeometry(desired)
    window.set_always_on_top(False)
    qapp.processEvents()
    saved = window.geometry()
    window.close()
    assert Settings.load().window_geometry == saved.getRect()
    restored = MainWindow()
    try:
        assert not restored.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        assert restored.geometry() == saved
    finally:
        restored.close()


def test_comparison_reuses_window_refreshes_and_marks_stale(qapp):
    from mahjong_jev_advisor.state import GameState
    window = MainWindow()
    window._load_initial_preview()
    try:
        window.show_discard_comparison()
        dialog = window._comparison_dialog
        assert dialog.isVisible() and not dialog.isModal()
        window.show_discard_comparison()
        assert window._comparison_dialog is dialog
        original = dialog.snapshot_identity
        changed = GameState.from_dict({'hand': '123m456p789s123z55m'})
        window.last_state = changed
        window._render_state(changed)
        assert '牌面已变化' in dialog.freshness_label.text()
        assert dialog.snapshot_identity == original
        QTest.mouseClick(dialog.refresh_button, Qt.MouseButton.LeftButton)
        assert dialog.snapshot_identity == changed.identity()
        assert '当前局面快照' in dialog.freshness_label.text()
        dialog.table.selectRow(1)
        assert dialog.detail_label.text() == dialog.table.item(1, 0).data(Qt.ItemDataRole.UserRole)
        QTest.mouseClick(dialog.copy_button, Qt.MouseButton.LeftButton)
        assert '已复制' in dialog.feedback_label.text()
        window.on_capture_failure('采集失败')
        QTest.mouseClick(dialog.refresh_button, Qt.MouseButton.LeftButton)
        assert '当前画面不可用' in dialog.freshness_label.text()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert window._comparison_dialog is None
    finally:
        window.close()


def test_comparison_dialog_works_seamlessly_during_steam_realtime_protocol_stream(qapp, monkeypatch):
    from dataclasses import replace
    window = MainWindow()
    monkeypatch.setattr(window.steam, "start", lambda: None)
    monkeypatch.setattr(window.steam, "stop", lambda: None)
    window.settings.input_source = "steam"
    window._load_initial_preview()
    window.show_discard_comparison()
    comparison = window._comparison_dialog
    window.running, window.manual = True, False
    started = []
    monkeypatch.setattr(window, "start_advice", started.append)
    try:
        updated = replace(_demo_reference_state(), honba=2)
        window.on_steam_state(updated)
        assert started == [updated]
        assert "牌面已变化" in comparison.freshness_label.text()
    finally:
        window.close()


def test_compact_action_bar_tab_order_and_bounds(qapp):
    bar = BottomActionBar()
    bar.resize(320, bar.sizeHint().height())
    bar.show()
    qapp.processEvents()
    buttons = (bar.btn_ai, bar.btn_compare, bar.btn_explain, bar.btn_jev,
               bar.btn_llm, bar.btn_clear, bar.btn_toggle, bar.btn_manual)
    try:
        for index, button in enumerate(buttons):
            assert bar.rect().contains(button.geometry())
            assert button.width() >= button.minimumSizeHint().width()
            if index + 1 < len(buttons):
                assert button.nextInFocusChain() is buttons[index + 1]
    finally:
        bar.close()
