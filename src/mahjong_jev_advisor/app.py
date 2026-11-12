"""Cross-platform Cyberpunk HUD advisor window for Mahjong Soul & Jev."""

from __future__ import annotations

import ctypes
import sys
import time
import traceback
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .resources import extension_dir
from typing import Any
import os

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QFont, QFontDatabase, QIcon, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QLayout, QMainWindow, QMenu, QMessageBox,
    QScrollArea, QVBoxLayout, QWidget,
)

from .edition import launch_edition
from .jev import JevError, advise
from .hook import HookBridge
from .rules import (
    NoDecision, UncertainState, candidates, danger_level, discard_candidate,
    discard_comparison, shanten_text,
)
from .settings import Settings, append_decision_log
from .state import Advice, Candidate, GameState
from .steam_capture import SteamCaptureBridge
from .ui_components import (
    BottomActionBar, FourPlayersRiverWidget, HandWidget, HUDTitleBar,
    DecisionHeroCard, RoundInfoBar,
)
from .ui_dialogs import (
    DiscardComparisonDialog, JevSettingsDialog, LLMAnalysisDialog,
    LLMSettingsDialog, ManualStateDialog, SteamConnectionDialog,
)


class AdvisorScrollArea(QScrollArea):
    """Forward only Ctrl-wheel input, never general Qt teardown events."""
    opacity_wheel = Signal(object)

    def wheelEvent(self, event: Any) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.opacity_wheel.emit(event)
        else:
            super().wheelEvent(event)


def fit_window_geometry(saved: QRect, available: QRect) -> QRect:
    """Keep the client rectangle inside a usable screen, reserving caption space."""
    bounds = available.adjusted(8, 32, -8, -8)
    width = min(max(320, saved.width()), bounds.width())
    height = min(max(320, saved.height()), bounds.height())
    return QRect(max(bounds.left(), min(saved.x(), bounds.right() - width + 1)),
                 max(bounds.top(), min(saved.y(), bounds.bottom() - height + 1)), width, height)


def capture_window_rect(window: QWidget) -> QRect:
    """Match capture coordinates, including native Windows DPI and window chrome."""
    if sys.platform == "win32":
        from ctypes.wintypes import RECT
        native_rect = RECT()
        get_rect = ctypes.windll.user32.GetWindowRect
        get_rect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
        get_rect.restype = ctypes.c_int
        if get_rect(int(window.winId()), ctypes.byref(native_rect)):
            return QRect(native_rect.left, native_rect.top,
                         native_rect.right - native_rect.left, native_rect.bottom - native_rect.top)
    return window.frameGeometry()


def overlay_size_for_screen(available: QRect) -> tuple[int, int]:
    """Return a usable logical-pixel HUD size for the current screen."""
    return min(390, max(320, available.width() - 24)), min(720, max(320, available.height() - 64))


