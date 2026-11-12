"""Dialogs and utility windows for Mahjong Jev Advisor.

Contains:
- DiscardComparisonDialog for local tile efficiency and danger comparison
- SteamConnectionDialog for Steam real-time Liqi WebSocket capture & certificate setup
- JevSettingsDialog & LLMSettingsDialog
- ManualStateDialog for manual verification and rapid tile input
- LLMAnalysisDialog for deep AI tactical commentary
"""

from __future__ import annotations

import json
from typing import Any
from PySide6.QtCore import QEvent, QObject, QRect, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QDoubleSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QApplication,
)

from .hook import HOOK_PORT
from .settings import Settings
from .state import Candidate, GameState
from .steam_capture import (
    DEFAULT_STEAM_PROCESS,
    detect_steam_game_processes,
    generate_clash_rules,
    generate_surge_rules,
    install_mitm_cert,
    is_mitm_cert_installed,
    mitm_cert_path,
)
from .tiles import parse_buttons


def make_dialog_header(title: str, subtitle: str, badge_text: str | None = None) -> QFrame:
    """Creates a consistent Cyberpunk glassmorphic header banner for dialogs."""
    banner = QFrame()
    banner.setObjectName("dialogHeroBanner")
    layout = QVBoxLayout(banner)
    layout.setContentsMargins(14, 10, 14, 10)
    layout.setSpacing(3)

    top_row = QHBoxLayout()
    top_row.setSpacing(8)
    if badge_text:
        badge = QLabel(badge_text)
        badge.setStyleSheet(
            "background: rgba(56, 189, 248, 0.15); color: #38bdf8; "
            "border: 1px solid rgba(56, 189, 248, 0.4); border-radius: 4px; "
            "padding: 2px 7px; font-size: 10px; font-weight: 700;"
        )
        top_row.addWidget(badge)
    lbl_title = QLabel(title)
    lbl_title.setObjectName("dialogHeader")
    top_row.addWidget(lbl_title)
    top_row.addStretch()
    layout.addLayout(top_row)

    if subtitle:
        lbl_sub = QLabel(subtitle)
        lbl_sub.setObjectName("dialogSubHeader")
        lbl_sub.setWordWrap(True)
        layout.addWidget(lbl_sub)
    return banner


