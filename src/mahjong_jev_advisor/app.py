"""Cross-platform Cyberpunk HUD advisor window for Mahjong Soul & Jev."""

from __future__ import annotations

import ctypes
import math
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import mss
import numpy as np
from PySide6.QtCore import QPoint, QRect, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QMouseEvent
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QMainWindow, QMenu, QMessageBox, QVBoxLayout, QWidget,
)

from .jev import JevError, advise
from .rules import (
    NoDecision, UncertainState, candidates, danger, dora_value,
    shanten, shanten_text, ukeire_details, yaku_hints,
)
from .settings import Settings, append_decision_log, config_dir
from .state import Advice, Candidate, GameState
from .ui_components import (
    BottomActionBar, FourPlayersRiverWidget, HandWidget, HUDTitleBar,
    DecisionHeroCard, RoundInfoBar,
)
from .ui_dialogs import (
    JevSettingsDialog, LLMAnalysisDialog, LLMSettingsDialog, ManualStateDialog,
    RegionDialog, TemplateDialog,
)
from .vision import REGIONS, Observation, TemplateStore, VisionReader


def capture_box(box: tuple[int, int, int, int]) -> np.ndarray:
    x, y, width, height = box
    with mss.mss() as grabber:
        image = np.asarray(grabber.grab({"left": x, "top": y, "width": width, "height": height}))
    return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)


def capture_desktop() -> tuple[np.ndarray, tuple[int, int]]:
    with mss.mss() as grabber:
        monitor = grabber.monitors[0]
        image = np.asarray(grabber.grab(monitor))
    return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR), (monitor["left"], monitor["top"])


def apply_blur_effect(hwnd: int) -> None:
    """Applies Windows DWM blur behind / acrylic effect for real frosted glass HUD."""
    if sys.platform != "win32":
        return
    try:
        class ACCENT_POLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int),
            ]

        class WINDOW_COMPOSITION_ATTRIB_DATA(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t),
            ]

        policy = ACCENT_POLICY()
        policy.AccentState = 3  # ACCENT_ENABLE_BLURBEHIND
        policy.AccentFlags = 2
        policy.GradientColor = 0x88101626  # Semi-transparent dark ARGB tint

        data = WINDOW_COMPOSITION_ATTRIB_DATA()
        data.Attribute = 19  # WCA_ACCENT_POLICY
        data.Data = ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(policy)

        set_window_composition_attribute = ctypes.windll.user32.SetWindowCompositionAttribute
        set_window_composition_attribute.restype = ctypes.c_int
        set_window_composition_attribute.argtypes = [ctypes.c_void_p, ctypes.POINTER(WINDOW_COMPOSITION_ATTRIB_DATA)]
        set_window_composition_attribute(hwnd, ctypes.byref(data))
    except Exception:
        pass


class WorkerSignals:
    pass


from PySide6.QtCore import QObject


class TaskSignals(QObject):
    captured = Signal()
    done = Signal(object)
    failed = Signal(object)


class CaptureWorker(QRunnable):
    def __init__(self, box: tuple[int, int, int, int], reader: VisionReader, seat: int):
        super().__init__()
        self.box, self.reader, self.seat = box, reader, seat
        self.signals = TaskSignals()

    def run(self) -> None:
        try:
            frame = capture_box(self.box)
            self.signals.captured.emit()
            self.signals.done.emit(self.reader.analyze(frame, self.seat))
        except Exception:
            self.signals.failed.emit(traceback.format_exc(limit=3))


class AdviceWorker(QRunnable):
    def __init__(self, state: GameState, key: str):
        super().__init__()
        self.state, self.key = state, key
        self.signals = TaskSignals()

    def run(self) -> None:
        try:
            start = time.perf_counter()
            options = candidates(self.state)
            advice = advise(self.state, options, self.key)
            elapsed = int((time.perf_counter() - start) * 1000)
            object.__setattr__(advice, "latency_ms", elapsed)
            self.signals.done.emit((self.state.identity(), advice, self.state))
        except (NoDecision, UncertainState) as error:
            self.signals.failed.emit((self.state.identity(), str(error)))
        except JevError as error:
            self.signals.failed.emit((self.state.identity(), str(error)))
        except Exception:
            self.signals.failed.emit((self.state.identity(), traceback.format_exc(limit=3)))