def exclude_window_from_capture(hwnd: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        setter = ctypes.windll.user32.SetWindowDisplayAffinity
        setter.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        setter.restype = ctypes.c_int
        return bool(setter(hwnd, 0x11))
    except (AttributeError, OSError, ValueError):
        return False


class TaskSignals(QObject):
    captured = Signal()
    done = Signal(object)
    failed = Signal(object)


class AdviceWorker(QRunnable):
    def __init__(self, state: GameState, key: str, connection=None):
        super().__init__()
        self.state, self.key = state, key
        self.connection = connection
        self.signals = TaskSignals()

    def run(self) -> None:
        try:
            start = time.perf_counter()
            options = candidates(self.state)
            advice = advise(self.state, options, self.key, connection=self.connection)
            elapsed = int((time.perf_counter() - start) * 1000)
            object.__setattr__(advice, "latency_ms", elapsed)
            self.signals.done.emit((self.state.identity(), advice, self.state))
        except (NoDecision, UncertainState) as error:
            self.signals.failed.emit((self.state.identity(), str(error)))
        except JevError as error:
            self.signals.failed.emit((self.state.identity(), str(error)))
        except Exception:
            self.signals.failed.emit((self.state.identity(), traceback.format_exc(limit=3)))


def _candidate_display_label(candidate: Candidate) -> str:
    return candidate.tile if candidate.action == "discard" and candidate.tile else candidate.label


def _demo_reference_state() -> GameState:
    """Provides the exact match state shown in the reference image for instant preview."""
    return GameState.from_dict({
        "hand": "4m4m1p1p4p4p4p2s2s2s7s8s9s1p",
        "rivers": [
            ["3m", "8m", "1s", "3z", "2m", "4m", "6s", "2m", "1m", "5p", "9s", "3p"],
            ["5z", "8m", "3m", "7z", "2p", "3s", "8p", "6m", "1p", "4s", "2s"],
            ["7p", "1z", "4z", "3z", "1m", "3m", "3p", "4z", "6p", "9m", "5s"],
            ["1z", "2z", "2p", "9s", "1z", "4s", "7p", "5p", "8s", "6z"],
        ],
        "melds": [[], [], ["3m", "4m", "5m"], []],
        "dora_indicators": ["7p"],
        "buttons": ["riichi"],
        "seat": 0,
        "round_wind": "E",
        "round_number": 1,
        "seat_wind": "E",
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
        edition_label = {"browser": " · 网页版", "steam": " · Steam 版"}.get(launch_edition(), "")
        self.setWindowTitle("雀魂 · Jev 实时切牌顾问" + edition_label)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)

        self.settings = Settings.load()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.settings.always_on_top)
        self._comparison_dialog = None
        self._comparison_state_available = False
        edition = launch_edition()
        if edition:
            self.settings.switch_input_source(edition)
        self._capture_excluded = False
        self._capture_generation = 0
        self.hook = HookBridge(proxy_port=self.settings.steam_proxy_port)
        self.hook.signals.state.connect(self.on_hook_state)
        self.hook.signals.status.connect(self.on_hook_status)
        self.steam = SteamCaptureBridge(
            mode=self.settings.steam_capture_mode,
            process_name=self.settings.steam_process_name,
            proxy_port=self.settings.steam_proxy_port,
            upstream_proxy=self.settings.steam_upstream_proxy,
        )
        self.steam.signals.state.connect(self.on_steam_state)
        self.steam.signals.status.connect(self.on_steam_status)
        self.steam.signals.unavailable.connect(self.on_steam_unavailable)
        self.pool = QThreadPool.globalInstance()

        self.capture_busy = False
        self.capture_hidden = False
        self.advice_busy = False
        self.running = False
        self.manual = False
        self.last_state: GameState | None = None
        self.pending_identity: tuple[Any, ...] | None = None
        self._explicit_request_identity: tuple[Any, ...] | None = None
        self.stable_count = 0
        self.advised_identity: tuple[Any, ...] | None = None
        self.active_advice_identity: tuple[Any, ...] | None = None
        self._current_chosen: Candidate | None = None
        self._simulation_identity: tuple[Any, ...] | None = None
        self._rendered_identity: tuple[Any, ...] | None = None
        self._demo_preview = False
        self._screen_bound = False
        self._connection_revision = 0
        self._active_connection_revision = 0
        self._table_user_override = False
        self._hook_active = False

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

        # Keep controls visible while dense table and decision details scroll on
        # shorter screens (including 720p and scaled laptop displays).
        self.content_scroll = AdvisorScrollArea(self)
        self.content_scroll.setObjectName("hudContentScroll")
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_scroll.opacity_wheel.connect(self.wheelEvent)
        content = QWidget()
        content.setObjectName("hudScrollableContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(6)
        content_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.content_scroll.setWidget(content)
        root_layout.addWidget(self.content_scroll, 1)

        # 2. Round Info Bar (东1局 · 余 29 · 3北 · 宝牌)
        self.round_bar = RoundInfoBar(self)
        content_layout.addWidget(self.round_bar)

        # 3. Four-player status card with rivers
        self.table_card = FourPlayersRiverWidget(self)
        self.table_card.setVisible(self.settings.show_table)
        content_layout.addWidget(self.table_card)

        # 4. Hand & Draw Strip with Glowing Highlight
        self.hand_widget = HandWidget(self)
        self.hand_widget.toggle_table.connect(self.toggle_table_visibility)
        self.hand_widget.tile_clicked.connect(self.on_hand_tile_clicked)
        content_layout.addWidget(self.hand_widget)

        # 5. Hero Decision Card (Probability bars, recommendation tile, pills)
        self.hero_card = DecisionHeroCard(self)
        self.hero_card.alternative_clicked.connect(self.on_hand_tile_clicked)
        content_layout.addWidget(self.hero_card)
        content_layout.addStretch()

        # 6. Bottom Action & Status Bar
        self.bottom_bar = BottomActionBar(self)
        self.bottom_bar.ai_suggest_clicked.connect(self._force_recompute_advice)
        self.bottom_bar.llm_analysis_clicked.connect(self.show_llm_analysis)
        self.bottom_bar.clear_clicked.connect(self.clear_state)
        self.bottom_bar.jev_settings_clicked.connect(self.edit_jev_settings)
        self.bottom_bar.llm_settings_clicked.connect(self.edit_llm_settings)
        self.bottom_bar.toggle_clicked.connect(self.toggle_recognition)
        self.bottom_bar.manual_clicked.connect(self.manual_state)
        self.bottom_bar.compare_clicked.connect(self.show_discard_comparison)
        root_layout.addWidget(self.bottom_bar)
        self.compare_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self.compare_shortcut.activated.connect(self.show_discard_comparison)
        self.recompute_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.recompute_shortcut.activated.connect(self._force_recompute_advice)

        self.setMinimumSize(320, 320)
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is not None:
            self._adapt_to_screen(screen)
            if self.settings.window_geometry:
                saved = QRect(*self.settings.window_geometry)
                target = QApplication.screenAt(saved.center()) or screen
                self.setGeometry(fit_window_geometry(saved, target.availableGeometry()))
        else:
            self.resize(390, 720)
        self.setWindowOpacity(self.settings.opacity)

        self.timer = QTimer(self)
        self.timer.setInterval(self.settings.ui_interval_ms)

        self.clear_state()
        # The HTTP server also serves Steam's PAC URL; browser events remain
        # isolated by on_hook_state/on_hook_status when Steam is selected.
        self.hook.start()
        if self.settings.input_source == "browser":
            self.title_bar.set_status("等待连接", "idle")
            self.bottom_bar.set_status_info("网页 Hook 监听中", "打开或刷新雀魂网页即可自动捕获实时牌局")
        else:
            self.title_bar.set_status("Steam 就绪", "idle")
            self.bottom_bar.set_status_info(
                "Steam 实时协议模式",
                "点击【开始识别】启动毫秒级协议直连；首次使用可在【桌】安装本地证书",
            )
            if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
                generation = self._capture_generation
                QTimer.singleShot(
                    200,
                    lambda: self.toggle_recognition()
                    if self.settings.input_source == "steam" and not self.running and generation == self._capture_generation
                    else None,
                )
        if self.settings.load_warning:
            self.title_bar.set_status("配置待修复", "warning")
            self.bottom_bar.set_status_info("配置读取异常", self.settings.load_warning)

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
        self._demo_preview = True
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
            danger_label="注意",
            danger_key="watch",
            threat_note="立直 1 家",
            draw_info="摸切 6",
            melds_count=2,
            alternatives=["2s", "4p"],
            source_note="界面示例 · 非实时 Jev 输出",
            raw_detail="2向听 · 有效牌约 24 张 · 危险级 1 · 役牌候选、门清",
        )
        self.hero_card.stats_line_1.setText("示例选择比例 0.83 · 示例置信度 0.78")
        self.hero_card.stats_line_2.setText("示例危险档位 · 非真实牌局测量")
        self.hand_widget.set_hand(demo.hand, highlight_tile="1p")
        self.bottom_bar.set_status_info(
            "示范牌局 · 非实时",
            "示例牌面与概率，仅用于预览界面",
        )
        self.title_bar.set_status("示范中", "idle")

    def toggle_table_visibility(self) -> None:
        self._table_user_override = True
        new_visible = not self.table_card.isVisible()
        self.table_card.setVisible(new_visible)
        self.settings.show_table = new_visible
        self.settings.save()
        self.hand_widget.btn_toggle.setText("展开四家" if not new_visible else "隐藏四家")

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if not self._screen_bound and self.windowHandle() is not None:
            self.windowHandle().screenChanged.connect(self._on_screen_changed)
            self._screen_bound = True
        if not self._capture_excluded:
            self._capture_excluded = exclude_window_from_capture(int(self.winId()))

    def _adapt_to_screen(self, screen: Any) -> None:
        available = screen.availableGeometry()
        self.resize(*overlay_size_for_screen(available))
        compact = available.height() < 760
        self.content_scroll.widget().layout().setSpacing(4 if compact else 6)
        self.hero_card.layout().setSpacing(4 if compact else 8)
        self.hero_card.layout().setContentsMargins(10 if compact else 12, 8 if compact else 10,
                                                  10 if compact else 12, 8 if compact else 10)
        if not self._table_user_override:
            show_table = self.settings.show_table and not compact
            self.table_card.setVisible(show_table)
            self.hand_widget.btn_toggle.setText("隐藏四家" if show_table else "展开四家")
        if self.isVisible():
            x = max(available.left(), min(self.x(), available.right() - self.width() + 1))
            y = max(available.top(), min(self.y(), available.bottom() - self.height() + 1))
            self.move(x, y)

    def _on_screen_changed(self, screen: Any) -> None:
        # Never reposition or resize during a system drag; that makes the window jump.
        pass

    def _on_ai_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("立即重新获取建议", self._force_recompute_advice)
        menu.addAction("比较所有切法（本地）", self.show_discard_comparison)
        menu.addAction("大模型深度牌理解析 (LLM)", self.show_llm_analysis)
        menu.addAction("Jev 设置 (API Key)", self.edit_jev_settings)
        menu.addAction("大模型设置 (LLM)", self.edit_llm_settings)
        menu.addSeparator()
        menu.addAction("载入示范对局状态", self._load_initial_preview)
        menu.exec(self.title_bar.btn_ai.mapToGlobal(QPoint(0, self.title_bar.btn_ai.height() + 2)))

    def _on_table_menu(self) -> None:
        menu = QMenu(self)
        edition = launch_edition()
        if not edition:
            source_menu = menu.addMenu("输入来源")
            for source, label in (
                ("browser", "浏览器（网页 Hook 实时协议）"),
                ("steam", "Steam 客户端（实时协议直连）"),
            ):
                action = source_menu.addAction(label)
                action.setCheckable(True)
                action.setChecked(self.settings.input_source == source)
                action.triggered.connect(lambda checked=False, value=source: self.set_input_source(value))
        else:
            menu.addAction("网页版" if edition == "browser" else "Steam 版（实时协议）").setEnabled(False)
        if edition != "browser":
            menu.addAction("Steam 实时连接与证书设置...", self.edit_steam_settings)
            menu.addAction("查看 Steam 协议实时诊断", self.show_steam_diagnostic)
            menu.addAction("重启 Steam 捕获引擎", self.restart_steam_capture)
        menu.addSeparator()
        if edition != "steam":
            menu.addAction("打开网页 Hook 扩展目录", self.open_hook_extension)
            menu.addAction("查看网页 Hook 诊断", self.show_hook_diagnostic)
        menu.addSeparator()
        pin = menu.addAction("窗口置顶")
        pin.setCheckable(True)
        pin.setChecked(self.settings.always_on_top)
        pin.triggered.connect(self.set_always_on_top)
        menu.addAction("恢复窗口大小与位置", self.reset_window_layout)
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

    def set_always_on_top(self, enabled: bool) -> None:
        self.settings.always_on_top = enabled
        self.settings.save()
        geometry = self.geometry()
        visible = self.isVisible()
        state = self.windowState()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self._capture_excluded = False
        self.setGeometry(geometry)
        self.setWindowState(state)
        if visible:
            self.show()
        self.bottom_bar.set_status_info("窗口已置顶" if enabled else "已取消置顶", "可在【桌】菜单切换")

    def reset_window_layout(self) -> None:
        screen = QApplication.screenAt(QCursor.pos()) or self.screen()
        self.showNormal()
        self._adapt_to_screen(screen)
        available = screen.availableGeometry()
        self.setGeometry(fit_window_geometry(QRect(available.left() + 24, available.top() + 48,
                                                   self.width(), self.height()), available))
        self.settings.window_geometry = self.geometry().getRect()
        self.settings.save()
        self.bottom_bar.set_status_info("窗口已恢复", "已移回当前屏幕，位置与大小会在关闭时保存")

    def set_input_source(self, source: str) -> None:
        if launch_edition() and source != launch_edition():
            return
        if source == self.settings.input_source:
            return
        self.running = False
        self.manual = False
        self.timer.stop()
        self._hook_active = False
        self._capture_generation += 1
        self._connection_revision += 1  # Invalidate advice from the previous input.
        self.settings.switch_input_source(source)
        self.settings.save()
        self.clear_state()
        self.bottom_bar.btn_toggle.setText("开始识别")
        if source == "steam":
            self.hook.start()
            self.bottom_bar.set_status_info(
                "Steam 实时协议模式",
                "点击【开始识别】启动毫秒级协议直连；首次使用可在【桌】安装本地证书",
            )
        else:
            self.steam.stop()
            self.hook.start()
            self.bottom_bar.set_status_info("浏览器模式", "刷新雀魂网页以建立网页 Hook 实时连接")

    def edit_steam_settings(self) -> None:
        self.steam.refresh_system_diagnostic()
        dialog = SteamConnectionDialog(self.settings, self.steam.diagnostic_snapshot(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings.steam_capture_mode = dialog.mode_combo.currentData()
            self.settings.steam_process_name = dialog.process_name.text().strip() or "Jantama_MahjongSoul"
            self.settings.steam_proxy_port = int(dialog.proxy_port.value())
            self.settings.steam_upstream_proxy = dialog.upstream_proxy.text().strip()
            self.settings.save()
            self.hook.proxy_port = self.settings.steam_proxy_port
            self.steam.configure(
                mode=self.settings.steam_capture_mode,
                process_name=self.settings.steam_process_name,
                proxy_port=self.settings.steam_proxy_port,
                upstream_proxy=self.settings.steam_upstream_proxy,
            )
            self.bottom_bar.set_status_info(
                "Steam 连接配置已保存",
                f"模式：{self.settings.steam_capture_mode} · 进程：{self.settings.steam_process_name} · 端口：{self.settings.steam_proxy_port}",
            )

    def restart_steam_capture(self) -> None:
        self.steam.stop()
        self.set_input_source("steam")
        if self.steam.start():
            self._hook_active = self.steam.live_ready
            self._begin_realtime("Steam 捕获引擎已重启 · 正在监听雀魂对局协议")
        else:
            self.bottom_bar.set_status_info("Steam 捕获启动失败", self.steam.last_error or "请检查【桌】-> Steam 实时连接设置")

    def show_steam_diagnostic(self) -> None:
        self.steam.refresh_system_diagnostic()
        snap = self.steam.diagnostic_snapshot()
        procs = snap.get("detected_processes") or []
        proc_desc = (
            "，".join(f"{p['name']}(PID {p['pid']})" for p in procs)
            if procs
            else "未检测到运行中的雀魂进程"
        )
        mode_desc = (
            f"进程直连 ({snap.get('active_mode_spec') or ('local:' + snap['process_name'])})"
            if snap["mode"] == "process" and not snap["fallback_active"]
            else f"本地代理 (127.0.0.1:{snap['proxy_port']})"
        )
        lines = [
            f"运行状态：{'监听中' if snap['running'] else '未启动'}",
            f"当前模式：{mode_desc}",
            f"本地 CA 证书：{'已安装并信任' if snap['cert_installed'] else '未安装（请在【桌】一键安装）'}",
            f"运行中雀魂进程：{proc_desc}",
            f"活跃 WebSocket：{snap['ws_connections']} 个",
            f"已捕获协议帧：{snap['frames_seen']}",
            f"已解码消息数：{self.steam.protocol.messages_seen}",
            f"已重建牌局状态：{snap['states_seen']}",
            f"解码错误：{snap['decode_errors']}",
            f"最近协议消息：{snap['last_message_name'] or '无'}",
            f"最近对局动作：{snap['last_action_name'] or '无'}",
            f"连接主机：{snap['last_host'] or '等待连接'}",
            f"系统 PAC 地址：http://127.0.0.1:{ self.hook.port }/proxy.pac",
        ]
        if snap.get("last_error"):
            lines.append(f"最近提示：{snap['last_error']}")
        QMessageBox.information(self, "Steam 协议实时诊断", "\n".join(lines))

    def open_hook_extension(self) -> None:
        directory = extension_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        self.bottom_bar.set_status_info("网页 Hook 扩展目录已打开", "在浏览器扩展页选择“加载解压缩的扩展”")

    def show_hook_diagnostic(self) -> None:
        info = self.hook.browser_diagnostic
        if not info:
            detail = "尚未收到浏览器诊断。请重载扩展，再刷新雀魂页面。"
        else:
            browser_frames = int(info.get("sentFrames", 0) or 0) + int(info.get("receivedFrames", 0) or 0)
            detail = "\n".join([
                f"扩展版本：{info.get('extensionVersion') or '未知'}",
                f"页面 Hook：{'已注入' if info.get('hookReady') else '未注入'}",
                f"雀魂 WebSocket：{int(info.get('socketCount', 0) or 0)} 个",
                f"浏览器捕获帧：{browser_frames}",
                f"桌面端接收帧：{self.hook.frames_seen}",
                f"协议消息：{self.hook.protocol.messages_seen}",
                f"已重建牌局状态：{self.hook.states_seen}",
                f"解码错误：{self.hook.decode_errors}",
                f"最近事件：{self.hook.protocol.last_message_name or '无'}",
                f"连接主机：{info.get('lastSocketHost') or '尚未建立'}",
            ])
            if self.hook.last_error:
                detail += f"\n最近错误：{self.hook.last_error}"
        QMessageBox.information(self, "网页 Hook 诊断", detail)

    def _clear_current_decision(self) -> None:
        self._current_chosen = None
        self._simulation_identity = None
        for slot in (*self.hand_widget.hand_slots, self.hand_widget.draw_slot):
            slot.set_glowing(False)

    def _render_state(self, state: GameState, highlight_tile: str | None = None) -> None:
        identity = state.identity()
        if identity != self._rendered_identity:
            self._clear_current_decision()
            if self._explicit_request_identity != identity:
                self._explicit_request_identity = None
        elif highlight_tile is None and self._current_chosen is not None:
            highlight_tile = self._current_chosen.tile
        self._rendered_identity = identity
        self._comparison_state_available = True
        if self._comparison_dialog is not None:
            self._comparison_dialog.mark_current_state(state)
        # Hand & drawn tile
        self.hand_widget.set_hand(state.hand, highlight_tile=highlight_tile)

        # Round & Dora info
        dora = state.dora_indicators[0] if state.dora_indicators else ""
        seat_desc = f"{state.player_count}人 · {dict(E='东', S='南', W='西', N='北').get(state.seat_wind, '?')}家"
        self.round_bar.set_round_info(
            round_wind=state.round_wind,
            round_num=state.round_number,
            # GameState does not track the live wall. Rivers and concealed
            # hand length cannot reconstruct it after calls or replacement draws.
            remaining_tiles=None,
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
            seat_wind=state.seat_wind,
        )

    @staticmethod
    def _danger_metrics(state: GameState, chosen: Candidate) -> tuple[str, str, str]:
        """Describe observed threats without computing unrelated discard options."""
        opponents = [r for i, r in enumerate(state.riichi) if i != state.seat]
        threats = sum(r is True for r in opponents)
        unknown = sum(r is None for r in opponents)
        if unknown:
            danger_label, danger_key = danger_level(None)
            threat_note = f"立直 {threats} 家 · {unknown} 家未知" if threats else "立直状态未知"
        elif threats:
            danger_label, danger_key = danger_level(chosen.danger)
            threat_note = f"立直 {threats} 家"
        else:
            danger_label, danger_key = "未评估", "unknown"
            threat_note = "未见对手立直"
        return danger_label, danger_key, threat_note

    def _calculate_metrics(self, state: GameState, chosen: Candidate) -> tuple[str, str, str, list[tuple[str, int, bool]], list[Candidate]]:
        """Banded danger and local candidate scores, never deal-in probabilities."""
        danger_label, danger_key, threat_note = self._danger_metrics(state, chosen)
        threats = sum(r is True for i, r in enumerate(state.riichi) if i != state.seat)

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

            for i, opt in enumerate(opts):
                lbl = _candidate_display_label(opt)
                pct = max(3, raw_pcts[i])
                bar_items.append((lbl, pct, opt == chosen))
            bar_items.sort(key=lambda x: -x[1])
            bar_items = bar_items[:4]
            allocated = sum(x[1] for x in bar_items)
            rem = max(2, 100 - allocated)
            if threats > 0:
                bar_items.append(("防守", rem, False))
            else:
                bar_items.append(("等待", rem, False))
        else:
            bar_items = [(_candidate_display_label(chosen), 85, True)]

        return danger_label, danger_key, threat_note, bar_items, opts

    def on_advice(self, result: tuple[tuple[Any, ...], Advice, GameState]) -> None:
        self.advice_busy = False
        identity, advice, state = result
        if identity != self.pending_identity or self._active_connection_revision != self._connection_revision:
            self._start_latest_if_needed()
            return

        self.advised_identity = identity
        self._explicit_request_identity = None
        if self.settings.logging_enabled:
            append_decision_log({
                "time": datetime.now(timezone.utc).isoformat(),
                "state": state.to_dict(),
                "advice": advice.to_dict(),
            })
        if self._simulation_identity == identity:
            return
        chosen = advice.selected
        rec_tile = chosen.tile or ""

        # Update hand tile highlight
        self._render_state(state, highlight_tile=rec_tile)
        self._current_chosen = chosen

        # Calculate banded danger and candidate bars
        danger_label, danger_key, threat_note, bar_items, opts = self._calculate_metrics(state, chosen)
        if advice.source == "jev":
            bar_items = []

        # If Jev returned explicit probabilities, merge them
        if advice.source == "jev" and advice.probabilities:
            try:
                mapped = []
                for i, opt in enumerate(opts):
                    key = f"a{i}"
                    lbl = _candidate_display_label(opt)
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

        alternatives_list = [_candidate_display_label(alt) for alt in advice.alternatives[:3]]

        source_note = (
            f"在线模型 {advice.model or 'latest'} · {advice.latency_ms}ms"
            if advice.source == "jev"
            else "Jev 故障回退 · 本地规则" if advice.note.startswith("Jev 不可用")
            else "确定性规则"
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
            confidence=advice.confidence,
            probabilities=bar_items,
            danger_label=danger_label,
            danger_key=danger_key,
            threat_note=threat_note,
            draw_info=f"有效牌 {chosen.ukeire} 张" if chosen.ukeire is not None else "有效牌未知",
            melds_count=state.open_melds,
            alternatives=alternatives_list,
            source_note=source_note,
            raw_detail=chosen.rationale,
            yaku_badge=yaku_badge,
            probability_label="Jev 选择概率" if advice.source == "jev" else "规则相对评分",
            decision_available=True,
        )

        status_title = (
            f"AI 建议 Jev {advice.model or 'latest'}"
            if advice.source == "jev" else source_note
        )
        confidence_summary = f"置信 {advice.confidence:.0%}" if advice.confidence is not None else "未提供置信度"
        self.bottom_bar.set_status_info(
            status_title,
            f"{chosen.label}  ·  {confidence_summary}  ·  {threat_note}  ·  危险 {danger_label}",
        )
        self.title_bar.set_status("对局中", "active")

    def on_advice_failure(self, result: tuple[tuple[Any, ...], str]) -> None:
        self.advice_busy = False
        identity, error = result
        if identity != self.pending_identity or self._active_connection_revision != self._connection_revision:
            self._start_latest_if_needed()
            return
        self.advised_identity = identity
        self._explicit_request_identity = None
        if self._simulation_identity == identity:
            return
        self._clear_current_decision()
        self.hero_card.update_decision(
            action_name="决策未就绪", rec_tile="", shanten_num=None, confidence=None,
            probabilities=[], draw_info="—",
            melds_count=self.last_state.open_melds if self.last_state else 0,
            alternatives=[], source_note="在线模型请求失败",
            raw_detail=error.splitlines()[-1],
        )
        self.bottom_bar.set_status_info("AI 决策未就绪", error.splitlines()[-1])
        self.title_bar.set_status("等待输入", "warning")

    def _start_latest_if_needed(self) -> None:
        if (
            self.last_state is not None
            and self.last_state.identity() == self.pending_identity
            and (self.manual or self.stable_count >= 2
                 or self._explicit_request_identity == self.pending_identity)
            and self.pending_identity != self.advised_identity
            and not self.advice_busy
        ):
            self.start_advice(self.last_state)

    def start_advice(self, state: GameState) -> None:
        if self.advice_busy:
            return
        self._explicit_request_identity = None
        if not self.settings.api_key:
            self.advised_identity = state.identity()
            if self._simulation_identity == state.identity():
                return
            self._clear_current_decision()
            self.hero_card.update_decision(
                action_name="待连接", rec_tile="", shanten_num=None, confidence=None,
                probabilities=[], draw_info="—",
                melds_count=state.open_melds, alternatives=[],
                source_note="等待 API Key",
                raw_detail="牌局状态已准备好。请在模型设置填写 API Key 和请求地址，并测试连接。",
            )
            self.bottom_bar.set_status_info("Jev 待连接", "填写 API Key 和请求地址后测试连接")
            self.title_bar.set_status("待连接", "warning")
            return

        self.advice_busy = True
        if self._simulation_identity != state.identity():
            self._show_advice_pending()
        self._active_connection_revision = self._connection_revision
        self.active_advice_identity = state.identity()
        worker = AdviceWorker(state, self.settings.api_key, self.settings.connection())
        worker.signals.done.connect(self.on_advice)
        worker.signals.failed.connect(self.on_advice_failure)
        self.pool.start(worker)

    def _show_advice_pending(self) -> None:
        self._clear_current_decision()
        self.hero_card.update_decision(action_name="请求中", rec_tile="", shanten_num=None,
            confidence=None, probabilities=[], alternatives=[], source_note="等待真实模型响应",
            raw_detail=f"模型：{self.settings.model_name}；单次超时 {self.settings.model_timeout:g} 秒，临时故障最多自动重试 2 次")
        self.bottom_bar.set_status_info("AI 建议请求中", "正在等待当前局面的建议")

    def _force_recompute_advice(self) -> None:
        if self._demo_preview:
            self.bottom_bar.set_status_info("等待真实牌局", "请先识别牌桌或手动核对，再请求 Jev 建议")
            return
        if self.last_state and self.last_state.hand:
            if not self._comparison_state_available:
                self.bottom_bar.set_status_info("暂时无法请求建议", "当前画面不可用，请等待识别恢复或手动核对牌面。")
                return
            self._simulation_identity = None
            self.advised_identity = None
            self.pending_identity = self.last_state.identity()
            self._explicit_request_identity = self.pending_identity
            if self.advice_busy:
                self._show_advice_pending()
                return
            self.start_advice(self.last_state)
        else:
            self.bottom_bar.set_status_info("等待真实牌局", "可在模型设置测试连接；实时建议需识别牌桌或手动核对牌面")

    def clear_state(self) -> None:
        """Clears current hand and state to fresh."""
        self.pending_identity = None
        self._explicit_request_identity = None
        self.advised_identity = None
        self.stable_count = 0
        self._demo_preview = False
        self._clear_current_decision()
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
        self.round_bar.round_text.setText("等待识别局况")
        for row in self.table_card.rows:
            row.score_label.setText("—")
        self.hero_card.update_decision(
            action_name="待机",
            rec_tile="",
            shanten_num=None,
            confidence=None,
            probabilities=[],
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
            self._capture_generation += 1
            self.pending_identity = None
            self._explicit_request_identity = None
            self.advised_identity = None
            self.stable_count = 0
            self._clear_current_decision()
            self.hero_card.update_decision(action_name="已暂停", rec_tile="", shanten_num=None,
                confidence=None, probabilities=[], alternatives=[], source_note="实时建议已暂停",
                raw_detail="点击【开始识别】恢复；暂停前的请求结果不会显示。")
            self.running = False
            self.timer.stop()
            self.bottom_bar.btn_toggle.setText("开始识别")
            self.title_bar.set_status("已暂停", "idle")
            self.bottom_bar.set_status_info("实时监测已暂停", "点击【开始识别】恢复后台监测")
            return

        if self._demo_preview:
            self.clear_state()

        if self.settings.input_source == "steam":
            if not self.steam.running and not self.steam.start():
                self.on_steam_unavailable(self.steam.last_error or "Steam 捕获启动失败")
                return
            self._hook_active = self.steam.live_ready
            self._begin_realtime("Steam 实时协议监听中 · 牌局变化毫秒级自动请求 Jev")
        else:
            if self.hook._server is None:
                self.hook.start()
            self._hook_active = self.hook.live_ready
            self._begin_realtime("网页 Hook 实时协议监听中 · 牌局变化毫秒级自动请求 Jev")

        if self.last_state and self.last_state.hand and self._comparison_state_available:
            self.pending_identity = self.last_state.identity()
            self.stable_count = 2
            if self.pending_identity != self.advised_identity and not self.advice_busy:
                self.start_advice(self.last_state)

    def _begin_realtime(self, detail: str | None = None) -> None:
        self.manual = False
        self.running = True
        self.timer.stop()
        self.bottom_bar.btn_toggle.setText("暂停识别")
        self.title_bar.set_status("实时监测中", "active")
        self.bottom_bar.set_status_info(
            "实时监测运行中", detail or "实时协议监听中 · 牌局变化后毫秒级自动请求 Jev"
        )

    def on_hook_status(self, message: str) -> None:
        if self.settings.input_source == "steam":
            return
        if message == "网页 Hook 协议已就绪":
            if not self.running:
                self._begin_realtime("网页 Hook 已注入 · 等待真实牌局事件")
            else:
                self.bottom_bar.set_status_info(message, "正在等待雀魂牌局事件")
        elif not self.running:
            self.title_bar.set_status("Hook 就绪", "active")
            self.bottom_bar.set_status_info(message, "已连接到浏览器扩展")
        else:
            self.bottom_bar.set_status_info(message, "网页 Hook 实时诊断")

    def on_steam_unavailable(self, message: str) -> None:
        if self.settings.input_source != "steam":
            return
        self._hook_active = False
        self._connection_revision += 1
        self.on_capture_failure(message)

    def on_steam_status(self, message: str) -> None:
        if self.settings.input_source != "steam":
            return
        if message.startswith("error:") or "通道异常" in message:
            self._hook_active = False
            self.on_capture_failure(message)
            return
        if message == "connected" or "协议已就绪" in message or "已捕获" in message:
            self._hook_active = True
        elif message == "disconnected":
            self.on_steam_unavailable("Steam 实时连接已断开 · 等待重新同步牌局")
            return
        if not self.running:
            self.title_bar.set_status("Steam 就绪", "active")
            self.bottom_bar.set_status_info(message, "Steam 实时协议通道已准备")
        else:
            self.bottom_bar.set_status_info(message, "Steam 实时协议监听中")

    def on_hook_state(self, state: GameState) -> None:
        if self.settings.input_source == "steam":
            return
        self._handle_protocol_state(state)

    def on_steam_state(self, state: GameState) -> None:
        if self.settings.input_source != "steam":
            return
        self._handle_protocol_state(state)

    def _handle_protocol_state(self, state: GameState) -> None:
        self.last_state = state
        self._demo_preview = False
        self._render_state(state)
        if not self.running:
            # Pausing automatic advice still permits an explicit request or
            # local simulation. Duplicate Hook snapshots must not cancel it.
            if state.identity() in (self.pending_identity, self._simulation_identity):
                return
            self.pending_identity = None
            self._explicit_request_identity = None
            self.advised_identity = None
            self._clear_current_decision()
            self.hero_card.update_decision(action_name="已暂停", rec_tile="", shanten_num=None,
                confidence=None, probabilities=[], alternatives=[], source_note="实时建议已暂停",
                raw_detail="已收到新牌面，开始识别后生成当前局面的建议。")
            self.bottom_bar.set_status_info("已捕获牌局状态", "点击【开始识别】开启 AI 自动切牌建议")
            return
        if not self._hook_active:
            self._hook_active = True
            self.timer.stop()
        identity = state.identity()
        if identity != self.pending_identity:
            self._clear_current_decision()
            self.hero_card.update_decision(action_name="等待建议", rec_tile="", shanten_num=None,
                confidence=None, probabilities=[], alternatives=[], source_note="牌局已更新",
                raw_detail="正在处理最新局面")
        self.pending_identity = identity
        self.stable_count = 2
        if identity == self.advised_identity or self.advice_busy:
            return
        self.advised_identity = None
        self.start_advice(state)

    def on_capture_failure(self, error: str) -> None:
        self._comparison_state_available = False
        if self._comparison_dialog is not None:
            self._comparison_dialog.mark_current_state(None)
        self.capture_busy = False
        self.pending_identity = None
        self._explicit_request_identity = None
        self.advised_identity = None
        self.stable_count = 0
        self._clear_current_decision()
        self.hero_card.update_decision(action_name="采集异常", rec_tile="", shanten_num=None,
            confidence=None, probabilities=[], alternatives=[], source_note="画面不可用 · 暂停建议")
        self.bottom_bar.set_status_info("实时通道异常", error.splitlines()[-1])
        self.title_bar.set_status("采集异常", "warning")

    def edit_jev_settings(self) -> None:
        dialog = JevSettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings.api_key = dialog.key.text().strip()
            connection = dialog.connection()
            self.settings.model_endpoint = connection.endpoint
            self.settings.model_name = connection.model
            self.settings.model_protocol = connection.protocol
            self.settings.model_timeout = connection.timeout
            self.settings.local_fallback = connection.local_fallback
            self._connection_revision += 1
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

    def show_discard_comparison(self) -> None:
        state = self.last_state
        if state is None:
            QMessageBox.information(self, "暂无局面", "请先识别牌桌或手动核对手牌。")
            return
        try:
            options = discard_comparison(state)
        except (ValueError, NoDecision, UncertainState) as error:
            QMessageBox.information(self, "暂时无法比较", str(error))
            return
        if self._comparison_dialog is None:
            dialog = DiscardComparisonDialog(state, options, self)
            self._comparison_dialog = dialog
            dialog.refresh_requested.connect(self.show_discard_comparison)
            dialog.simulate_requested.connect(self._simulate_comparison_discard)
            dialog.finished.connect(self._close_comparison)
        else:
            self._comparison_dialog.set_snapshot(state, options)
        if not self._comparison_state_available:
            self._comparison_dialog.mark_current_state(None)
        self._comparison_dialog.show()
        self._comparison_dialog.raise_()
        self._comparison_dialog.activateWindow()

    def _simulate_comparison_discard(self, snapshot_identity: tuple[Any, ...], tile: str) -> None:
        state = self.last_state
        if not self._comparison_state_available or state is None or state.identity() != snapshot_identity:
            if self._comparison_dialog is not None:
                self._comparison_dialog.mark_current_state(state if self._comparison_state_available else None)
                self._comparison_dialog.feedback_label.setText("牌面已变化或不可用，请先刷新局面再模拟")
            return
        self.on_hand_tile_clicked(tile)

    def _close_comparison(self, result: int) -> None:
        dialog = self._comparison_dialog
        self._comparison_dialog = None
        if dialog is not None:
            dialog.deleteLater()

    def show_llm_analysis(self) -> None:
        """Launches LLM deep tactical analysis dialog."""
        if self._demo_preview or not self.last_state or not self.last_state.hand:
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
        if not self._comparison_state_available:
            self.bottom_bar.set_status_info("暂时无法模拟", "当前画面不可用，请等待识别恢复或手动核对牌面。")
            return
        try:
            base = discard_candidate(self.last_state, tile)
        except (ValueError, NoDecision, UncertainState) as error:
            self.bottom_bar.set_status_info("暂时无法模拟", str(error))
            return
        sim_candidate = replace(base, label=f"模拟打 {tile}")
        self._current_chosen = sim_candidate
        self._simulation_identity = self.last_state.identity()
        self.hand_widget.set_hand(self.last_state.hand, highlight_tile=tile)
        danger_label, danger_key, threat_note = self._danger_metrics(self.last_state, sim_candidate)

        self.hero_card.update_decision(
            action_name="模拟切牌",
            rec_tile=tile,
            shanten_num=sim_candidate.shanten,
            confidence=None,
            probabilities=[],
            danger_label=danger_label,
            danger_key=danger_key,
            threat_note=threat_note,
            draw_info="手动模拟",
            melds_count=self.last_state.open_melds,
            alternatives=[],
            source_note="手动模拟 · 点击【AI建议】恢复推荐",
            raw_detail=sim_candidate.rationale,
        )
        self.bottom_bar.set_status_info(
            "手动模拟切牌",
            f"切 {tile}  ·  {shanten_text(sim_candidate.shanten)}  ·  进张约 {sim_candidate.ukeire} 张  ·  危险 {danger_label}",
        )

    def manual_state(self, force_resume: bool = False) -> None:
        dialog = ManualStateDialog(self.last_state, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.state is None:
            return
        self._capture_generation += 1
        resume = force_resume or dialog.learn_and_resume.isChecked()
        self.manual = not resume
        self.running = False
        self.timer.stop()
        self.bottom_bar.btn_toggle.setText("恢复识别" if not resume else "暂停识别")
        self.last_state = dialog.state
        self._demo_preview = False
        self._render_state(dialog.state)
        self.pending_identity = dialog.state.identity()
        self.stable_count = 2
        self.advised_identity = None
        self.bottom_bar.set_status_info("手动状态", "已加载手动输入的局面")
        self._start_latest_if_needed()
        if resume:
            self._begin_realtime("已应用手动局面 · 后续协议事件将自动同步")

    def closeEvent(self, event: Any) -> None:
        if self._comparison_dialog is not None:
            self._comparison_dialog.close()
        rectangle = self.normalGeometry() if self.isMaximized() or self.isMinimized() else self.geometry()
        if rectangle.isValid():
            self.settings.window_geometry = rectangle.getRect()
            try:
                self.settings.save()
            except OSError:
                pass  # Closing must still work if the configuration directory is read-only.
        self.timer.stop()
        self.running = False
        self.pool.waitForDone(1000)
        self.hook.stop()
        self.steam.stop()
        super().closeEvent(event)


HUD_STYLESHEET = """
/* Cyberpunk Deep Glassmorphic Translucent Theme for Mahjong HUD */

QFrame#overlayContainer {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0d1322, stop:0.45 #090e18, stop:1 #060911);
    border: 1.2px solid rgba(56, 189, 248, 0.40);
    border-radius: 14px;
}

QScrollArea#hudContentScroll, QWidget#hudScrollableContent {
    background: transparent;
    border: none;
}
QScrollArea#hudContentScroll QScrollBar:vertical {
    background: rgba(10, 15, 26, 0.4);
    width: 5px;
    margin: 4px 1px;
    border: none;
    border-radius: 2px;
}
QScrollArea#hudContentScroll QScrollBar::handle:vertical {
    background: rgba(100, 116, 139, 0.65);
    border-radius: 2.5px;
    min-height: 24px;
}
QScrollArea#hudContentScroll QScrollBar::handle:vertical:hover {
    background: rgba(56, 189, 248, 0.8);
}
QScrollArea#hudContentScroll QScrollBar::add-line:vertical,
QScrollArea#hudContentScroll QScrollBar::sub-line:vertical {
    height: 0;
}

QLabel#appTitle {
    color: #f8fafc;
    font-size: 14px;
    font-weight: 800;
    letter-spacing: 0.8px;
}

QLabel#roundInfoText {
    color: #e2e8f0;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.3px;
}

QPushButton#headerPillBtn {
    color: #cbd5e1;
    background: rgba(30, 41, 59, 0.70);
    border: 1px solid rgba(71, 85, 105, 0.60);
    border-radius: 6px;
    font-weight: 700;
    font-size: 11px;
    padding: 3px 9px;
    min-width: 22px;
}
QPushButton#headerPillBtn:hover {
    color: #ffffff;
    background: rgba(51, 65, 85, 0.90);
    border-color: rgba(56, 189, 248, 0.75);
}
QPushButton#headerPillBtn:pressed {
    background: rgba(20, 30, 46, 0.90);
}

QPushButton#headerCloseBtn {
    color: #94a3b8;
    background: rgba(30, 41, 59, 0.70);
    border: 1px solid rgba(71, 85, 105, 0.60);
    border-radius: 6px;
    font-weight: bold;
    font-size: 13px;
    padding: 2px 8px;
}
QPushButton#headerCloseBtn:hover {
    color: #ffffff;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #f43f5e, stop:1 #e11d48);
    border-color: #fb7185;
}

/* Four Players Card */
QFrame#tableCard {
    background: rgba(16, 22, 38, 0.75);
    border: 1px solid rgba(45, 60, 92, 0.65);
    border-radius: 8px;
}

QLabel#playerWindBadge {
    color: #e2e8f0;
    font-size: 12px;
    font-weight: 800;
}

QLabel#riichiBadge {
    color: #ffffff;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #ef4444, stop:1 #b91c1c);
    border: 1px solid #f87171;
    border-radius: 3px;
    font-size: 9px;
    font-weight: 800;
    padding: 1px 4px;
}

QLabel#playerScore {
    color: #f8fafc;
    font-size: 11px;
    font-weight: 700;
}

QLabel#playerMelds {
    color: #64748b;
    font-size: 9px;
    font-weight: 600;
}

/* Hand Section */
QLabel#sectionTitle {
    color: #e2e8f0;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.3px;
}

QPushButton#ghostPillBtn {
    color: #94a3b8;
    background: rgba(20, 28, 46, 0.65);
    border: 1px solid rgba(51, 65, 85, 0.6);
    border-radius: 5px;
    font-size: 10px;
    font-weight: 600;
    padding: 2px 8px;
}
QPushButton#ghostPillBtn:hover {
    color: #f8fafc;
    background: rgba(30, 42, 68, 0.85);
    border-color: rgba(56, 189, 248, 0.65);
}

QFrame#tilesContainerCard {
    background: rgba(16, 22, 38, 0.72);
    border: 1px solid rgba(42, 56, 86, 0.6);
    border-radius: 8px;
}

/* Hero Decision Card */
QFrame#decisionHeroCard {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(14, 38, 38, 0.88), stop:0.5 rgba(10, 24, 28, 0.92), stop:1 rgba(7, 16, 22, 0.95));
    border: 1.5px solid rgba(16, 185, 129, 0.78);
    border-radius: 10px;
}

QLabel#heroActionBadge {
    color: #022c22;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #34d399, stop:1 #10b981);
    font-size: 11px;
    font-weight: 800;
    border: 1px solid #6ee7b7;
    border-radius: 6px;
    padding: 3px 8px;
}

QLabel#heroShantenLabel {
    color: #34d399;
    font-size: 18px;
    font-weight: 900;
}

QLabel#heroConfidencePill {
    color: #6ee7b7;
    background: rgba(16, 185, 129, 0.18);
    border: 1px solid rgba(16, 185, 129, 0.55);
    border-radius: 8px;
    font-size: 10px;
    font-weight: 700;
    padding: 2px 7px;
}

QLabel#heroDescText {
    color: #93c5fd;
    font-size: 10px;
    font-weight: 500;
    line-height: 1.3;
}

QLabel#dataPill {
    color: #a7f3d0;
    background: rgba(6, 78, 59, 0.55);
    border: 1px solid rgba(16, 185, 129, 0.45);
    border-radius: 6px;
    font-size: 10px;
    font-weight: 700;
    padding: 2px 7px;
}

QLabel#heroSubText {
    color: #94a3b8;
    font-size: 10px;
    font-weight: 500;
}

QLabel#heroFootnote {
    color: #64748b;
    font-size: 9px;
    font-weight: 500;
}

/* Bottom Action Bar */
QFrame#bottomStatusBox {
    background: rgba(15, 23, 42, 0.75);
    border: 1px solid rgba(38, 52, 78, 0.65);
    border-left: 2.5px solid #38bdf8;
    border-radius: 6px;
}

QLabel#bottomStatusTitle {
    color: #f1f5f9;
    font-size: 11px;
    font-weight: 700;
}

QLabel#bottomStatusSub {
    color: #38bdf8;
    font-size: 10px;
    font-weight: 500;
}

QPushButton#hudGhostBtn {
    color: #cbd5e1;
    background: rgba(26, 36, 56, 0.75);
    border: 1px solid rgba(56, 72, 100, 0.65);
    border-radius: 6px;
    font-size: 11px;
    font-weight: 700;
    padding: 5px 8px;
}
QPushButton#hudGhostBtn:hover {
    color: #ffffff;
    background: rgba(45, 60, 90, 0.90);
    border-color: rgba(56, 189, 248, 0.7);
}
QPushButton#hudGhostBtn:pressed {
    background: rgba(18, 25, 40, 0.90);
}

QPushButton#hudPrimaryBtn {
    color: #022c22;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #10b981, stop:1 #059669);
    border: 1px solid #34d399;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 800;
    padding: 6px 14px;
}
QPushButton#hudPrimaryBtn:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #34d399, stop:1 #10b981);
    border-color: #6ee7b7;
}
QPushButton#hudPrimaryBtn:pressed {
    background: #047857;
}

/* Menus */
QMenu {
    background: rgba(15, 23, 42, 0.96);
    color: #f1f5f9;
    border: 1px solid rgba(56, 189, 248, 0.35);
    border-radius: 8px;
    padding: 5px;
}
QMenu::item {
    padding: 6px 20px;
    border-radius: 5px;
    font-size: 11px;
    font-weight: 500;
}
QMenu::item:selected {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #059669, stop:1 #10b981);
    color: #ffffff;
}
QMenu::separator {
    height: 1px;
    background: rgba(51, 65, 85, 0.6);
    margin: 4px 6px;
}

/* Unified Cyberpunk Dialog Styling */
QDialog {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0f172a, stop:0.5 #0b1120, stop:1 #080d18);
    color: #f1f5f9;
}

QDialog QLabel {
    color: #cbd5e1;
    font-size: 11px;
}

QDialog QLabel#dialogHeader {
    color: #f8fafc;
    font-size: 13px;
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
    line-height: 1.4;
}

QDialog QFrame#dialogHeroBanner {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(15, 23, 42, 0.95), stop:1 rgba(30, 41, 59, 0.65));
    border: 1px solid rgba(56, 189, 248, 0.25);
    border-bottom: 2px solid rgba(56, 189, 248, 0.5);
    border-radius: 8px;
}

QDialog QFrame#dialogSectionCard {
    background: rgba(15, 23, 42, 0.7);
    border: 1px solid rgba(51, 65, 85, 0.6);
    border-radius: 8px;
}

QDialog QLabel#dialogSectionTitle {
    color: #38bdf8;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.5px;
}

QDialog QFrame#dialogCalloutBox {
    background: rgba(12, 74, 110, 0.2);
    border: 1px solid rgba(56, 189, 248, 0.3);
    border-left: 3px solid #38bdf8;
    border-radius: 6px;
}

QDialog QTextEdit#terminalOutput {
    background: #070c16;
    color: #38bdf8;
    border: 1px solid rgba(56, 189, 248, 0.35);
    border-radius: 6px;
    font-family: "Consolas", "Cascadia Code", "Courier New", monospace;
    font-size: 11px;
    padding: 8px;
    line-height: 1.4;
}

QDialog QPushButton#probeTestButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #0369a1);
    color: #ffffff;
    border: 1px solid #38bdf8;
    font-weight: 700;
    border-radius: 6px;
    padding: 7px 16px;
}

QDialog QPushButton#probeTestButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #38bdf8, stop:1 #0284c7);
    border-color: #7dd3fc;
}

QWidget#modelSettingsBody {
    background: #0c1220;
}
QDialog QLineEdit, QDialog QComboBox, QDialog QSpinBox, QDialog QDoubleSpinBox, QDialog QTextEdit {
    background: rgba(15, 23, 42, 0.85);
    color: #f8fafc;
    border: 1px solid rgba(56, 72, 100, 0.75);
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}

QDialog QLineEdit:focus, QDialog QComboBox:focus, QDialog QSpinBox:focus, QDialog QDoubleSpinBox:focus, QDialog QTextEdit:focus {
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
    background: rgba(26, 36, 56, 0.7);
    color: #94a3b8;
    border: 1px solid rgba(51, 65, 85, 0.5);
    padding: 6px 16px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-weight: 600;
    font-size: 11px;
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
    padding: 6px 16px;
    font-size: 11px;
    font-weight: 600;
}

QDialog QPushButton:hover {
    color: #ffffff;
    background: #334155;
    border-color: rgba(56, 189, 248, 0.6);
}

QDialog QPushButton:default, QDialog QPushButton[primary="true"] {
    color: #022c22;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #10b981, stop:1 #059669);
    border: 1px solid #34d399;
    font-weight: 800;
}

QDialog QPushButton:default:hover, QDialog QPushButton[primary="true"]:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #34d399, stop:1 #10b981);
    border-color: #6ee7b7;
}

QDialog QScrollArea {
    background: transparent;
    border: 1px solid rgba(51, 65, 85, 0.5);
    border-radius: 6px;
}
QPushButton:focus, QPushButton#hudGhostBtn:focus, QPushButton#hudPrimaryBtn:focus,
QPushButton#headerPillBtn:focus {
    border: 1px solid #38bdf8;
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
