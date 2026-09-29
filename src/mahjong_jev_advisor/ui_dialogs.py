"""Dialogs and utility windows for Mahjong Jev Advisor.

Contains:
- SelectionCanvas & RegionDialog for table/region calibrations
- TemplateDialog for labeling mahjong tiles
- JevSettingsDialog & LLMSettingsDialog
- ManualStateDialog for manual verification and rapid tile input
- LLMAnalysisDialog for deep AI tactical commentary
"""

from __future__ import annotations

import json
from typing import Any
import cv2
import numpy as np
from PySide6.QtCore import QObject, QPoint, QRect, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QDoubleSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from .settings import Settings
from .state import Candidate, GameState
from .vision import REGIONS, TEMPLATE_LABELS, TemplateStore, parse_buttons, region_cells


def pixmap_from_bgr(image: np.ndarray) -> QPixmap:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    qimage = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimage.copy())


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


class SelectionCanvas(QWidget):
    """Interactive screen calibration canvas with high-tech tactical reticle."""

    def __init__(self, image: np.ndarray, parent: QWidget | None = None):
        super().__init__(parent)
        self.image = image
        self.pixmap = pixmap_from_bgr(image)
        self.setFixedSize(self.pixmap.size())
        self.start: QPoint | None = None
        self.end: QPoint | None = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.start = event.position().toPoint()
            self.end = self.start
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.start is not None:
            self.end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.start is not None:
            self.end = event.position().toPoint()
            self.update()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(0, 0, self.pixmap)

        if self.start is not None and self.end is not None:
            rect = QRect(self.start, self.end).normalized()
            # 1. Darken exterior unselected areas for focus
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 110))
            if rect.top() > 0:
                painter.drawRect(0, 0, self.width(), rect.top())
            if rect.bottom() < self.height():
                painter.drawRect(0, rect.bottom() + 1, self.width(), self.height() - rect.bottom() - 1)
            painter.drawRect(0, rect.top(), rect.left(), rect.height() + 1)
            painter.drawRect(rect.right() + 1, rect.top(), self.width() - rect.right() - 1, rect.height() + 1)

            # 2. Glowing target interior
            painter.setBrush(QColor(56, 189, 248, 25))
            painter.drawRect(rect)

            # 3. Main reticle border
            painter.setPen(QPen(QColor(56, 189, 248, 220), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

            # 4. Tactical corner brackets (emerald luminous)
            bracket_len = min(14, rect.width() // 3, rect.height() // 3)
            if bracket_len >= 4:
                painter.setPen(QPen(QColor(52, 211, 153), 2.5))
                # Top-Left
                painter.drawLine(rect.left(), rect.top(), rect.left() + bracket_len, rect.top())
                painter.drawLine(rect.left(), rect.top(), rect.left(), rect.top() + bracket_len)
                # Top-Right
                painter.drawLine(rect.right(), rect.top(), rect.right() - bracket_len, rect.top())
                painter.drawLine(rect.right(), rect.top(), rect.right(), rect.top() + bracket_len)
                # Bottom-Left
                painter.drawLine(rect.left(), rect.bottom(), rect.left() + bracket_len, rect.bottom())
                painter.drawLine(rect.left(), rect.bottom(), rect.left(), rect.bottom() - bracket_len)
                # Bottom-Right
                painter.drawLine(rect.right(), rect.bottom(), rect.right() - bracket_len, rect.bottom())
                painter.drawLine(rect.right(), rect.bottom(), rect.right(), rect.bottom() - bracket_len)

            # 5. Dimension pill badge
            w, h = rect.width(), rect.height()
            dim_str = f" {w} × {h} px "
            font = QFont("Segoe UI", 9)
            font.setBold(True)
            painter.setFont(font)
            metrics = painter.fontMetrics()
            tw = metrics.horizontalAdvance(dim_str) + 8
            th = metrics.height() + 4
            bx = rect.right() - tw
            by = rect.bottom() + 6
            if by + th > self.height():
                by = rect.top() - th - 6
            if bx < 0:
                bx = rect.left()
            painter.setPen(QPen(QColor(56, 189, 248, 180), 1))
            painter.setBrush(QColor(10, 15, 26, 220))
            painter.drawRoundedRect(bx, by, tw, th, 4, 4)
            painter.setPen(QColor(224, 242, 254))
            painter.drawText(bx, by, tw, th, Qt.AlignmentFlag.AlignCenter, dim_str)

    def selected(self) -> tuple[int, int, int, int] | None:
        if self.start is None or self.end is None:
            return None
        rect = QRect(self.start, self.end).normalized()
        if rect.width() < 8 or rect.height() < 8:
            return None
        return rect.x(), rect.y(), rect.width(), rect.height()


class RegionDialog(QDialog):
    def __init__(self, image: np.ndarray, title: str, optional: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        banner = make_dialog_header(
            title=f"区域校准 · {title}",
            subtitle="按住鼠标左键在画面中拖拽框选，完成后点击“确定保存”",
            badge_text="视觉标定",
        )
        layout.addWidget(banner)

        self.scale = min(1.0, 1250 / image.shape[1], 740 / image.shape[0])
        display = cv2.resize(image, None, fx=self.scale, fy=self.scale) if self.scale < 1 else image
        self.canvas = SelectionCanvas(display)
        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setWidgetResizable(False)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept_if_selected)
        buttons.rejected.connect(self.reject)
        if optional:
            skip = buttons.addButton("跳过", QDialogButtonBox.ButtonRole.ActionRole)
            skip.clicked.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(min(display.shape[1] + 40, 1300), min(display.shape[0] + 130, 840))

    def _accept_if_selected(self) -> None:
        if not self.canvas.selected():
            QMessageBox.information(self, "请框选", "请先在画面上拖动，框选对应区域。")
            return
        self.accept()

    def selected_original(self) -> tuple[int, int, int, int] | None:
        box = self.canvas.selected()
        if box is None:
            return None
        return tuple(round(value / self.scale) for value in box)


class TemplateDialog(QDialog):
    def __init__(self, frame: np.ndarray, settings: Settings, store: TemplateStore, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("标注牌面样本")
        self.store = store
        self.rows: list[tuple[QComboBox, np.ndarray]] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        banner = make_dialog_header(
            title="标注牌面样本与模板库校准",
            subtitle="为未识别的牌面或空白格标注标签，系统将自动增量学习并持久化模板",
            badge_text="增量学习",
        )
        root.addWidget(banner)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(8, 8, 8, 8)
        body_layout.setSpacing(6)
        count = 0
        template_regions = sorted(
            REGIONS,
            key=lambda item: {"hand": 0, "draw": 1, "dora": 2}.get(item[0], 3),
        )
        for name, title, cols, rows, _ in template_regions:
            if name not in settings.regions or name.startswith(("score", "riichi")) or name in ("buttons", "round"):
                continue
            for number, cell in enumerate(region_cells(frame, settings.regions[name], cols, rows)):
                if cell.size == 0:
                    continue
                matched = store.match(cell)
                if matched.label and matched.confidence >= 0.94:
                    continue

                row_card = QFrame()
                row_card.setObjectName("tileSampleCard")
                row_card.setStyleSheet(
                    "QFrame#tileSampleCard { background: rgba(18, 25, 41, 0.7); "
                    "border: 1px solid rgba(56, 72, 100, 0.45); border-radius: 6px; padding: 4px; }"
                )
                line = QHBoxLayout(row_card)
                line.setContentsMargins(8, 4, 8, 4)
                line.setSpacing(10)

                lbl = QLabel(f"{title} #{number + 1}")
                lbl.setStyleSheet("color: #e2e8f0; font-size: 11px; font-weight: 600; min-width: 80px;")
                line.addWidget(lbl)

                preview = QLabel()
                preview.setStyleSheet("border: 1px solid rgba(56, 189, 248, 0.4); border-radius: 4px; background: #060911;")
                preview.setPixmap(pixmap_from_bgr(cell).scaled(42, 56, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                line.addWidget(preview)

                choice = QComboBox()
                choice.addItems(["跳过"] + list(TEMPLATE_LABELS))
                if matched.label:
                    choice.setCurrentText(matched.label)
                line.addWidget(choice, 1)

                body_layout.addWidget(row_card)
                self.rows.append((choice, cell))
                count += 1
                if count >= 100:
                    break
            if count >= 100:
                break
        body_layout.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存样本")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.resize(520, 680)

    def _save(self) -> None:
        saved = 0
        for choice, cell in self.rows:
            label = choice.currentText()
            if label != "跳过":
                self.store.add(label, cell)
                saved += 1
        QMessageBox.information(self, "样本已保存", f"保存了 {saved} 个牌面样本。")
        self.accept()


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
        for i, title in enumerate(("我 (自家)", "下家", "对家", "上家")):
            check = QCheckBox(title)
            known = self.base.get("riichi", [False] * 4)[i]
            check.setChecked(bool(known))
            riichi_layout.addWidget(check)
            self.riichi_checks.append(check)
        riichi_layout.addStretch()
        form.addRow("立直状态:", riichi_row)

        quick_layout.addLayout(form)

        self.learn_and_resume = QCheckBox("用本次核对自动学习当前牌面，并恢复实时监测")
        self.learn_and_resume.setChecked(True)
        self.learn_and_resume.setToolTip("只在本机保存牌面小图模板，不保存完整截图")
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
                data.update({
                    "hand": self.hand.text().strip(),
                    "dora_indicators": self.dora.text().strip(),
                    "buttons": sorted(parse_buttons(self.action_buttons.text())),
                    "last_discard": self.last_discard.text().strip() or None,
                    "open_melds": self.open_melds.value(),
                    "round_wind": self.round_wind.currentText(),
                    "round_number": int(self.round_number.currentText()),
                    "seat_wind": self.seat_wind.currentText(),
                    "riichi": [x.isChecked() for x in self.riichi_checks],
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