class DiscardComparisonDialog(QDialog):
    """Non-modal, refreshable comparison with an explicit immutable snapshot."""
    refresh_requested = Signal()
    simulate_requested = Signal(object, str)

    def __init__(self, state: GameState, options: tuple[Candidate, ...], parent=None):
        super().__init__(parent)
        self.setWindowTitle("切牌比较 · 局面快照")
        self.setSizeGripEnabled(True)
        self.setMinimumSize(400, 380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addWidget(make_dialog_header("比较每一种切法", "点击列标题排序，选择一行查看牌理；双击或按 Enter 模拟该切法。", "本地分析"))
        self.snapshot_label = QLabel()
        self.snapshot_label.setWordWrap(True)
        layout.addWidget(self.snapshot_label)
        self.freshness_label = QLabel()
        self.freshness_label.setWordWrap(True)
        layout.addWidget(self.freshness_label)
        self.table = QTableWidget(0, 6)
        self.table.setAccessibleName("切牌方案比较")
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("""
            QTableWidget { background: #0f172a; alternate-background-color: #172238;
                color: #e2e8f0; gridline-color: #334155; border: 1px solid #334155;
                selection-background-color: #164e63; selection-color: #ffffff; }
            QTableWidget:focus { border: 1px solid #38bdf8; }
            QHeaderView::section { background: #1e293b; color: #cbd5e1;
                padding: 7px; border: none; border-right: 1px solid #334155; }
            QScrollBar:horizontal { background: #0f172a; height: 12px; margin: 0; }
            QScrollBar:vertical { background: #0f172a; width: 12px; margin: 0; }
            QScrollBar::handle { background: #475569; border-radius: 5px; min-width: 24px; min-height: 24px; }
            QScrollBar::handle:hover { background: #64748b; }
            QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
            QScrollBar::add-page, QScrollBar::sub-page { background: none; }
            QAbstractScrollArea::corner { background: #0f172a; }
        """)
        self.headers = ["切牌", "向听", "进张（张）", "危险级", "弃宝牌", "有效牌"]
        self.table.setHorizontalHeaderLabels(self.headers)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.table.itemDoubleClicked.connect(self.simulate_selected)
        self.table.installEventFilter(self)
        layout.addWidget(self.table, 1)
        self.detail_label = QLabel("选择一行查看牌理")
        self.detail_label.setWordWrap(True)
        self.detail_label.setMinimumHeight(42)
        self.detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.detail_label)
        note = QLabel("0 向听 = 听牌。进张按可见牌估算，危险级仅评估已知立直对手；无人立直或信息不全时显示 —。操作限制请以游戏为准。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.feedback_label = QLabel("无需 API Key · 不会在游戏中执行操作")
        self.feedback_label.setWordWrap(True)
        layout.addWidget(self.feedback_label)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("刷新局面")
        self.refresh_button.setToolTip("使用主窗口当前已识别的牌面重新比较（Ctrl+R）")
        self.refresh_button.setShortcut("Ctrl+R")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        buttons.addWidget(self.refresh_button)
        self.simulate_button = QPushButton("模拟切牌")
        self.simulate_button.setToolTip("在主窗口模拟选中的切法（Enter），不会在游戏中出牌")
        self.simulate_button.setShortcut("Return")
        self.simulate_button.clicked.connect(self.simulate_selected)
        buttons.addWidget(self.simulate_button)
        self.copy_button = QPushButton("复制比较表")
        self.copy_button.setToolTip("复制全部比较结果（Ctrl+C）")
        self.copy_button.setShortcut("Ctrl+C")
        self.copy_button.clicked.connect(self.copy_table)
        buttons.addWidget(self.copy_button)
        buttons.addStretch()
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.close_button)
        for button in (self.refresh_button, self.simulate_button, self.copy_button, self.close_button):
            button.setAutoDefault(False)
            button.setMinimumHeight(32)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addLayout(buttons)
        self.set_snapshot(state, options)
        # Obtain the application-owned screen directly. QWidget.screen() can
        # tie its Python wrapper to the parent window's lifetime in PySide.
        screen = QApplication.screenAt(parent.frameGeometry().center()) if parent is not None else None
        screen = screen or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, 800, 650)
        self.resize(min(780, max(400, available.width() - 32)), min(600, max(380, available.height() - 64)))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.table and event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            simulate = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() in (
                Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier,
            )
            copy = event.matches(QKeySequence.StandardKey.Copy) or (
                event.key() == Qt.Key.Key_C and event.modifiers() == Qt.KeyboardModifier.ControlModifier
            )
            if simulate or copy:
                event.accept()
                if event.type() == QEvent.Type.KeyPress:
                    self.simulate_selected() if simulate else self.copy_table()
                return True
        return super().eventFilter(watched, event)

    def set_snapshot(self, state: GameState, options: tuple[Candidate, ...]) -> None:
        header = self.table.horizontalHeader()
        sort_column = header.sortIndicatorSection() if self.table.isSortingEnabled() else -1
        sort_order = header.sortIndicatorOrder()
        current_row = self.table.currentRow()
        selected_item = self.table.item(current_row, 0) if current_row >= 0 else None
        selected_tile = selected_item.text() if selected_item is not None else None
        self.snapshot_identity = state.identity()
        self.snapshot_label.setText(f"{state.player_count}人麻将 · 手牌：{' '.join(state.hand)}")
        self.table.setSortingEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(len(options))
        opponents = [r for i, r in enumerate(state.riichi) if i != state.seat]
        danger_available = any(opponents) and all(r is not None for r in opponents)
        for row, candidate in enumerate(options):
            values = [candidate.tile, candidate.shanten, candidate.ukeire,
                      candidate.danger if danger_available else None,
                      candidate.dora_loss, " ".join(candidate.improving_tiles) or "—"]
            for column, value in enumerate(values):
                item = QTableWidgetItem()
                item.setData(Qt.ItemDataRole.DisplayRole, value if value is not None else "—")
                item.setData(Qt.ItemDataRole.UserRole, candidate.rationale)
                item.setToolTip(candidate.rationale)
                if value is not None:
                    self._colorize(item, column, value)
                self.table.setItem(row, column, item)
        header.setSortIndicator(sort_column, sort_order)
        self.table.setSortingEnabled(True)
        self.mark_current_state(state)
        self.feedback_label.setText(f"已载入 {len(options)} 种切法 · 本地分析")
        if options:
            selected_row = next((row for row in range(len(options))
                                 if self.table.item(row, 0).text() == selected_tile), 0)
            self.table.selectRow(selected_row)
            self._show_detail()
        else:
            self.detail_label.setText("当前没有可比较的切法")

    @staticmethod
    def _colorize(item: QTableWidgetItem, column: int, value: Any) -> None:
        """Band colors matching the hero card: shanten by progress, danger by risk."""
        from PySide6.QtGui import QBrush, QColor
        if column == 1 and isinstance(value, int):
            colors = {0: QColor(253, 224, 71), 1: QColor(56, 189, 248)}
            if value in colors:
                item.setForeground(QBrush(colors[value]))
        elif column == 3 and isinstance(value, int):
            if value <= 0:
                color = QColor(110, 231, 183)
            elif value == 1:
                color = QColor(125, 211, 252)
            elif value <= 3:
                color = QColor(252, 211, 77)
            elif value == 4:
                color = QColor(253, 186, 116)
            else:
                color = QColor(252, 165, 165)
            item.setForeground(QBrush(color))

    def mark_current_state(self, state: GameState | None) -> None:
        fresh = state is not None and state.identity() == self.snapshot_identity
        self._snapshot_current = fresh
        self.simulate_button.setEnabled(fresh and self.table.rowCount() > 0)
        if state is None:
            message = "当前画面不可用 · 下表保留旧快照，待识别恢复后刷新"
        elif fresh:
            message = "当前局面快照 · 可同时操作主窗口"
        else:
            message = "牌面已变化 · 点击「刷新局面」更新；下表保留旧快照"
        self.freshness_label.setText(message)
        self.freshness_label.setStyleSheet("color: #94a3b8;" if fresh else "color: #fbbf24;")

    def _show_detail(self) -> None:
        selected = self.table.selectedItems()
        self.detail_label.setText(selected[0].data(Qt.ItemDataRole.UserRole) if selected else "选择一行查看牌理")

    def simulate_selected(self) -> None:
        if not self._snapshot_current:
            self.feedback_label.setText("当前快照不可用于模拟，请先恢复识别并刷新局面")
            return
        row = self.table.currentRow()
        if row >= 0:
            tile = self.table.item(row, 0).text()
            self.simulate_requested.emit(self.snapshot_identity, tile)

    def copy_table(self) -> None:
        rows = ["\t".join(self.headers)]
        for row in range(self.table.rowCount()):
            rows.append("\t".join(self.table.item(row, col).text() for col in range(self.table.columnCount())))
        QApplication.clipboard().setText("\n".join(rows))
        self.feedback_label.setText(f"已复制 {self.table.rowCount()} 种切法，可粘贴到表格或聊天中")


