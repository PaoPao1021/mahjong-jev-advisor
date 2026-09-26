"""Dialogs and utility windows for Mahjong Jev Advisor.

Contains:
- SelectionCanvas & RegionDialog for table/region calibrations
- TemplateDialog for labeling mahjong tiles
- JevSettingsDialog & LLMSettingsDialog
- ManualStateDialog for manual verification and rapid tile input
"""

from __future__ import annotations

import json
from typing import Any
import cv2
import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from .settings import Settings
from .state import GameState
from .vision import REGIONS, TEMPLATE_LABELS, TemplateStore, parse_buttons, region_cells


def pixmap_from_bgr(image: np.ndarray) -> QPixmap:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    qimage = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(qimage.copy())


class SelectionCanvas(QWidget):
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
        painter.drawPixmap(0, 0, self.pixmap)
        if self.start is not None and self.end is not None:
            rect = QRect(self.start, self.end).normalized()
            painter.fillRect(rect, QColor(56, 189, 248, 60))
            painter.setPen(QPen(QColor(56, 189, 248), 2))
            painter.drawRect(rect)

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
        info = QLabel(f"拖动鼠标框选：{title}")
        info.setObjectName("dialogHeader")
        layout.addWidget(info)

        self.scale = min(1.0, 1250 / image.shape[1], 740 / image.shape[0])
        display = cv2.resize(image, None, fx=self.scale, fy=self.scale) if self.scale < 1 else image
        self.canvas = SelectionCanvas(display)
        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setWidgetResizable(False)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept_if_selected)
        buttons.rejected.connect(self.reject)
        if optional:
            skip = buttons.addButton("跳过", QDialogButtonBox.ButtonRole.ActionRole)
            skip.clicked.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(min(display.shape[1] + 40, 1300), min(display.shape[0] + 110, 820))

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
        root.addWidget(QLabel("给未识别的牌、空白格标注一次；以后同样画面会自动匹配。建议逐局补充样本。"))
        scroll = QScrollArea()
        body = QWidget()
        body_layout = QVBoxLayout(body)
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
                line = QHBoxLayout()
                line.addWidget(QLabel(f"{title} #{number + 1}"))
                preview = QLabel()
                preview.setPixmap(pixmap_from_bgr(cell).scaled(48, 64, Qt.AspectRatioMode.KeepAspectRatio))
                line.addWidget(preview)
                choice = QComboBox()
                choice.addItems(["跳过"] + list(TEMPLATE_LABELS))
                if matched.label:
                    choice.setCurrentText(matched.label)
                line.addWidget(choice)
                body_layout.addLayout(line)
                self.rows.append((choice, cell))
                count += 1
                if count >= 100:
                    break
            if count >= 100:
                break
        body_layout.addStretch()
        scroll.setWidget(body)
        scroll.setWidgetResizable(True)
        root.addWidget(scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.resize(520, 700)

    def _save(self) -> None:
        saved = 0
        for choice, cell in self.rows:
            label = choice.currentText()
            if label != "跳过":
                self.store.add(label, cell)
                saved += 1
        QMessageBox.information(self, "样本已保存", f"保存了 {saved} 个牌面样本。")
        self.accept()


class JevSettingsDialog(QDialog):
    """Jev API Key & system settings dialog."""

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("TypeSafe Jev 设置")
        form = QFormLayout(self)
        form.setSpacing(10)

        header = QLabel("配置 TypeSafe Jev 实时决策模型")
        header.setObjectName("dialogHeader")
        form.addRow(header)

        desc = QLabel("Jev 专精于日麻巡目攻守效率与点数期望决策。")
        desc.setObjectName("dialogTip")
        form.addRow(desc)

        self.key = QLineEdit(settings.api_key)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setMinimumWidth(320)
        self.key.setPlaceholderText("粘贴 TypeSafe API Key")
        form.addRow("TypeSafe API Key:", self.key)

        self.log = QCheckBox("保存决策 JSONL 日志（不保存任何截图）")
        self.log.setChecked(settings.logging_enabled)
        form.addRow(self.log)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存配置")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.setMinimumWidth(480)


class LLMSettingsDialog(QDialog):
    """General LLM API configuration (DeepSeek, OpenAI, Claude, Qwen, etc.)."""

    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("大模型设置")
        form = QFormLayout(self)
        form.setSpacing(10)

        header = QLabel("配置通用大模型（用于牌理复盘与局况解析）")
        header.setObjectName("dialogHeader")
        form.addRow(header)

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

        tips = QLabel("提示：支持所有 OpenAI 兼容格式接口，可用于复杂局况深度推演与复盘。")
        tips.setObjectName("dialogTip")
        tips.setWordWrap(True)
        form.addRow(tips)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.setMinimumWidth(500)


class ManualStateDialog(QDialog):
    """Rapid manual state input & inspection dialog."""

    def __init__(self, state: GameState | None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("手动核对手牌与局面")
        layout = QVBoxLayout(self)

        self.base = state.to_dict() if state else {
            "hand": [], "rivers": [[], [], [], []], "melds": [[], [], [], []],
            "dora_indicators": [], "buttons": [], "seat": 0,
            "round_wind": "E", "round_number": 1, "seat_wind": "E", "scores": [25000] * 4,
            "riichi": [False] * 4, "open_melds": 0,
        }

        self.tabs = QTabWidget()
        quick = QWidget()
        form = QFormLayout(quick)
        form.setSpacing(8)

        notice = QLabel("牌码记法：m 万、p 筒、s 索、z 字牌(1-7 东南西北白发中)；0m/0p/0s 为赤五。")
        notice.setObjectName("dialogSubHeader")
        form.addRow(notice)

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

        self.round_wind = QComboBox()
        self.round_wind.addItems(["E", "S", "W", "N"])
        self.round_wind.setCurrentText(self.base.get("round_wind", "E"))
        form.addRow("场风 (E东/S南):", self.round_wind)

        self.round_number = QComboBox()
        self.round_number.addItems(["1", "2", "3", "4"])
        self.round_number.setCurrentText(str(self.base.get("round_number") or "1"))
        form.addRow("局数:", self.round_number)

        self.seat_wind = QComboBox()
        self.seat_wind.addItems(["E", "S", "W", "N"])
        self.seat_wind.setCurrentText(self.base.get("seat_wind", "E"))
        form.addRow("自风:", self.seat_wind)

        riichi_row = QWidget()
        riichi_layout = QHBoxLayout(riichi_row)
        riichi_layout.setContentsMargins(0, 0, 0, 0)
        self.riichi_checks: list[QCheckBox] = []
        for i, title in enumerate(("我", "下家", "对家", "上家")):
            check = QCheckBox(title)
            known = self.base.get("riichi", [False] * 4)[i]
            check.setChecked(bool(known))
            riichi_layout.addWidget(check)
            self.riichi_checks.append(check)
        form.addRow("立直状态:", riichi_row)

        self.tabs.addTab(quick, "快速输入")

        self.editor = QTextEdit()
        self.editor.setPlainText(json.dumps(self.base, ensure_ascii=False, indent=2))
        self.tabs.addTab(self.editor, "高级 JSON")
        layout.addWidget(self.tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("确认并应用")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.state: GameState | None = None
        self.resize(620, 460)

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


from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool


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
        self.setWindowTitle("AI 大模型战术牌理解析")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header card
        header_box = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        model_name = settings.llm_model.strip() or "deepseek-chat"
        lbl_title = QLabel(f"牌理深度推演 · {model_name}")
        lbl_title.setObjectName("dialogHeader")
        title_col.addWidget(lbl_title)

        action_desc = f"推荐动作：{candidate.label}  ·  有效牌约 {candidate.ukeire or 0} 张  ·  危险级 {candidate.danger or 0}"
        lbl_sub = QLabel(action_desc)
        lbl_sub.setObjectName("dialogSubHeader")
        title_col.addWidget(lbl_sub)
        header_box.addLayout(title_col)

        header_box.addStretch()
        layout.addLayout(header_box)

        # Content area
        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setStyleSheet(
            "background: rgba(10, 14, 25, 0.90); border: 1.5px solid rgba(56, 189, 248, 0.4); "
            "border-radius: 8px; padding: 12px; font-size: 13px; line-height: 1.6; color: #f1f5f9;"
        )
        self.text_area.setPlaceholderText("正在连线大模型进行战术推演，请稍候...")
        layout.addWidget(self.text_area, 1)

        # Status / progress
        self.status_label = QLabel("正在分析局面中...")
        self.status_label.setObjectName("dialogSubHeader")
        layout.addWidget(self.status_label)

        # Buttons
        btn_box = QHBoxLayout()
        self.btn_copy = QPushButton("复制分析")
        self.btn_copy.setEnabled(False)
        self.btn_copy.clicked.connect(self._copy_text)
        btn_box.addWidget(self.btn_copy)

        self.btn_retry = QPushButton("重新分析")
        self.btn_retry.clicked.connect(self._start_query)
        btn_box.addWidget(self.btn_retry)

        btn_box.addStretch()

        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.accept)
        btn_box.addWidget(self.btn_close)
        layout.addLayout(btn_box)

        self.resize(560, 480)
        self._start_query()

    def _start_query(self) -> None:
        self.text_area.clear()
        self.status_label.setText(f"正在请求 {self.settings.llm_model} 深度推演...")
        self.btn_retry.setEnabled(False)
        self.btn_copy.setEnabled(False)

        worker = _LLMWorker(self.state, self.candidate, self.settings)
        worker.signals.done.connect(self._on_done)
        worker.signals.failed.connect(self._on_failed)
        QThreadPool.globalInstance().start(worker)

    def _on_done(self, text: str) -> None:
        self.text_area.setPlainText(text)
        self.status_label.setText("分析完成。")
        self.btn_retry.setEnabled(True)
        self.btn_copy.setEnabled(True)

    def _on_failed(self, error: str) -> None:
        self.text_area.setPlainText(f"大模型解析失败：\n{error}")
        self.status_label.setText("推演异常，请检查网络或 API Key 设置。")
        self.btn_retry.setEnabled(True)

    def _copy_text(self) -> None:
        from PySide6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self.text_area.toPlainText())
            self.status_label.setText("已成功复制分析文本到剪贴板。")