def _demo_reference_state() -> GameState:
    """Provides the exact match state shown in the reference image for instant preview."""
    return GameState.from_dict({
        "hand": "4m4m1p1p4p4p4p2s2s2s7s8s9s1p",
        "rivers": [
            ["3m", "8m", "1s", "3z", "2m", "4m", "6s", "2m", "1m", "5p", "9s", "3p"],
            ["5z", "8m", "3m", "7z", "2p", "3s", "8p", "6m", "1p", "4s", "2s"],
            ["7p", "1z", "4z", "3z", "1m", "3m", "3p", "4z", "6p", "9m", "5s"],
            ["1z", "2z", "2p", "9s", "1z", "4s", "7p", "5p", "8s", "3m"],
        ],
        "melds": [[], [], ["3m", "4m", "5m"], []],
        "dora_indicators": ["7p"],
        "buttons": ["riichi"],
        "seat": 0,
        "round_wind": "E",
        "round_number": 1,
        "seat_wind": "N",
        "scores": [25000, 25000, 25000, 25000],
        "riichi": [False, False, False, True],
        "honba": 0,
        "sticks": 0,
        "open_melds": 0,
        "observation_confidence": 0.98,
    })


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("雀魂 · Jev 实时切牌顾问")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.settings = Settings.load()
        self._capture_excluded = False
        self.templates = TemplateStore()
        self.reader = VisionReader(self.settings.regions, self.templates)
        self.pool = QThreadPool.globalInstance()

        self.capture_busy = False
        self.capture_hidden = False
        self.advice_busy = False
        self.running = False
        self.manual = False
        self.last_state: GameState | None = None
        self.pending_identity: tuple[Any, ...] | None = None
        self.stable_count = 0
        self.advised_identity: tuple[Any, ...] | None = None
        self.active_advice_identity: tuple[Any, ...] | None = None
        self._current_chosen: Candidate | None = None
        self._drag_pos: QPoint | None = None

        # Build Main Glassmorphic HUD Layout
        panel = QFrame()
        panel.setObjectName("overlayContainer")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCentralWidget(panel)

        root_layout = QVBoxLayout(panel)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(6)

        # 1. Custom Title Bar
        self.title_bar = HUDTitleBar(self)
        self.title_bar.ai_clicked.connect(self._on_ai_menu)
        self.title_bar.table_clicked.connect(self._on_table_menu)
        self.title_bar.minimize_clicked.connect(self.showMinimized)
        self.title_bar.close_clicked.connect(self.close)
        root_layout.addWidget(self.title_bar)

        # 2. Round Info Bar (东1局 · 余 29 · 3北 · 宝牌)
        self.round_bar = RoundInfoBar(self)
        root_layout.addWidget(self.round_bar)

        # 3. Four-player status card with rivers
        self.table_card = FourPlayersRiverWidget(self)
        self.table_card.setVisible(self.settings.show_table)
        root_layout.addWidget(self.table_card)

        # 4. Hand & Draw Strip with Glowing Highlight
        self.hand_widget = HandWidget(self)
        self.hand_widget.toggle_table.connect(self.toggle_table_visibility)
        self.hand_widget.tile_clicked.connect(self.on_hand_tile_clicked)
        root_layout.addWidget(self.hand_widget)

        # 5. Hero Decision Card (Probability bars, recommendation tile, pills)
        self.hero_card = DecisionHeroCard(self)
        self.hero_card.alternative_clicked.connect(self.on_hand_tile_clicked)
        root_layout.addWidget(self.hero_card, 1)

        # 6. Bottom Action & Status Bar
        self.bottom_bar = BottomActionBar(self)
        self.bottom_bar.ai_suggest_clicked.connect(self._force_recompute_advice)
        self.bottom_bar.llm_analysis_clicked.connect(self.show_llm_analysis)
        self.bottom_bar.clear_clicked.connect(self.clear_state)
        self.bottom_bar.jev_settings_clicked.connect(self.edit_jev_settings)
        self.bottom_bar.llm_settings_clicked.connect(self.edit_llm_settings)
        self.bottom_bar.toggle_clicked.connect(self.toggle_recognition)
        self.bottom_bar.manual_clicked.connect(self.manual_state)
        root_layout.addWidget(self.bottom_bar)

        self.setMinimumWidth(380)
        self.resize(390, 720)
        self.setWindowOpacity(self.settings.opacity)

        # Periodic capture timer
        self.timer = QTimer(self)
        self.timer.setInterval(self.settings.ui_interval_ms)
        self.timer.timeout.connect(self.capture_tick)

        # Load reference preview initially so user instantly sees full graphics
        self._load_initial_preview()

        # If game_box is configured, automatically start real-time monitoring
        if self.settings.game_box:
            QTimer.singleShot(300, self.toggle_recognition)
        else:
            self.title_bar.set_status("待选牌桌", "idle")
            self.bottom_bar.set_status_info("等待绑定", "点击右上角【桌】绑定雀魂窗口以开启实时监测")

    def set_opacity(self, opacity: float) -> None:
        self.settings.opacity = max(0.45, min(1.0, float(opacity)))
        self.settings.save()
        self.setWindowOpacity(self.settings.opacity)

    def _select_opacity(self, val: float) -> None:
        self.set_opacity(val)
        self.bottom_bar.set_status_info("透明度已更新", f"当前悬浮窗透明度：{int(val * 100)}%")

    def wheelEvent(self, event: Any) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            step = 0.05 if delta > 0 else -0.05
            new_op = max(0.45, min(1.0, self.settings.opacity + step))
            self.set_opacity(new_op)
            self.bottom_bar.set_status_info("透明度调节", f"当前透明度：{int(new_op * 100)}% (Ctrl+滚轮微调)")
            event.accept()
        else:
            super().wheelEvent(event)

    def _load_initial_preview(self) -> None:
        """Loads a demo state resembling the reference image so UI looks spectacular out-of-the-box."""
        demo = _demo_reference_state()
        self.last_state = demo
        self._render_state(demo)

        demo_candidate = Candidate(
            "discard", "1p", "打 1p",
            "2向听 · 有效牌约 24 张 · 危险级 1 · 弃宝牌 0 张 · 役牌候选、门清",
            2, 24, 1, 0,
            improving_tiles=("1p", "4p", "2s", "3s"),
        )
        self._current_chosen = demo_candidate

        # Pre-compute demo advice matching the reference image: 切 1p, 2向听, 置信 78%, 83% 1p
        probabilities = [
            ("1p", 83, True),
            ("2s", 7, False),
            ("4p", 5, False),
            ("防守", 3, False),
            ("等待", 2, False),
        ]
        self.hero_card.update_decision(
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
            source_note="Jev · 校准概率可视化",
            raw_detail="2向听 · 有效牌约 24 张 · 危险级 1 · 役牌候选、门清",
        )
        self.hand_widget.set_hand(demo.hand, highlight_tile="1p")
        self.bottom_bar.set_status_info(
            "AI 建议 Jev jev-latest · 桥OK",
            "切 1p  ·  置信 78%  ·  弃和 28%",
        )
        self.title_bar.set_status("对局中", "active")

    def toggle_table_visibility(self) -> None:
        new_visible = not self.table_card.isVisible()
        self.table_card.setVisible(new_visible)
        self.settings.show_table = new_visible
        self.settings.save()
        self.hand_widget.btn_toggle.setText("展开四家" if not new_visible else "隐藏四家")

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if sys.platform == "win32":
            apply_blur_effect(int(self.winId()))
            if not self._capture_excluded:
                try:
                    # Exclude overlay window from screen capture on Windows 10 2004+
                    self._capture_excluded = bool(ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), 0x11))
                except (AttributeError, OSError, ValueError):
                    pass

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: Any) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def _on_ai_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("立即重新获取建议", self._force_recompute_advice)
        menu.addAction("大模型深度牌理解析 (LLM)", self.show_llm_analysis)
        menu.addAction("Jev 设置 (API Key)", self.edit_jev_settings)
        menu.addAction("大模型设置 (LLM)", self.edit_llm_settings)
        menu.addSeparator()
        menu.addAction("载入示范对局状态", self._load_initial_preview)
        menu.exec(self.title_bar.btn_ai.mapToGlobal(QPoint(0, self.title_bar.btn_ai.height() + 2)))

    def _on_table_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("选择牌桌画面", self.select_game)
        menu.addAction("基础区域校准", lambda: self.calibrate(False))
        menu.addAction("扩展区域校准", lambda: self.calibrate(True))
        menu.addAction("标注牌面样本", self.label_templates)
        menu.addAction("保存当前帧截图", self.save_frame)
        menu.addSeparator()
        op_menu = menu.addMenu("窗口透明度")
        for val, title in [
            (1.0, "100% 完全不透明"),
            (0.92, "92% 轻微透明"),
            (0.85, "85% 标准磨砂半透 (推荐)"),
            (0.75, "75% 高透游戏 HUD"),
            (0.60, "60% 极透模式"),
        ]:
            op_menu.addAction(title, lambda v=val: self._select_opacity(v))
        menu.exec(self.title_bar.btn_table.mapToGlobal(QPoint(0, self.title_bar.btn_table.height() + 2)))

    def _render_state(self, state: GameState, highlight_tile: str | None = None) -> None:
        # Hand & drawn tile
        self.hand_widget.set_hand(state.hand, highlight_tile=highlight_tile)

        # Round & Dora info
        dora = state.dora_indicators[0] if state.dora_indicators else ""
        seat_desc = f"{state.seat + 1}{'东' if state.seat_wind == 'E' else '北'}"
        self.round_bar.set_round_info(
            round_wind=state.round_wind,
            round_num=state.round_number or 1,
            remaining_tiles=max(0, 70 - sum(len(r) for r in state.rivers)),
            seat_desc=seat_desc,
            honba=state.honba,
            sticks=state.sticks,
            dora_indicator=dora,
        )

        # Table players rivers & scores
        self.table_card.update_table(
            scores=state.scores,
            riichi=state.riichi,
            rivers=state.rivers,
            melds=state.melds,
            my_seat=state.seat,
        )

    def _calculate_metrics(self, state: GameState, chosen: Candidate) -> tuple[int, float, list[tuple[str, int, bool]]]:
        """Calculates fold rate %, danger score, and display probability distribution."""
        threats = sum(1 for i, r in enumerate(state.riichi) if r and i != state.seat)
        raw_danger = chosen.danger or 0

        danger_score = round(1.0 + (raw_danger * 0.12) + (threats * 0.45), 2)

        if threats > 0:
            fold_rate = min(95, max(15, 20 + threats * 25 + raw_danger * 5))
        else:
            fold_rate = max(5, min(40, (chosen.shanten or 1) * 12))

        opts: list[Candidate] = []
        try:
            opts = list(candidates(state))
        except (NoDecision, UncertainState):
            pass

        bar_items: list[tuple[str, int, bool]] = []
        if opts:
            # Score each candidate by tile efficiency: 10^(-1.8*shanten) * (ukeire + 1) / (1 + 0.7*danger + 1.5*dora_loss)
            scores = []
            for opt in opts:
                s = opt.shanten if opt.shanten is not None else 2
                u = opt.ukeire if opt.ukeire is not None else 5
                d = opt.danger if opt.danger is not None else 1
                dl = opt.dora_loss
                eff = max(0.01, (10.0 ** (-1.8 * s)) * (u + 1) / (1.0 + 0.7 * d + 1.5 * dl))
                scores.append(eff)
            total_eff = sum(scores) or 1.0
            raw_pcts = [int(round(s / total_eff * 88)) for s in scores]

            top_tile = chosen.tile or chosen.label
            for i, opt in enumerate(opts):
                lbl = opt.tile or opt.label
                pct = max(3, raw_pcts[i])
                bar_items.append((lbl, pct, opt.tile == chosen.tile))
            bar_items.sort(key=lambda x: -x[1])
            bar_items = bar_items[:4]
            allocated = sum(x[1] for x in bar_items)
            rem = max(2, 100 - allocated)
            if threats > 0:
                bar_items.append(("防守", rem, False))
            else:
                bar_items.append(("等待", rem, False))
        else:
            bar_items = [(chosen.tile or chosen.label, 85, True)]

        return fold_rate, danger_score, bar_items

    def on_advice(self, result: tuple[tuple[Any, ...], Advice, GameState]) -> None:
        self.advice_busy = False
        identity, advice, state = result
        if identity != self.pending_identity:
            self._start_latest_if_needed()
            return

        self.advised_identity = identity
        chosen = advice.selected
        self._current_chosen = chosen
        rec_tile = chosen.tile or ""

        # Update hand tile highlight
        self._render_state(state, highlight_tile=rec_tile)

        # Calculate rich stats (fold rate, danger score, bars)
        fold_rate, danger_score, bar_items = self._calculate_metrics(state, chosen)

        # If Jev returned explicit probabilities, merge them
        if advice.source == "jev" and advice.probabilities:
            try:
                opts = candidates(state)
                mapped = []
                for i, opt in enumerate(opts):
                    key = f"a{i}"
                    lbl = opt.tile or opt.label
                    prob = advice.probabilities.get(key, 0.0)
                    pct = int(round(prob * 100))
                    mapped.append((lbl, pct, opt == chosen))
                if mapped:
                    # Sort descending
                    mapped.sort(key=lambda x: -x[1])
                    bar_items = mapped[:4]
            except Exception:
                pass

        action_name = "切牌" if chosen.action == "discard" else chosen.label

        alternatives_list = [alt.tile or alt.label for alt in advice.alternatives[:3]]

        source_note = (
            f"Jev {advice.model or 'latest'} · 校准概率可视化 · {advice.latency_ms}ms"
            if advice.source == "jev"
            else "本地规则确定性引擎"
        )

        yaku_badge = ""
        if chosen.expected_han is not None and chosen.expected_han > 0:
            top_yk = chosen.potential_yaku[0] if chosen.potential_yaku else ""
            yaku_badge = f"{chosen.expected_han}番" + (f" · {top_yk}" if top_yk else "")
        elif chosen.potential_yaku:
            yaku_badge = chosen.potential_yaku[0]

        self.hero_card.update_decision(
            action_name=action_name,
            rec_tile=rec_tile,
            shanten_num=chosen.shanten,
            confidence=advice.confidence or 0.82,
            probabilities=bar_items,
            fold_rate=fold_rate,
            danger_score=danger_score,
            draw_info=f"摸切 {chosen.ukeire or 6}" if rec_tile == (state.hand[-1] if state.hand else "") else "手切",
            melds_count=state.open_melds,
            alternatives=alternatives_list,
            source_note=source_note,
            raw_detail=chosen.rationale,
            yaku_badge=yaku_badge,
        )

        self.bottom_bar.set_status_info(
            f"AI 建议 Jev {advice.model or 'latest'} · 桥OK",
            f"切 {rec_tile or chosen.label}  ·  置信 {int((advice.confidence or 0.8) * 100)}%  ·  弃和 {fold_rate}%",
        )
        self.title_bar.set_status("对局中", "active")

        if self.settings.logging_enabled:
            append_decision_log({
                "time": datetime.now(timezone.utc).isoformat(),
                "state": state.to_dict(),
                "advice": advice.to_dict(),
            })

    def on_advice_failure(self, result: tuple[tuple[Any, ...], str]) -> None:
        self.advice_busy = False
        identity, error = result
        if identity != self.pending_identity:
            self._start_latest_if_needed()
            return
        self.advised_identity = identity
        self.bottom_bar.set_status_info("AI 决策未就绪", error.splitlines()[-1])
        self.title_bar.set_status("等待输入", "warning")

    def _start_latest_if_needed(self) -> None:
        if (
            self.last_state is not None
            and self.last_state.identity() == self.pending_identity
            and (self.manual or self.stable_count >= 2)
            and self.pending_identity != self.advised_identity
            and not self.advice_busy
        ):
            self.start_advice(self.last_state)

    def start_advice(self, state: GameState) -> None:
        if self.advice_busy:
            return
        if not self.settings.api_key:
            # Fall back to local rules
            self.advised_identity = state.identity()
            try:
                opts = candidates(state)
                chosen = opts[0]
                advice = Advice(chosen, opts[1:4], source="rules", note="本地规则推算（未填 API Key）")
                self.on_advice((state.identity(), advice, state))
            except Exception as e:
                self.bottom_bar.set_status_info("本地规则推算", str(e))
            return

        self.advice_busy = True
        self.active_advice_identity = state.identity()
        worker = AdviceWorker(state, self.settings.api_key)
        worker.signals.done.connect(self.on_advice)
        worker.signals.failed.connect(self.on_advice_failure)
        self.pool.start(worker)

    def _force_recompute_advice(self) -> None:
        if self.last_state:
            self.advised_identity = None
            self.start_advice(self.last_state)

    def clear_state(self) -> None:
        """Clears current hand and state to fresh."""
        self._current_chosen = None
        empty_state = GameState.from_dict({
            "hand": [],
            "rivers": [[], [], [], []],
            "melds": [[], [], [], []],
            "dora_indicators": [],
            "scores": [25000] * 4,
            "riichi": [False] * 4,
        })
        self.last_state = empty_state
        self._render_state(empty_state)
        self.hero_card.update_decision(
            action_name="待机",
            rec_tile="",
            shanten_num=None,
            confidence=None,
            probabilities=[],
            fold_rate=0,
            danger_score=0.0,
            draw_info="无",
            melds_count=0,
            alternatives=[],
            source_note="已清空 · 点击开始识别或手动核对",
            raw_detail="等待画面采集或手动录入手牌",
        )
        self.bottom_bar.set_status_info("牌面已重置", "等待开始识别")
        self.title_bar.set_status("待机中", "idle")

    def toggle_recognition(self) -> None:
        if self.running:
            self.running = False
            self.timer.stop()
            self.bottom_bar.btn_toggle.setText("开始识别")
            self.title_bar.set_status("已暂停", "idle")
            self.bottom_bar.set_status_info("实时监测已暂停", "点击【开始识别】恢复后台监测")
            return

        if not self.settings.game_box:
            QMessageBox.information(self, "请先选牌桌", "请点击右上角【桌】→【选择牌桌画面】框选雀魂。")
            return

        self.manual = False
        self.running = True
        self.bottom_bar.btn_toggle.setText("暂停识别")
        self.title_bar.set_status("实时监测中", "active")
        self.bottom_bar.set_status_info("实时监测运行中", f"采样间隔 {self.settings.ui_interval_ms}ms · 画面捕获中")
        self.timer.start()
        self.capture_tick()

    def capture_tick(self) -> None:
        if not self.running or self.manual or self.capture_busy or not self.settings.game_box:
            return
        self.capture_busy = True
        worker = CaptureWorker(self.settings.game_box, self.reader, 0)
        worker.signals.captured.connect(self._show_after_capture)
        worker.signals.done.connect(self.on_observation)
        worker.signals.failed.connect(self.on_capture_failure)

        if not self._capture_excluded and self.geometry().intersects(QRect(*self.settings.game_box)):
            self.capture_hidden = True
            self.hide()
            QTimer.singleShot(40, lambda: self.pool.start(worker))
        else:
            self.pool.start(worker)

    def _show_after_capture(self) -> None:
        if self.capture_hidden:
            self.capture_hidden = False
            self.show()

    def on_capture_failure(self, error: str) -> None:
        self._show_after_capture()
        self.capture_busy = False
        self.bottom_bar.set_status_info("画面采集异常", error.splitlines()[-1])
        self.title_bar.set_status("采集异常", "warning")

    def on_observation(self, observation: Observation) -> None:
        self.capture_busy = False
        if not self.running or self.manual:
            return

        self.last_state = observation.state
        if observation.state:
            self._render_state(observation.state)
        else:
            if observation.debug.get("recognized_hand"):
                self.hand_widget.set_hand(observation.debug["recognized_hand"])

        if observation.problems or observation.state is None or observation.confidence < 0.90:
            self.pending_identity = None
            self.stable_count = 0
            self.title_bar.set_status("核对中", "warning")
            self.bottom_bar.set_status_info("识别待核对", "；".join(observation.problems) or "置信度不足")
            return

        identity = observation.state.identity()
        if identity == self.pending_identity:
            self.stable_count += 1
        else:
            self.pending_identity = identity
            self.stable_count = 1
            self.advised_identity = None

        if self.stable_count >= 2 and identity != self.advised_identity and not self.advice_busy:
            self.start_advice(observation.state)

    def _capture_without_overlay(self, box: tuple[int, int, int, int] | None = None):
        was_visible = self.isVisible()
        self.hide()
        QApplication.processEvents()
        time.sleep(0.2)
        try:
            return capture_box(box) if box else capture_desktop()
        finally:
            if was_visible:
                self.show()

    def select_game(self) -> None:
        self.running = False
        self.timer.stop()
        self.bottom_bar.btn_toggle.setText("开始识别")
        image, origin = self._capture_without_overlay()
        dialog = RegionDialog(image, "雀魂浏览器游戏画面", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dialog.selected_original()
        if not chosen:
            return
        x, y, width, height = chosen
        self.settings.game_box = (x + origin[0], y + origin[1], width, height)
        self.settings.save()
        self.bottom_bar.set_status_info("已绑定牌桌", f"{width}×{height} · 自动开启实时监测")
        if not self.running:
            self.toggle_recognition()

    def calibrate(self, full: bool = False) -> None:
        if not self.settings.game_box:
            QMessageBox.information(self, "先选牌桌", "请先框选雀魂游戏画面。")
            return
        self.running = False
        self.timer.stop()
        frame = self._capture_without_overlay(self.settings.game_box)
        regions = dict(self.settings.regions)
        selected_regions = (
            tuple(row for row in REGIONS if not row[4] or row[0] not in regions)
            if full else tuple(row for row in REGIONS if row[4])
        )
        for name, title, _, _, required in selected_regions:
            dialog = RegionDialog(frame, title, optional=not required, parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                if required and name not in regions:
                    QMessageBox.information(self, "校准未完成", f"必需区域 {title} 未框选。")
                    return
                continue
            box = dialog.selected_original()
            if not box:
                continue
            x, y, w, h = box
            regions[name] = (x / frame.shape[1], y / frame.shape[0], w / frame.shape[1], h / frame.shape[0])
        self.settings.regions = regions
        self.settings.save()
        self.reader = VisionReader(regions, self.templates)
        self.bottom_bar.set_status_info("校准已保存", "可继续标注样本或开启实时识别。")

    def label_templates(self) -> None:
        if not self.settings.game_box or not self.settings.regions:
            QMessageBox.information(self, "先校准", "请先选择牌桌并校准区域。")
            return
        frame = self._capture_without_overlay(self.settings.game_box)
        dialog = TemplateDialog(frame, self.settings, self.templates, self)
        dialog.exec()
        self.bottom_bar.set_status_info("样本库已更新", f"当前共有 {len(self.templates.samples)} 个样本。")

    def save_frame(self) -> None:
        if not self.settings.game_box:
            QMessageBox.information(self, "先选牌桌", "请先选择牌桌。")
            return
        frame = self._capture_without_overlay(self.settings.game_box)
        directory = config_dir() / "frames"
        directory.mkdir(parents=True, exist_ok=True)
        filename = datetime.now().strftime("frame-%Y%m%d-%H%M%S-%f.png")
        path = directory / filename
        cv2.imwrite(str(path), frame)
        self.bottom_bar.set_status_info("截图已保存", str(path.name))

    def edit_jev_settings(self) -> None:
        dialog = JevSettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings.api_key = dialog.key.text().strip()
            self.settings.logging_enabled = dialog.log.isChecked()
            self.settings.save()
            self.bottom_bar.set_status_info("Jev 设置已保存", "API Key 已更新")
            self._force_recompute_advice()

    def edit_llm_settings(self) -> None:
        dialog = LLMSettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings.llm_base_url = dialog.base_url.text().strip()
            self.settings.llm_api_key = dialog.key.text().strip()
            self.settings.llm_model = dialog.model.text().strip()
            self.settings.save()
            self.bottom_bar.set_status_info("大模型设置已保存", f"模型：{self.settings.llm_model}")

    def show_llm_analysis(self) -> None:
        """Launches LLM deep tactical analysis dialog."""
        if not self.last_state or not self.last_state.hand:
            QMessageBox.information(self, "暂无局面", "当前没有可供大模型推演的牌面，请先选择牌桌开启识别或点击【手动核对】。")
            return
        if not self.settings.llm_api_key:
            reply = QMessageBox.question(
                self,
                "未配置大模型 API Key",
                "尚未配置通用大模型 API Key。\n是否立即前往【大模型设置】填入？\n（支持 DeepSeek、OpenAI、Claude、Qwen 等兼容接口）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.edit_llm_settings()
            if not self.settings.llm_api_key:
                return

        chosen = self._current_chosen
        if chosen is None:
            try:
                opts = candidates(self.last_state)
                chosen = opts[0]
            except Exception as e:
                QMessageBox.warning(self, "无法解析候选动作", str(e))
                return

        dialog = LLMAnalysisDialog(self.last_state, chosen, self.settings, parent=self)
        dialog.exec()

    def on_hand_tile_clicked(self, tile: str) -> None:
        """Interactive simulation when user clicks a tile in their hand or alternative badges."""
        if not self.last_state or not self.last_state.hand or tile not in self.last_state.hand:
            return
        rest = list(self.last_state.hand)
        rest.remove(tile)
        after = tuple(rest)
        s = shanten(after, self.last_state.open_melds)
        u, imp_tiles = ukeire_details(after, self.last_state)
        d = danger(tile, self.last_state)
        dora = dora_value(tile, self.last_state)

        if imp_tiles:
            preview_str = " ".join(imp_tiles[:6]) + (f" 等{len(imp_tiles)}种" if len(imp_tiles) > 6 else "")
            wait_desc = f"{'待牌' if s == 0 else '进张'} {preview_str} 约 {u} 张"
        else:
            wait_desc = f"有效牌约 {u} 张"

        sim_candidate = Candidate(
            "discard", tile, f"模拟打 {tile}",
            f"{shanten_text(s)} · {wait_desc} · 危险级 {d} · 弃宝牌 {dora} 张 · {yaku_hints(self.last_state)}",
            s, u, d, dora,
            improving_tiles=imp_tiles,
        )
        self._current_chosen = sim_candidate
        self.hand_widget.set_hand(self.last_state.hand, highlight_tile=tile)
        fold_rate, danger_score, _ = self._calculate_metrics(self.last_state, sim_candidate)

        self.hero_card.update_decision(
            action_name="模拟切牌",
            rec_tile=tile,
            shanten_num=s,
            confidence=1.0,
            probabilities=[(tile, 85, True)],
            fold_rate=fold_rate,
            danger_score=danger_score,
            draw_info="手动模拟",
            melds_count=self.last_state.open_melds,
            alternatives=[],
            source_note="手动模拟 · 点击【AI建议】恢复推荐",
            raw_detail=sim_candidate.rationale,
        )
        self.bottom_bar.set_status_info("手动模拟切牌", f"切 {tile}  ·  {shanten_text(s)}  ·  进张约 {u} 张  ·  危险级 {d}")

    def manual_state(self) -> None:
        dialog = ManualStateDialog(self.last_state, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.state is None:
            return
        self.manual = True
        self.running = False
        self.timer.stop()
        self.bottom_bar.btn_toggle.setText("恢复识别")
        self.last_state = dialog.state
        self._render_state(dialog.state)
        self.pending_identity = dialog.state.identity()
        self.stable_count = 2
        self.advised_identity = None
        self.bottom_bar.set_status_info("手动状态", "已加载手动输入的局面")
        self._start_latest_if_needed()

    def closeEvent(self, event: Any) -> None:
        self.timer.stop()
        self.running = False
        self.pool.waitForDone(1000)
        super().closeEvent(event)


HUD_STYLESHEET = """
/* Cyberpunk Glassmorphic Translucent Theme for Mahjong HUD */

QFrame#overlayContainer {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(16, 22, 38, 0.82), stop:1 rgba(10, 14, 25, 0.86));
    border: 1.2px solid rgba(56, 189, 248, 0.35);
    border-radius: 14px;
}

QLabel#appTitle {
    color: #f8fafc;
    font-size: 15px;
    font-weight: 800;
    letter-spacing: 0.5px;
}

QLabel#roundInfoText {
    color: #e2e8f0;
    font-size: 12px;
    font-weight: 700;
}

QPushButton#headerPillBtn {
    color: #cbd5e1;
    background: rgba(30, 41, 59, 0.65);
    border: 1px solid rgba(51, 65, 85, 0.65);
    border-radius: 6px;
    font-weight: bold;
    font-size: 11px;
    padding: 3px 8px;
    min-width: 22px;
}
QPushButton#headerPillBtn:hover {
    color: #ffffff;
    background: rgba(51, 65, 85, 0.85);
    border-color: rgba(100, 116, 139, 0.8);
}

QPushButton#headerCloseBtn {
    color: #94a3b8;
    background: rgba(30, 41, 59, 0.65);
    border: 1px solid rgba(51, 65, 85, 0.65);
    border-radius: 6px;
    font-weight: bold;
    font-size: 13px;
    padding: 2px 7px;
}
QPushButton#headerCloseBtn:hover {
    color: #ffffff;
    background: #e11d48;
    border-color: #f43f5e;
}

/* Four Players Card */
QFrame#tableCard {
    background: rgba(19, 26, 44, 0.68);
    border: 1px solid rgba(32, 43, 66, 0.65);
    border-radius: 8px;
}

QLabel#playerWindBadge {
    color: #e2e8f0;
    font-size: 12px;
    font-weight: bold;
}

QLabel#riichiBadge {
    color: #ffffff;
    background: #dc2626;
    border-radius: 3px;
    font-size: 9px;
    font-weight: bold;
    padding: 1px 3px;
}

QLabel#playerScore {
    color: #f1f5f9;
    font-size: 11px;
    font-weight: bold;
}

QLabel#playerMelds {
    color: #94a3b8;
    font-size: 9px;
}

/* Hand Section */
QLabel#sectionTitle {
    color: #cbd5e1;
    font-size: 11px;
    font-weight: 700;
}

QPushButton#ghostPillBtn {
    color: #94a3b8;
    background: rgba(15, 23, 42, 0.5);
    border: 1px solid rgba(42, 55, 79, 0.65);
    border-radius: 6px;
    font-size: 10px;
    padding: 2px 8px;
}
QPushButton#ghostPillBtn:hover {
    color: #f1f5f9;
    background: rgba(30, 41, 59, 0.7);
    border-color: #475569;
}

QFrame#tilesContainerCard {
    background: rgba(19, 26, 43, 0.65);
    border: 1px solid rgba(33, 44, 68, 0.6);
    border-radius: 8px;
}

/* Hero Decision Card */
QFrame#decisionHeroCard {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(18, 36, 38, 0.82), stop:1 rgba(12, 24, 26, 0.85));
    border: 1.5px solid rgba(16, 185, 129, 0.85);
    border-radius: 10px;
}

QLabel#heroActionBadge {
    color: #064e3b;
    background: #34d399;
    font-size: 11px;
    font-weight: 800;
    border-radius: 6px;
    padding: 3px 7px;
}

QLabel#heroShantenLabel {
    color: #f0fdf4;
    font-size: 18px;
    font-weight: 900;
}

QLabel#heroConfidencePill {
    color: #6ee7b7;
    background: rgba(16, 185, 129, 0.25);
    border: 1px solid #059669;
    border-radius: 8px;
    font-size: 10px;
    font-weight: bold;
    padding: 2px 6px;
}

QLabel#heroDescText {
    color: #93c5fd;
    font-size: 10px;
}

QLabel#dataPill {
    color: #a7f3d0;
    background: rgba(6, 78, 59, 0.6);
    border: 1px solid #047857;
    border-radius: 8px;
    font-size: 10px;
    font-weight: bold;
    padding: 3px 8px;
}

QLabel#heroSubText {
    color: #cbd5e1;
    font-size: 10px;
}

QLabel#heroFootnote {
    color: #64748b;
    font-size: 9px;
}

/* Bottom Action Bar */
QFrame#bottomStatusBox {
    background: rgba(19, 25, 41, 0.65);
    border: 1px solid rgba(30, 41, 59, 0.6);
    border-radius: 6px;
}

QLabel#bottomStatusTitle {
    color: #e2e8f0;
    font-size: 11px;
    font-weight: bold;
}

QLabel#bottomStatusSub {
    color: #38bdf8;
    font-size: 10px;
}

QPushButton#hudGhostBtn {
    color: #cbd5e1;
    background: rgba(30, 41, 59, 0.65);
    border: 1px solid rgba(51, 65, 85, 0.65);
    border-radius: 6px;
    font-size: 11px;
    font-weight: bold;
    padding: 6px 10px;
}
QPushButton#hudGhostBtn:hover {
    color: #ffffff;
    background: rgba(51, 65, 85, 0.85);
    border-color: rgba(100, 116, 139, 0.85);
}

QPushButton#hudPrimaryBtn {
    color: #022c22;
    background: rgba(16, 185, 129, 0.90);
    border: 1px solid #34d399;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 800;
    padding: 7px 14px;
}
QPushButton#hudPrimaryBtn:hover {
    background: #34d399;
    border-color: #6ee7b7;
}

/* Menus */
QMenu {
    background: rgba(19, 26, 44, 0.95);
    color: #f1f5f9;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 20px;
    border-radius: 4px;
}
QMenu::item:selected {
    background: #047857;
    color: #ffffff;
}

/* Unified Cyberpunk Dialog Styling */
QDialog {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0f172a, stop:1 #090d16);
    color: #f1f5f9;
}

QDialog QLabel {
    color: #cbd5e1;
    font-size: 11px;
}

QDialog QLabel#dialogHeader {
    color: #f8fafc;
    font-size: 14px;
    font-weight: 800;
    letter-spacing: 0.5px;
}

QDialog QLabel#dialogSubHeader {
    color: #38bdf8;
    font-size: 11px;
    font-weight: 600;
}

QDialog QLabel#dialogTip {
    color: #94a3b8;
    font-size: 11px;
}

QDialog QLineEdit, QDialog QComboBox, QDialog QSpinBox, QDialog QTextEdit {
    background: rgba(15, 23, 42, 0.9);
    color: #f8fafc;
    border: 1px solid rgba(51, 65, 85, 0.85);
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}

QDialog QLineEdit:focus, QDialog QComboBox:focus, QDialog QSpinBox:focus, QDialog QTextEdit:focus {
    border: 1.5px solid #38bdf8;
    background: rgba(15, 23, 42, 0.98);
}

QDialog QComboBox::drop-down {
    border: none;
    padding-right: 8px;
}

QDialog QComboBox QAbstractItemView {
    background: #0f172a;
    color: #f8fafc;
    selection-background-color: #047857;
    border: 1px solid #334155;
}

QDialog QCheckBox {
    color: #cbd5e1;
    font-size: 11px;
    spacing: 6px;
}

QDialog QCheckBox::indicator {
    width: 16px;
    height: 16px;
    background: rgba(15, 23, 42, 0.85);
    border: 1px solid #475569;
    border-radius: 4px;
}

QDialog QCheckBox::indicator:checked {
    background: #10b981;
    border-color: #34d399;
}

QDialog QTabWidget::pane {
    border: 1px solid rgba(51, 65, 85, 0.7);
    background: rgba(11, 15, 25, 0.7);
    border-radius: 8px;
    padding: 6px;
}

QDialog QTabBar::tab {
    background: rgba(30, 41, 59, 0.6);
    color: #94a3b8;
    border: 1px solid rgba(51, 65, 85, 0.5);
    padding: 6px 16px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-weight: 600;
}

QDialog QTabBar::tab:selected {
    background: rgba(15, 23, 42, 0.95);
    color: #38bdf8;
    border-bottom: 2px solid #38bdf8;
}

QDialog QPushButton {
    color: #cbd5e1;
    background: rgba(30, 41, 59, 0.85);
    border: 1px solid #475569;
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 11px;
    font-weight: 600;
}

QDialog QPushButton:hover {
    color: #ffffff;
    background: #334155;
    border-color: #64748b;
}

QDialog QPushButton:default, QDialog QPushButton[primary="true"] {
    color: #022c22;
    background: #10b981;
    border: 1px solid #34d399;
    font-weight: 800;
}

QDialog QPushButton:default:hover, QDialog QPushButton[primary="true"]:hover {
    background: #34d399;
    border-color: #6ee7b7;
}

QDialog QScrollArea {
    background: transparent;
    border: 1px solid rgba(51, 65, 85, 0.5);
    border-radius: 6px;
}
"""


def setup_chinese_font(app: QApplication) -> str:
    """Detects and loads high-quality Chinese system fonts."""
    font_candidates = (
        [Path("/System/Library/Fonts/PingFang.ttc")]
        if sys.platform == "darwin" else
        [
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/msyhbd.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
            Path("C:/Windows/Fonts/simsun.ttc"),
        ]
    )
    family = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei"
    for path in font_candidates:
        if path.exists():
            font_id = QFontDatabase.addApplicationFont(str(path))
            families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
            if families:
                family = families[0]
                break
    font = QFont(family, 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)
    return family


def main() -> None:
    app = QApplication(sys.argv)
    setup_chinese_font(app)
    app.setStyleSheet(HUD_STYLESHEET)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