class SteamConnectionDialog(QDialog):
    """Configuration and one-click setup dialog for Steam Mahjong Soul Liqi capture."""

    def __init__(self, settings: Settings, diagnostic: dict[str, Any] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Steam 雀魂实时连接与证书设置")
        self._diagnostic = diagnostic or {}
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        banner = make_dialog_header(
            title="Steam 雀魂 · 毫秒级只读协议直连配置",
            subtitle="不截图、不改包、不注入内存，直接解码雀魂 Liqi WebSocket 协议流（完美支持四人/三人麻将）",
            badge_text="STEAM 实时引擎",
        )
        root.addWidget(banner)

        card1 = QFrame()
        card1.setObjectName("dialogSectionCard")
        form1 = QFormLayout(card1)
        form1.setSpacing(10)
        form1.setContentsMargins(12, 10, 12, 10)

        lbl_sec1 = QLabel("● 捕获模式与进程/端口参数")
        lbl_sec1.setObjectName("dialogSectionTitle")
        form1.addRow(lbl_sec1)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("进程直连模式 (local:Jantama_MahjongSoul · 零代理配置，推荐)", "process")
        self.mode_combo.addItem("本地只读代理 (127.0.0.1:端口 · 免管理员，支持 PAC / 启动项 / Clash)", "proxy")
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(settings.steam_capture_mode)))
        form1.addRow("捕获方式", self.mode_combo)

        proc_row = QHBoxLayout()
        self.process_name = QLineEdit(settings.steam_process_name or DEFAULT_STEAM_PROCESS)
        self.process_name.setPlaceholderText("默认 Jantama_MahjongSoul")
        proc_row.addWidget(self.process_name, 1)
        self.scan_btn = QPushButton("扫描运行中雀魂")
        self.scan_btn.clicked.connect(self._scan_processes)
        proc_row.addWidget(self.scan_btn)
        form1.addRow("目标进程名", proc_row)

        self.proxy_port = QSpinBox()
        self.proxy_port.setRange(1024, 65535)
        self.proxy_port.setValue(int(settings.steam_proxy_port or 10999))
        form1.addRow("本地代理端口", self.proxy_port)

        self.upstream_proxy = QLineEdit(settings.steam_upstream_proxy)
        self.upstream_proxy.setPlaceholderText("可选，如 http://127.0.0.1:7890（若使用加速器/梯子时填写）")
        form1.addRow("上游代理链", self.upstream_proxy)

        root.addWidget(card1)

        card2 = QFrame()
        card2.setObjectName("dialogSectionCard")
        v2 = QVBoxLayout(card2)
        v2.setContentsMargins(12, 10, 12, 10)
        v2.setSpacing(8)

        lbl_sec2 = QLabel("● 本地 TLS 证书信任与一键分流工具")
        lbl_sec2.setObjectName("dialogSectionTitle")
        v2.addWidget(lbl_sec2)

        cert_row = QHBoxLayout()
        self.cert_status_label = QLabel()
        self.cert_status_label.setWordWrap(True)
        cert_row.addWidget(self.cert_status_label, 1)
        self.install_cert_btn = QPushButton("一键安装 / 信任本地 CA 证书")
        self.install_cert_btn.clicked.connect(self._install_cert)
        cert_row.addWidget(self.install_cert_btn)
        v2.addLayout(cert_row)

        copy_row = QHBoxLayout()
        self.copy_pac_btn = QPushButton("复制系统 PAC 地址")
        self.copy_pac_btn.clicked.connect(self._copy_pac_url)
        copy_row.addWidget(self.copy_pac_btn)

        self.copy_launch_btn = QPushButton("复制 Steam 启动项")
        self.copy_launch_btn.clicked.connect(self._copy_steam_launch_arg)
        copy_row.addWidget(self.copy_launch_btn)

        self.copy_clash_btn = QPushButton("复制 Clash 分流规则")
        self.copy_clash_btn.clicked.connect(self._copy_clash_rules)
        copy_row.addWidget(self.copy_clash_btn)
        v2.addLayout(copy_row)

        root.addWidget(card2)

        self.output = QTextEdit()
        self.output.setObjectName("terminalOutput")
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(140)
        root.addWidget(self.output, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存并应用")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.mode_combo.currentIndexChanged.connect(self._refresh_summary)
        self.process_name.textChanged.connect(self._refresh_summary)
        self.proxy_port.valueChanged.connect(self._refresh_summary)
        self._refresh_cert_status()
        self._refresh_summary()
        self.resize(600, 580)

    def _refresh_cert_status(self) -> None:
        installed = is_mitm_cert_installed()
        path = mitm_cert_path()
        if installed:
            self.cert_status_label.setText(f"证书状态：已安装并受系统信任（{path.name}）")
            self.cert_status_label.setStyleSheet("color: #34d399; font-weight: 700;")
        else:
            self.cert_status_label.setText(f"证书状态：尚未安装（首次使用请点击右侧按钮安装 {path.name}）")
            self.cert_status_label.setStyleSheet("color: #fbbf24; font-weight: 700;")

    def _refresh_summary(self) -> None:
        port = int(self.proxy_port.value())
        proc = self.process_name.text().strip() or DEFAULT_STEAM_PROCESS
        mode = self.mode_combo.currentData()
        if mode == "process":
            header = (
                f"【当前模式：OS 进程直连 (local:{proc})】\n"
                "• 自动定向捕获 Steam 雀魂客户端进程流量，无需配置系统代理。\n"
                f"• 若未授予管理员/网络扩展权限，将自动平滑回退至本地代理 127.0.0.1:{port}。\n\n"
            )
        else:
            header = (
                f"【当前模式：本地只读代理 (127.0.0.1:{port})】\n"
                "• 免管理员权限，可通过以下任一方式将 Steam 雀魂流量导向本地只读代理：\n\n"
            )
        self.output.setPlainText(
            header
            + generate_surge_rules(proxy_port=port, process_name=proc, hook_port=HOOK_PORT)
            + "\n"
            + generate_clash_rules(proxy_port=port, process_name=proc)
        )

    def _scan_processes(self) -> None:
        found = detect_steam_game_processes()
        if not found:
            self.output.setPlainText(
                "未检测到正在运行的 Steam 雀魂客户端进程。\n"
                "默认进程名保持为 Jantama_MahjongSoul（启动雀魂后会自动匹配）。"
            )
            return
        first = found[0]
        self.process_name.setText(str(first["spec"]))
        lines = [f"检测到 {len(found)} 个雀魂相关进程："]
        for item in found:
            lines.append(f"• {item['name']} (PID {item['pid']}) -> 进程标识: {item['spec']}")
        self.output.setPlainText("\n".join(lines))

    def _install_cert(self) -> None:
        ok, message = install_mitm_cert()
        self._refresh_cert_status()
        self.output.setPlainText(message)
        if ok:
            QMessageBox.information(self, "证书已就绪", message)
        else:
            QMessageBox.warning(self, "证书安装提示", message)

    def _copy_pac_url(self) -> None:
        url = f"http://127.0.0.1:{HOOK_PORT}/proxy.pac"
        QApplication.clipboard().setText(url)
        self.output.setPlainText(f"已复制系统 PAC 地址：{url}\n可在系统『代理设置 -> 使用设置脚本』中粘贴该地址。")

    def _copy_steam_launch_arg(self) -> None:
        arg = f"--proxy-server=http://127.0.0.1:{int(self.proxy_port.value())}"
        QApplication.clipboard().setText(arg)
        self.output.setPlainText(f"已复制 Steam 启动项：{arg}\n在 Steam 库右键『雀魂麻将』-> 属性 -> 通用 -> 启动选项 中粘贴即可。")

    def _copy_clash_rules(self) -> None:
        rules = generate_clash_rules(
            proxy_port=int(self.proxy_port.value()),
            process_name=self.process_name.text().strip() or DEFAULT_STEAM_PROCESS,
        )
        QApplication.clipboard().setText(rules)
        self.output.setPlainText("已复制 Clash / Mihomo 分流规则到剪贴板：\n\n" + rules)


class ConnectionSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class ConnectionWorker(QRunnable):
    def __init__(self, key, connection):
        super().__init__()
        self.key, self.connection = key, connection
        self.signals = ConnectionSignals()

    def run(self):
        from .jev import JevError, test_connection
        try:
            self.signals.done.emit(test_connection(self.key, self.connection))
        except JevError as error:
            self.signals.failed.emit(str(error))
        except Exception as error:
            self.signals.failed.emit(f"连接测试异常：{type(error).__name__}")


class JevSettingsDialog(QDialog):
    """Editable connection with a real, asynchronous inference probe."""

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("在线模型连接设置")
        self._worker = None
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        banner = make_dialog_header(
            title="雀魂 Jev · 在线模型连接配置",
            subtitle="配置 Jev 深度强化学习推理模型与网络容灾回退机制",
            badge_text="AI 推理引擎",
        )
        root.addWidget(banner)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("modelSettingsBody")
        body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(8, 8, 8, 8)
        body_layout.setSpacing(12)

        # Card 1: 接口与鉴权
        card1 = QFrame()
        card1.setObjectName("dialogSectionCard")
        form1 = QFormLayout(card1)
        form1.setSpacing(10)
        form1.setContentsMargins(12, 10, 12, 10)

        lbl_sec1 = QLabel("● 接口协议与网关认证")
        lbl_sec1.setObjectName("dialogSectionTitle")
        form1.addRow(lbl_sec1)

        self.protocol = QComboBox()
        for label, value in [("Vercel · Jev 评估接口", "vercel"), ("TypeSafe · System One", "typesafe"),
                             ("OpenRouter · Jev System One", "openrouter"),
                             ("OpenAI 兼容 · Chat Completions", "openai")]:
            self.protocol.addItem(label, value)
        self.protocol.setCurrentIndex(max(0, self.protocol.findData(settings.model_protocol)))
        form1.addRow("接口协议", self.protocol)

        self.endpoint = QLineEdit(settings.model_endpoint)
        self.endpoint.setPlaceholderText("https://… 完整 POST 请求地址")
        form1.addRow("请求地址", self.endpoint)

        self.model = QLineEdit(settings.model_name)
        form1.addRow("模型名称", self.model)

        self.key = QLineEdit(settings.api_key)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("对应服务商的 API Key")
        form1.addRow("API Key", self.key)

        body_layout.addWidget(card1)

        # Card 2: 运行与容灾
        card2 = QFrame()
        card2.setObjectName("dialogSectionCard")
        form2 = QFormLayout(card2)
        form2.setSpacing(10)
        form2.setContentsMargins(12, 10, 12, 10)

        lbl_sec2 = QLabel("● 运行参数与容灾策略")
        lbl_sec2.setObjectName("dialogSectionTitle")
        form2.addRow(lbl_sec2)

        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(0.5, 120)
        self.timeout.setSuffix(" 秒")
        self.timeout.setValue(settings.model_timeout)
        form2.addRow("请求超时", self.timeout)

        self.fallback = QCheckBox("网络故障时允许本地规则回退（默认关闭）")
        self.fallback.setChecked(settings.local_fallback)
        form2.addRow(self.fallback)

        self.log = QCheckBox("保存决策日志（保护隐私，不保存截图或 Key）")
        self.log.setChecked(settings.logging_enabled)
        form2.addRow(self.log)

        tip_box = QFrame()
        tip_box.setObjectName("dialogCalloutBox")
        tip_layout = QVBoxLayout(tip_box)
        tip_layout.setContentsMargins(10, 8, 10, 8)
        tip = QLabel("提示：地址须包含接口路径。Jev 使用评估协议；聊天模型请选择 OpenAI 兼容协议。测试将发送一次简短推断请求。")
        tip.setObjectName("dialogTip")
        tip.setWordWrap(True)
        tip_layout.addWidget(tip)
        form2.addRow(tip_box)

        body_layout.addWidget(card2)
        body_layout.addStretch()

        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # Probe test button
        self.test_button = QPushButton("测试连接 · 真实请求")
        self.test_button.setObjectName("probeTestButton")
        self.test_button.clicked.connect(self._test_connection)
        root.addWidget(self.test_button)

        # Diagnostic output console
        self.result = QTextEdit()
        self.result.setObjectName("terminalOutput")
        self.result.setReadOnly(True)
        self.result.setMaximumHeight(110)
        self.result.setPlainText("尚未测试。测试成功必须收到可解析的模型答案。")
        root.addWidget(self.result)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存配置")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)

        self.protocol.currentIndexChanged.connect(self._protocol_changed)
        for field in (self.endpoint, self.model, self.key):
            field.textChanged.connect(self._invalidate_test)
        self.timeout.valueChanged.connect(self._invalidate_test)
        self.resize(560, 640)
        screen = self.screen().availableGeometry()
        self.resize(min(self.width(), screen.width() - 40), min(self.height(), screen.height() - 80))

    def _protocol_changed(self):
        presets = {
            "vercel": ("https://ai-gateway.vercel.sh/v4/ai/evaluation-model", "typesafe-ai/jev-latest"),
            "typesafe": ("https://api.typesafe.ai/v1/systemone", "jev-latest"),
            "openrouter": ("https://openrouter.ai/api/v1/systemone", "jev-latest"),
            "openai": ("https://ai-gateway.vercel.sh/v1/chat/completions", ""),
        }
        endpoint, model = presets[self.protocol.currentData()]
        self.endpoint.setText(endpoint)
        self.model.setText(model)
        self.key.clear()
        self.result.setPlainText("渠道已切换，请填写该渠道的 API Key 并测试连接。")

    def _invalidate_test(self):
        self.result.setPlainText("配置已更改，请测试当前连接。")

    def connection(self):
        from .jev import ModelConnection
        return ModelConnection(self.endpoint.text().strip(), self.model.text().strip(),
                               self.protocol.currentData(), self.timeout.value(), self.fallback.isChecked())

    def _save(self):
        from .jev import JevError
        try:
            self.connection().validate()
        except (JevError, ValueError) as error:
            self.result.setPlainText(str(error))
            return
        self.accept()

    def _set_testing(self, active):
        for control in (self.protocol, self.endpoint, self.model, self.key, self.timeout, self.test_button,
                        self.buttons.button(QDialogButtonBox.StandardButton.Save)):
            control.setEnabled(not active)
        self.test_button.setText("正在请求模型…" if active else "测试连接 · 真实请求")

    def _test_connection(self):
        from .jev import JevError
        try:
            self.connection().validate()
            if not self.key.text().strip():
                raise JevError("请填写 API Key")
        except (JevError, ValueError) as error:
            self.result.setPlainText(str(error))
            return
        self._set_testing(True)
        self.result.setPlainText("正在向填写的地址发送 POST 请求，等待实际模型答案…")
        self._worker = ConnectionWorker(self.key.text().strip(), self.connection())
        self._worker.signals.done.connect(self._test_done)
        self._worker.signals.failed.connect(self._test_failed)
        QThreadPool.globalInstance().start(self._worker)

    def _test_done(self, advice):
        self._set_testing(False)
        self.result.setPlainText(f"连接成功 · HTTP 200 · 已解析模型答案\n模型：{advice.model}\n耗时：{advice.latency_ms} ms · 返回：{advice.selected.label}")

    def _test_failed(self, message):
        self._set_testing(False)
        self.result.setPlainText(f"连接失败\n{message}")


class LLMSettingsDialog(QDialog):
    """General LLM API configuration (DeepSeek, OpenAI, Claude, Qwen, etc.)."""

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("大模型设置")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        banner = make_dialog_header(
            title="通用大模型配置",
            subtitle="配置用于牌理深度复盘与局况解析的大模型 (支持 OpenAI 兼容格式)",
            badge_text="战术推演",
        )
        layout.addWidget(banner)

        card = QFrame()
        card.setObjectName("dialogSectionCard")
        form = QFormLayout(card)
        form.setSpacing(10)
        form.setContentsMargins(12, 12, 12, 12)

        self.base_url = QLineEdit(settings.llm_base_url)
        self.base_url.setPlaceholderText("如 https://api.deepseek.com/v1 或 https://api.openai.com/v1")
        form.addRow("API Base URL:", self.base_url)

        self.key = QLineEdit(settings.llm_api_key)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("输入通用大模型 API Key")
        form.addRow("API Key:", self.key)

        self.model = QLineEdit(settings.llm_model)
        self.model.setPlaceholderText("如 deepseek-chat, gpt-4o-mini")
        form.addRow("模型名称:", self.model)

        tip_box = QFrame()
        tip_box.setObjectName("dialogCalloutBox")
        tip_l = QVBoxLayout(tip_box)
        tip_l.setContentsMargins(10, 8, 10, 8)
        tips = QLabel("提示：支持所有 OpenAI 兼容格式接口，可用于复杂局况深度推演与复盘。")
        tips.setObjectName("dialogTip")
        tips.setWordWrap(True)
        tip_l.addWidget(tips)
        form.addRow(tip_box)

        layout.addWidget(card)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存配置")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setMinimumWidth(520)


class ManualStateDialog(QDialog):
    """Rapid manual state input & inspection dialog."""

    def __init__(self, state: GameState | None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("手动核对手牌与局面")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        banner = make_dialog_header(
            title="手动核对手牌与局面",
            subtitle="校准或手动录入雀魂对局状态，即时计算最优切牌路线与向听数",
            badge_text="对局校准",
        )
        layout.addWidget(banner)

        self.base = state.to_dict() if state else {
            "hand": [], "rivers": [[], [], [], []], "melds": [[], [], [], []],
            "dora_indicators": [], "buttons": [], "seat": 0,
            "round_wind": "E", "round_number": 1, "seat_wind": "E", "scores": [25000] * 4,
            "riichi": [False] * 4, "open_melds": 0,
        }

        self.tabs = QTabWidget()
        quick = QWidget()
        quick_layout = QVBoxLayout(quick)
        quick_layout.setContentsMargins(10, 10, 10, 10)
        quick_layout.setSpacing(10)

        legend_box = QFrame()
        legend_box.setObjectName("dialogCalloutBox")
        legend_layout = QHBoxLayout(legend_box)
        legend_layout.setContentsMargins(10, 6, 10, 6)
        legend_layout.setSpacing(10)

        lbl_legend = QLabel(
            "牌码记法：<span style='color:#f87171;font-weight:700;'>m 万</span> · "
            "<span style='color:#38bdf8;font-weight:700;'>p 筒</span> · "
            "<span style='color:#34d399;font-weight:700;'>s 索</span> · "
            "<span style='color:#fbbf24;font-weight:700;'>z 字牌</span> (1-7 东南西北白发中) · "
            "<span style='color:#f43f5e;font-weight:700;'>0m/0p/0s 赤五</span>"
        )
        lbl_legend.setTextFormat(Qt.TextFormat.RichText)
        legend_layout.addWidget(lbl_legend)
        quick_layout.addWidget(legend_box)

        form = QFormLayout()
        form.setSpacing(8)

        self.player_count = QComboBox()
        self.player_count.addItem("四人麻将", 4)
        self.player_count.addItem("三人麻将（雀魂）", 3)
        self.player_count.setCurrentIndex(1 if self.base.get("player_count") == 3 else 0)
        form.addRow("玩法:", self.player_count)

        self.hand = QLineEdit(" ".join(self.base.get("hand", [])))
        self.hand.setPlaceholderText("例如 4m4m1p1p4p4p4p2s2s2s7s8s9s1p 或空格分隔")
        form.addRow("当前手牌:", self.hand)

        self.dora = QLineEdit(" ".join(self.base.get("dora_indicators", [])))
        self.dora.setPlaceholderText("例如 7p")
        form.addRow("宝牌指示牌:", self.dora)

        self.action_buttons = QLineEdit(" ".join(self.base.get("buttons", [])))
        self.action_buttons.setPlaceholderText("例如 立直、碰、过；留空则只计算切牌")
        form.addRow("可见操作按钮:", self.action_buttons)

        self.last_discard = QLineEdit(self.base.get("last_discard") or "")
        self.last_discard.setPlaceholderText("吃碰杠判断时填，如 5m")
        form.addRow("上一张弃牌:", self.last_discard)

        self.open_melds = QSpinBox()
        self.open_melds.setRange(0, 4)
        self.open_melds.setValue(int(self.base.get("open_melds", 0)))
        form.addRow("自己的副露组数:", self.open_melds)

        winds_layout = QHBoxLayout()
        self.round_wind = QComboBox()
        self.round_wind.addItems(["E", "S", "W", "N"])
        self.round_wind.setCurrentText(self.base.get("round_wind", "E"))
        winds_layout.addWidget(QLabel("场风:"))
        winds_layout.addWidget(self.round_wind)

        self.round_number = QComboBox()
        self.round_number.addItems(["1", "2", "3", "4"])
        self.round_number.setCurrentText(str(self.base.get("round_number") or "1"))
        winds_layout.addWidget(QLabel("局数:"))
        winds_layout.addWidget(self.round_number)

        self.seat_wind = QComboBox()
        self.seat_wind.addItems(["E", "S", "W", "N"])
        self.seat_wind.setCurrentText(self.base.get("seat_wind", "E"))
        winds_layout.addWidget(QLabel("自风:"))
        winds_layout.addWidget(self.seat_wind)
        winds_layout.addStretch()
        form.addRow("局况风位:", winds_layout)

        riichi_row = QWidget()
        riichi_layout = QHBoxLayout(riichi_row)
        riichi_layout.setContentsMargins(0, 0, 0, 0)
        riichi_layout.setSpacing(14)
        self.riichi_checks: list[QCheckBox] = []
        for i in range(4):
            check = QCheckBox()
            known = (list(self.base.get("riichi", [])) + [False] * 4)[i]
            check.setChecked(bool(known))
            riichi_layout.addWidget(check)
            self.riichi_checks.append(check)
        riichi_layout.addStretch()
        form.addRow("立直状态:", riichi_row)

        def update_player_fields():
            count = self.player_count.currentData()
            for seat, check in enumerate(self.riichi_checks):
                check.setVisible(seat < count)
                check.setText(f"玩家 {seat + 1}" + ("（自家）" if seat == self.base.get("seat", 0) else ""))
            old_round = self.round_number.currentText()
            self.round_number.clear()
            self.round_number.addItems([str(i + 1) for i in range(count)])
            self.round_number.setCurrentText(old_round if old_round in [str(i + 1) for i in range(count)] else "1")
            old_wind = self.seat_wind.currentText()
            self.seat_wind.clear()
            self.seat_wind.addItems(list("ESWN"[:count]))
            self.seat_wind.setCurrentText(old_wind if old_wind in "ESWN"[:count] else "E")
        self.player_count.currentIndexChanged.connect(update_player_fields)
        update_player_fields()
        quick_layout.addLayout(form)

        self.learn_and_resume = QCheckBox("应用本次手牌后继续保持实时协议监听")
        self.learn_and_resume.setChecked(True)
        self.learn_and_resume.setToolTip("后续收到雀魂协议事件时将自动更新实时牌面")
        quick_layout.addWidget(self.learn_and_resume)
        self.tabs.addTab(quick, "快速输入")

        self.editor = QTextEdit()
        self.editor.setObjectName("terminalOutput")
        self.editor.setPlainText(json.dumps(self.base, ensure_ascii=False, indent=2))
        self.tabs.addTab(self.editor, "高级 JSON")
        layout.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("确认并应用")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.state: GameState | None = None
        self.resize(640, 500)

    def _validate(self) -> None:
        try:
            if self.tabs.currentIndex() == 0:
                if not self.hand.text().strip():
                    raise ValueError("请填写当前手牌")
                data = dict(self.base)
                count = self.player_count.currentData()
                data["player_count"] = count
                if int(data.get("seat", 0)) >= count:
                    raise ValueError("当前座位不适用于三人麻将，请在高级 JSON 中核对座位")
                for field, default in (("rivers", []), ("melds", []), ("scores", None), ("nuki", 0)):
                    values = list(data.get(field, []))
                    if field != "scores" and any(values[count:]):
                        raise ValueError("被移除玩家仍有数据，请在高级 JSON 中核对后切换玩法")
                    data[field] = (values + [default] * count)[:count]
                data.update({
                    "hand": self.hand.text().strip(),
                    "dora_indicators": self.dora.text().strip(),
                    "buttons": sorted(parse_buttons(self.action_buttons.text())),
                    "last_discard": self.last_discard.text().strip() or None,
                    "open_melds": self.open_melds.value(),
                    "round_wind": self.round_wind.currentText(),
                    "round_number": int(self.round_number.currentText()),
                    "seat_wind": self.seat_wind.currentText(),
                    "riichi": [x.isChecked() for x in self.riichi_checks[:count]],
                    "observation_confidence": 1.0,
                })
            else:
                data = json.loads(self.editor.toPlainText())
            self.state = GameState.from_dict(data)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            QMessageBox.warning(self, "状态无效", str(error))
            return
        self.accept()


class _LLMSignals(QObject):
    done = Signal(str)
    failed = Signal(str)


class _LLMWorker(QRunnable):
    def __init__(self, state: GameState, candidate: Candidate, settings: Settings):
        super().__init__()
        self.state, self.candidate, self.settings = state, candidate, settings
        self.signals = _LLMSignals()

    def run(self) -> None:
        try:
            from .llm import query_llm_analysis
            text = query_llm_analysis(self.state, self.candidate, self.settings)
            self.signals.done.emit(text)
        except Exception as error:
            self.signals.failed.emit(str(error))


class LLMAnalysisDialog(QDialog):
    """Interactive dialog presenting deep LLM tactical commentary."""

    def __init__(
        self,
        state: GameState,
        candidate: Candidate,
        settings: Settings,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.state = state
        self.candidate = candidate
        self.settings = settings
        self._worker: _LLMWorker | None = None
        self.setWindowTitle("AI 大模型战术牌理解析")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # Header card
        model_name = settings.llm_model.strip() or "deepseek-chat"
        action_desc = f"推荐动作：{candidate.label}  ·  有效牌约 {candidate.ukeire or 0} 张  ·  危险级 {candidate.danger or 0}"
        banner = make_dialog_header(
            title=f"AI 牌理深度推演 · {model_name}",
            subtitle=action_desc,
            badge_text="战术决策",
        )
        layout.addWidget(banner)

        # Terminal Container Card
        term_frame = QFrame()
        term_frame.setObjectName("terminalContainer")
        term_frame.setStyleSheet(
            "QFrame#terminalContainer { background: #070c16; border: 1.2px solid rgba(56, 189, 248, 0.4); "
            "border-radius: 8px; }"
        )
        term_layout = QVBoxLayout(term_frame)
        term_layout.setContentsMargins(0, 0, 0, 0)
        term_layout.setSpacing(0)

        # Header strip inside terminal
        header_strip = QFrame()
        header_strip.setStyleSheet(
            "background: rgba(15, 23, 42, 0.85); border-bottom: 1px solid rgba(56, 189, 248, 0.25); "
            "border-top-left-radius: 7px; border-top-right-radius: 7px; padding: 5px 12px;"
        )
        strip_l = QHBoxLayout(header_strip)
        strip_l.setContentsMargins(4, 2, 4, 2)
        lbl_strip = QLabel("TERMINAL OUTPUT // AI TACTICAL REASONING TRACE")
        lbl_strip.setStyleSheet("color: #38bdf8; font-size: 10px; font-weight: 700; font-family: monospace;")
        strip_l.addWidget(lbl_strip)
        strip_l.addStretch()
        term_layout.addWidget(header_strip)

        # Content area
        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setStyleSheet(
            "background: transparent; border: none; padding: 12px; font-size: 13px; line-height: 1.6; color: #f1f5f9;"
        )
        self.text_area.setPlaceholderText("正在连线大模型进行战术推演，请稍候...")
        term_layout.addWidget(self.text_area, 1)

        layout.addWidget(term_frame, 1)

        # Status / progress
        status_bar = QHBoxLayout()
        status_bar.setSpacing(6)
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #38bdf8; font-size: 10px;")
        status_bar.addWidget(self.status_dot)
        self.status_label = QLabel("正在分析局面中...")
        self.status_label.setObjectName("dialogSubHeader")
        status_bar.addWidget(self.status_label)
        status_bar.addStretch()
        layout.addLayout(status_bar)

        # Buttons
        btn_box = QHBoxLayout()
        btn_box.setSpacing(10)
        self.btn_copy = QPushButton("复制分析")
        self.btn_copy.setEnabled(False)
        self.btn_copy.clicked.connect(self._copy_text)
        btn_box.addWidget(self.btn_copy)

        self.btn_retry = QPushButton("重新分析")
        self.btn_retry.clicked.connect(self._start_query)
        btn_box.addWidget(self.btn_retry)

        btn_box.addStretch()

        self.btn_close = QPushButton("关闭")
        self.btn_close.setProperty("primary", True)
        self.btn_close.clicked.connect(self.accept)
        btn_box.addWidget(self.btn_close)
        layout.addLayout(btn_box)

        self.resize(580, 500)
        self._start_query()

    def _start_query(self) -> None:
        self.text_area.clear()
        self.status_dot.setStyleSheet("color: #38bdf8; font-size: 10px;")
        self.status_label.setText(f"正在请求 {self.settings.llm_model} 深度推演...")
        self.btn_retry.setEnabled(False)
        self.btn_copy.setEnabled(False)

        worker = _LLMWorker(self.state, self.candidate, self.settings)
        self._worker = worker
        worker.signals.done.connect(self._on_done)
        worker.signals.failed.connect(self._on_failed)
        QThreadPool.globalInstance().start(worker)

    def _on_done(self, text: str) -> None:
        self.text_area.setPlainText(text)
        self.status_dot.setStyleSheet("color: #34d399; font-size: 10px;")
        self.status_label.setText("推演完成。")
        self.btn_retry.setEnabled(True)
        self.btn_copy.setEnabled(True)

    def _on_failed(self, error: str) -> None:
        self.text_area.setPlainText(f"大模型解析失败：\n{error}")
        self.status_dot.setStyleSheet("color: #f87171; font-size: 10px;")
        self.status_label.setText("推演异常，请检查网络或 API Key 设置。")
        self.btn_retry.setEnabled(True)

    def _copy_text(self) -> None:
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self.text_area.toPlainText())
            self.status_label.setText("已成功复制分析文本到剪贴板。")
