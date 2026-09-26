"""Cyberpunk HUD UI components for Mahjong Jev Advisor.

Implements the high-density floating advisor layout matching the reference screenshot:
- Dark glassmorphic frameless HUD
- Four-player table status with score, riichi badge, meld count, and compact rivers
- Hand tile strip with dedicated draw slot and glowing discard highlights
- Decision hero card with recommendation tile, shanten badge, probability bar charts, and data pills
- Action bar with quick controls
"""

from __future__ import annotations

from typing import Any, Callable
from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QMouseEvent, QPainter, QPen,
)
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton,
    QScrollArea, QSizePolicy, QToolTip, QVBoxLayout, QWidget,
)

from .tiles import normal
from .ui_tiles import CompactTileBadge, MahjongTileWidget


class DragBar(QWidget):
    """Allows dragging the parent window from anywhere on this bar."""

    def __init__(self, owner: QWidget, parent: QWidget | None = None):
        super().__init__(parent or owner)
        self.owner = owner
        self.origin: QPoint | None = None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.globalPosition().toPoint() - self.owner.pos()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.owner.move(event.globalPosition().toPoint() - self.origin)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.origin = None


class HUDTitleBar(QWidget):
    """Top bar matching screenshot: '雀魂牌面', status pill '对局中', AI/桌/min/close buttons."""

    ai_clicked = Signal()
    table_clicked = Signal()
    minimize_clicked = Signal()
    close_clicked = Signal()

    def __init__(self, owner: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.owner = owner
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 6)
        layout.setSpacing(8)

        # Draggable title area
        self.drag_area = DragBar(owner, self)
        drag_layout = QHBoxLayout(self.drag_area)
        drag_layout.setContentsMargins(0, 0, 0, 0)
        drag_layout.setSpacing(6)

        self.title_label = QLabel("雀魂牌面")
        self.title_label.setObjectName("appTitle")
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        drag_layout.addWidget(self.title_label)

        drag_layout.addStretch()
        layout.addWidget(self.drag_area, 1)

        # Status capsule e.g. 【对局中】 / 【待机中】
        self.status_pill = QLabel("对局中")
        self.status_pill.setObjectName("statusPill")
        layout.addWidget(self.status_pill)

        # Action buttons: AI, 桌, -, x
        self.btn_ai = QPushButton("AI")
        self.btn_ai.setObjectName("headerPillBtn")
        self.btn_ai.setToolTip("AI建议 / Jev设置 / 大模型设置")
        self.btn_ai.clicked.connect(self.ai_clicked.emit)
        layout.addWidget(self.btn_ai)

        self.btn_table = QPushButton("桌")
        self.btn_table.setObjectName("headerPillBtn")
        self.btn_table.setToolTip("牌桌选择与校准工具")
        self.btn_table.clicked.connect(self.table_clicked.emit)
        layout.addWidget(self.btn_table)

        self.btn_min = QPushButton("−")
        self.btn_min.setObjectName("headerPillBtn")
        self.btn_min.setToolTip("最小化")
        self.btn_min.clicked.connect(self.minimize_clicked.emit)
        layout.addWidget(self.btn_min)

        self.btn_close = QPushButton("×")
        self.btn_close.setObjectName("headerCloseBtn")
        self.btn_close.setToolTip("关闭")
        self.btn_close.clicked.connect(self.close_clicked.emit)
        layout.addWidget(self.btn_close)

    def set_status(self, text: str, state: str = "active") -> None:
        """state: 'active' (green), 'warning' (amber), 'idle' (gray/blue)"""
        self.status_pill.setText(text)
        if state == "active":
            self.status_pill.setStyleSheet(
                "color: #10b981; background: rgba(16, 185, 129, 0.18); border: 1px solid #059669; "
                "border-radius: 10px; padding: 2px 10px; font-weight: bold; font-size: 11px;"
            )
        elif state == "warning":
            self.status_pill.setStyleSheet(
                "color: #fbbf24; background: rgba(245, 158, 11, 0.18); border: 1px solid #d97706; "
                "border-radius: 10px; padding: 2px 10px; font-weight: bold; font-size: 11px;"
            )
        else:
            self.status_pill.setStyleSheet(
                "color: #94a3b8; background: rgba(71, 85, 105, 0.25); border: 1px solid #475569; "
                "border-radius: 10px; padding: 2px 10px; font-weight: bold; font-size: 11px;"
            )


class RoundInfoBar(QWidget):
    """Match screenshot: '东1局 · 余 29 · 3北', right side '宝 [🀄]'."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 4, 14, 6)
        layout.setSpacing(6)

        self.round_text = QLabel("东1局  ·  余 29  ·  3北")
        self.round_text.setObjectName("roundInfoText")
        layout.addWidget(self.round_text)

        layout.addStretch()

        # Dora label + mini tile
        dora_box = QHBoxLayout()
        dora_box.setSpacing(4)
        lbl = QLabel("宝")
        lbl.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: bold;")
        dora_box.addWidget(lbl)

        self.dora_tile = MahjongTileWidget(tile="", width=17, height=23, show_code=False)
        dora_box.addWidget(self.dora_tile)
        layout.addLayout(dora_box)

    def set_round_info(
        self,
        round_wind: str = "E",
        round_num: int | None = 1,
        remaining_tiles: int | None = 29,
        seat_desc: str = "3北",
        honba: int = 0,
        sticks: int = 0,
        dora_indicator: str = "",
    ) -> None:
        winds = {"E": "东", "S": "南", "W": "西", "N": "北", "?": "东"}
        wind_zh = winds.get(round_wind, "东")
        num_zh = f"{round_num}" if round_num is not None else "1"
        round_str = f"{wind_zh}{num_zh}局"

        rem_str = f"余 {remaining_tiles}" if remaining_tiles is not None else "对局中"
        seat_str = seat_desc or (f"本场 {honba}" if honba > 0 else "")

        parts = [round_str, rem_str]
        if seat_str:
            parts.append(seat_str)
        if sticks > 0:
            parts.append(f"供托{sticks}")

        self.round_text.setText("  ·  ".join(parts))
        self.dora_tile.set_tile(dora_indicator)


class PlayerRiverRow(QWidget):
    """Single player row: Golden wind badge, score, meld tag, compact river tiles."""

    def __init__(self, wind_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(6)

        # Wind & Riichi indicator
        left_box = QHBoxLayout()
        left_box.setSpacing(3)
        self.wind_label = QLabel(wind_name)
        self.wind_label.setObjectName("playerWindBadge")
        left_box.addWidget(self.wind_label)

        self.riichi_badge = QLabel("立")
        self.riichi_badge.setObjectName("riichiBadge")
        self.riichi_badge.hide()
        left_box.addWidget(self.riichi_badge)
        layout.addLayout(left_box)

        # Score & Melds info
        info_col = QVBoxLayout()
        info_col.setContentsMargins(0, 0, 0, 0)
        info_col.setSpacing(1)

        self.score_label = QLabel("25,000")
        self.score_label.setObjectName("playerScore")
        info_col.addWidget(self.score_label)

        self.meld_label = QLabel("门清")
        self.meld_label.setObjectName("playerMelds")
        info_col.addWidget(self.meld_label)
        layout.addLayout(info_col)

        # River compact tiles strip
        self.river_container = QWidget()
        self.river_layout = QHBoxLayout(self.river_container)
        self.river_layout.setContentsMargins(4, 0, 0, 0)
        self.river_layout.setSpacing(3)
        self.river_layout.addStretch()
        layout.addWidget(self.river_container, 1)

        self._all_tiles: list[str] = []

    def update_row(
        self,
        score: int | None,
        is_riichi: bool = False,
        melds_desc: str = "",
        river: list[str] | tuple[str, ...] = (),
    ) -> None:
        self.score_label.setText(f"{score:,}" if score is not None else "—")
        self.riichi_badge.setVisible(bool(is_riichi))
        self.meld_label.setText(melds_desc if melds_desc else ("副露" if melds_desc else "门清"))

        # Clear river widgets
        while self.river_layout.count() > 1:
            child = self.river_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        self._all_tiles = list(river)
        # Show recent up to 8 tiles, then +N
        max_show = 8
        if len(self._all_tiles) <= max_show:
            for t in self._all_tiles:
                badge = CompactTileBadge(t)
                self.river_layout.insertWidget(self.river_layout.count() - 1, badge)
        else:
            for t in self._all_tiles[:max_show]:
                badge = CompactTileBadge(t)
                self.river_layout.insertWidget(self.river_layout.count() - 1, badge)
            plus_lbl = QLabel(f"+{len(self._all_tiles) - max_show}")
            plus_lbl.setStyleSheet("color: #64748b; font-size: 10px; font-weight: bold; padding: 2px;")
            plus_lbl.setToolTip(" ".join(self._all_tiles))
            self.river_layout.insertWidget(self.river_layout.count() - 1, plus_lbl)


class FourPlayersRiverWidget(QFrame):
    """The 4-player table card with East, South, West, North rows."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("tableCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        self.rows: list[PlayerRiverRow] = []
        winds = ["东", "南", "西", "北"]
        for w in winds:
            row = PlayerRiverRow(w, self)
            self.rows.append(row)
            layout.addWidget(row)

    def update_table(
        self,
        scores: tuple[int | None, ...] | list[int | None],
        riichi: tuple[bool | None, ...] | list[bool | None],
        rivers: tuple[tuple[str, ...], ...] | list[list[str]],
        melds: tuple[tuple[str, ...], ...] | list[list[str]],
        my_seat: int = 0,
    ) -> None:
        for i in range(4):
            score = scores[i] if i < len(scores) else None
            is_riichi = bool(riichi[i]) if i < len(riichi) and riichi[i] is not None else False
            r_tiles = rivers[i] if i < len(rivers) else []
            m_list = melds[i] if i < len(melds) else []
            meld_text = f"副露×{len(m_list)}" if m_list else "门清"
            if i == my_seat:
                # Highlight current player wind
                self.rows[i].wind_label.setStyleSheet("color: #facc15; font-weight: 800; font-size: 13px;")
            else:
                self.rows[i].wind_label.setStyleSheet("color: #cbd5e1; font-weight: 700; font-size: 12px;")

            self.rows[i].update_row(score, is_riichi, meld_text, r_tiles)


class HandWidget(QWidget):
    """Hand section: Header '手牌 14' + toggle button, 13 hand tiles + draw slot with glow."""

    toggle_table = Signal()
    tile_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 6)
        layout.setSpacing(6)

        # Header: '手牌 14' and '隐藏四家' toggle button
        header = QHBoxLayout()
        self.count_label = QLabel("手牌 14")
        self.count_label.setObjectName("sectionTitle")
        header.addWidget(self.count_label)

        header.addStretch()

        self.btn_toggle = QPushButton("隐藏四家")
        self.btn_toggle.setObjectName("ghostPillBtn")
        self.btn_toggle.clicked.connect(self.toggle_table.emit)
        header.addWidget(self.btn_toggle)
        layout.addLayout(header)

        # Tiles strip container
        self.tiles_card = QFrame()
        self.tiles_card.setObjectName("tilesContainerCard")
        tiles_layout = QHBoxLayout(self.tiles_card)
        tiles_layout.setContentsMargins(6, 6, 6, 6)
        tiles_layout.setSpacing(2)

        # Hand tiles (up to 13)
        self.hand_slots: list[MahjongTileWidget] = []
        for _ in range(13):
            slot = MahjongTileWidget(tile="", width=23, height=33, show_code=True)
            slot.clicked.connect(self._on_tile_clicked)
            self.hand_slots.append(slot)
            tiles_layout.addWidget(slot)

        # Gap before drawn tile
        tiles_layout.addSpacing(10)

        # Draw tile container with '摸' indicator
        draw_col = QVBoxLayout()
        draw_col.setContentsMargins(0, 0, 0, 0)
        draw_col.setSpacing(1)

        draw_header = QHBoxLayout()
        draw_lbl = QLabel("摸")
        draw_lbl.setStyleSheet("color: #10b981; font-size: 9px; font-weight: bold;")
        draw_header.addWidget(draw_lbl)
        draw_col.addLayout(draw_header)

        self.draw_slot = MahjongTileWidget(tile="", width=23, height=33, show_code=True)
        self.draw_slot.clicked.connect(self._on_tile_clicked)
        draw_col.addWidget(self.draw_slot)
        tiles_layout.addLayout(draw_col)

        tiles_layout.addStretch()
        layout.addWidget(self.tiles_card)

    def _on_tile_clicked(self, tile: str) -> None:
        if tile:
            self.tile_clicked.emit(tile)

    def set_hand(self, tiles: tuple[str, ...] | list[str], highlight_tile: str | None = None) -> None:
        tile_list = list(tiles)
        total_count = len(tile_list)
        self.count_label.setText(f"手牌 {total_count}")

        # If 14 or (14 - 3*melds), last tile is the drawn tile
        has_draw = total_count in (14, 11, 8, 5, 2)
        hand_tiles = tile_list[:-1] if has_draw else tile_list
        draw_tile = tile_list[-1] if has_draw else ""

        norm_highlight = normal(highlight_tile) if highlight_tile else None

        # Populate hand slots
        for i, slot in enumerate(self.hand_slots):
            if i < len(hand_tiles):
                t = hand_tiles[i]
                slot.set_tile(t)
                is_glow = norm_highlight is not None and normal(t) == norm_highlight
                slot.set_glowing(is_glow)
                slot.show()
            else:
                slot.set_tile("")
                slot.set_glowing(False)
                slot.hide()

        # Populate drawn tile slot
        if draw_tile:
            self.draw_slot.set_tile(draw_tile)
            is_glow = norm_highlight is not None and normal(draw_tile) == norm_highlight
            self.draw_slot.set_glowing(is_glow)
            self.draw_slot.show()
        else:
            self.draw_slot.set_tile("")
            self.draw_slot.set_glowing(False)
            self.draw_slot.hide()


class ProbabilityBarItem(QWidget):
    """Single bar in the probability list e.g. '1p [========] 83%'."""

    def __init__(
        self,
        label: str,
        percent: int,
        is_top: bool = False,
        bar_color: QColor | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.label = label
        self.percent = max(0, min(100, percent))
        self.is_top = is_top
        self.bar_color = bar_color or (QColor("#10b981") if is_top else QColor("#8b5cf6"))
        self.setFixedHeight(18)

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # Tile / label text
        font = painter.font()
        font.setPointSize(9)
        font.setBold(self.is_top)
        painter.setFont(font)
        painter.setPen(QColor("#f1f5f9") if self.is_top else QColor("#cbd5e1"))
        label_rect = QRectF(2, 0, 36, h)
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.label)

        # Bar track
        bar_x = 42.0
        bar_w = w - bar_x - 42.0
        bar_rect = QRectF(bar_x, h * 0.28, bar_w, h * 0.44)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(30, 41, 59, 160)))
        painter.drawRoundedRect(bar_rect, 3.0, 3.0)

        # Bar fill with glow
        fill_w = max(4.0, bar_w * (self.percent / 100.0)) if self.percent > 0 else 0
        if fill_w > 0:
            fill_rect = QRectF(bar_x, h * 0.28, fill_w, h * 0.44)
            grad = QLinearGradient(fill_rect.topLeft(), fill_rect.topRight())
            if self.is_top:
                grad.setColorAt(0.0, QColor("#10b981"))
                grad.setColorAt(1.0, QColor("#34d399"))
            else:
                grad.setColorAt(0.0, self.bar_color)
                grad.setColorAt(1.0, self.bar_color.lighter(120))
            painter.setBrush(QBrush(grad))
            painter.drawRoundedRect(fill_rect, 3.0, 3.0)

        # Percentage text
        pct_rect = QRectF(w - 38, 0, 36, h)
        painter.setPen(QColor("#34d399") if self.is_top else QColor("#94a3b8"))
        painter.drawText(pct_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{self.percent}%")


class DecisionHeroCard(QFrame):
    """Hero recommendation card exactly matching the reference design."""

    alternative_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("decisionHeroCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # Header line: [AI 切牌] pill, Large glowing tile [1p], Big text "2向听", "置信 78%"
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self.action_badge = QLabel("AI 切牌")
        self.action_badge.setObjectName("heroActionBadge")
        top_row.addWidget(self.action_badge)

        self.hero_tile = MahjongTileWidget(tile="", width=34, height=48, show_code=True, glowing=True)
        top_row.addWidget(self.hero_tile)

        shanten_box = QVBoxLayout()
        shanten_box.setContentsMargins(0, 0, 0, 0)
        shanten_box.setSpacing(2)

        shanten_line = QHBoxLayout()
        shanten_line.setSpacing(6)
        self.shanten_label = QLabel("2向听")
        self.shanten_label.setObjectName("heroShantenLabel")
        shanten_line.addWidget(self.shanten_label)

        self.confidence_pill = QLabel("置信 78%")
        self.confidence_pill.setObjectName("heroConfidencePill")
        shanten_line.addWidget(self.confidence_pill)
        shanten_line.addStretch()
        shanten_box.addLayout(shanten_line)

        self.hero_desc = QLabel("Jev 待连接 · 点击底部设置配置 Key")
        self.hero_desc.setObjectName("heroDescText")
        self.hero_desc.setWordWrap(True)
        shanten_box.addWidget(self.hero_desc)

        top_row.addLayout(shanten_box, 1)
        layout.addLayout(top_row)

        # Probability bars list
        self.bars_container = QWidget()
        self.bars_layout = QVBoxLayout(self.bars_container)
        self.bars_layout.setContentsMargins(0, 4, 0, 4)
        self.bars_layout.setSpacing(3)
        layout.addWidget(self.bars_container)

        # Data Pills: 【弃和 28%】 【风险分 1.03】 【摸切 6】 【副露 2】
        self.pills_row = QHBoxLayout()
        self.pills_row.setSpacing(6)

        self.pill_fold = QLabel("弃和 28%")
        self.pill_fold.setObjectName("dataPill")
        self.pills_row.addWidget(self.pill_fold)

        self.pill_danger = QLabel("风险分 1.03")
        self.pill_danger.setObjectName("dataPill")
        self.pills_row.addWidget(self.pill_danger)

        self.pill_draw_status = QLabel("摸切 6")
        self.pill_draw_status.setObjectName("dataPill")
        self.pills_row.addWidget(self.pill_draw_status)

        self.pill_melds = QLabel("副露 2")
        self.pill_melds.setObjectName("dataPill")
        self.pills_row.addWidget(self.pill_melds)

        self.pill_yaku = QLabel("")
        self.pill_yaku.setObjectName("dataPill")
        self.pill_yaku.setStyleSheet("background-color: rgba(139, 92, 246, 0.25); color: #c4b5fd; border: 1px solid rgba(167, 139, 250, 0.4);")
        self.pills_row.addWidget(self.pill_yaku)

        self.pills_row.addStretch()
        layout.addLayout(self.pills_row)

        # Explanatory lines:
        # "Jev 选择概率 0.83, 置信度 0.78"
        # "风险 放铳风险 1.03 (confidence 0.38)"
        self.stats_line_1 = QLabel("Jev 选择概率 0.83, 置信度 0.78")
        self.stats_line_1.setObjectName("heroSubText")
        layout.addWidget(self.stats_line_1)

        self.stats_line_2 = QLabel("风险 放铳风险 1.03 (confidence 0.38)")
        self.stats_line_2.setObjectName("heroSubText")
        layout.addWidget(self.stats_line_2)

        # Alternatives badges line: 次选 [2s] [4p]
        self.alt_row = QHBoxLayout()
        self.alt_row.setSpacing(6)
        alt_label = QLabel("次选")
        alt_label.setStyleSheet("color: #94a3b8; font-size: 11px;")
        self.alt_row.addWidget(alt_label)

        self.alt_badges_layout = QHBoxLayout()
        self.alt_badges_layout.setSpacing(4)
        self.alt_row.addLayout(self.alt_badges_layout)
        self.alt_row.addStretch()
        layout.addLayout(self.alt_row)

        # Source footnote: "来源：Jev · 校准概率可视化"
        self.source_label = QLabel("来源：Jev · 校准概率可视化")
        self.source_label.setObjectName("heroFootnote")
        self.source_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.source_label)

    def update_decision(
        self,
        action_name: str = "切牌",
        rec_tile: str = "",
        shanten_num: int | None = 2,
        confidence: float | None = 0.78,
        probabilities: list[tuple[str, int, bool]] | None = None,
        fold_rate: int = 28,
        danger_score: float = 1.03,
        draw_info: str = "摸切",
        melds_count: int = 0,
        alternatives: list[str] | None = None,
        source_note: str = "Jev · 校准概率可视化",
        raw_detail: str = "",
        yaku_badge: str = "",
    ) -> None:
        self.rec_tile = rec_tile
        self.action_badge.setText(f"AI {action_name}")
        self.hero_tile.set_tile(rec_tile, glowing=bool(rec_tile))

        if shanten_num is not None:
            if shanten_num == 0:
                self.shanten_label.setText("听牌")
            else:
                self.shanten_label.setText(f"{shanten_num}向听")
        else:
            self.shanten_label.setText("决策中")

        conf_str = f"置信 {int(confidence * 100)}%" if confidence is not None else "确定规则"
        self.confidence_pill.setText(conf_str)

        if raw_detail:
            self.hero_desc.setText(raw_detail)
        else:
            self.hero_desc.setText("已根据牌效率、宝牌价值与放铳风险综合评估")

        # Update probability bars
        while self.bars_layout.count() > 0:
            child = self.bars_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if probabilities:
            for label, pct, is_top in probabilities:
                bar = ProbabilityBarItem(label, pct, is_top=is_top)
                self.bars_layout.addWidget(bar)
        else:
            # Fallback placeholder if no probabilities
            if rec_tile:
                bar = ProbabilityBarItem(rec_tile, 85, is_top=True)
                self.bars_layout.addWidget(bar)

        # Data pills
        self.pill_fold.setText(f"弃和 {fold_rate}%")
        self.pill_danger.setText(f"风险分 {danger_score:.2f}")
        self.pill_draw_status.setText(draw_info)
        self.pill_melds.setText(f"副露 {melds_count}")
        if yaku_badge:
            self.pill_yaku.setText(yaku_badge)
            self.pill_yaku.show()
        else:
            self.pill_yaku.hide()

        # Update detailed stats
        prob_val = probabilities[0][1] / 100.0 if probabilities else 0.80
        conf_val = confidence if confidence is not None else 0.85
        self.stats_line_1.setText(f"Jev 选择概率 {prob_val:.2f}, 置信度 {conf_val:.2f}")
        self.stats_line_2.setText(f"风险 放铳风险 {danger_score:.2f} (confidence {conf_val * 0.5:.2f})")

        # Alternatives badges
        while self.alt_badges_layout.count() > 0:
            child = self.alt_badges_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if alternatives:
            for alt in alternatives:
                badge = CompactTileBadge(alt)
                badge.clicked.connect(self.alternative_clicked.emit)
                self.alt_badges_layout.addWidget(badge)
        else:
            empty_lbl = QLabel("无")
            empty_lbl.setStyleSheet("color: #64748b; font-size: 11px;")
            self.alt_badges_layout.addWidget(empty_lbl)

        self.source_label.setText(f"来源：{source_note}")


class BottomActionBar(QWidget):
    """Bottom status line + button row: [AI建议] [深度解析] [清空] [Jev设置] [大模型设置]."""

    ai_suggest_clicked = Signal()
    llm_analysis_clicked = Signal()
    clear_clicked = Signal()
    jev_settings_clicked = Signal()
    llm_settings_clicked = Signal()
    toggle_clicked = Signal()
    manual_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 8)
        layout.setSpacing(6)

        # Status note line: "AI 建议 Jev jev-latest · 桥OK \n 切 1p · 置信 78% · 弃和 28%"
        self.status_box = QFrame()
        self.status_box.setObjectName("bottomStatusBox")
        box_layout = QVBoxLayout(self.status_box)
        box_layout.setContentsMargins(8, 6, 8, 6)
        box_layout.setSpacing(2)

        self.status_title = QLabel("AI 建议 Jev jev-latest · 桥OK")
        self.status_title.setObjectName("bottomStatusTitle")
        box_layout.addWidget(self.status_title)

        self.status_summary = QLabel("切 1p  ·  置信 78%  ·  弃和 28%")
        self.status_summary.setObjectName("bottomStatusSub")
        box_layout.addWidget(self.status_summary)

        layout.addWidget(self.status_box)

        # Main button bar: [AI建议] [深度解析] [清空] [Jev设置] [大模型设置]
        btn_row = QHBoxLayout()
        btn_row.setSpacing(5)

        self.btn_ai = QPushButton("AI建议")
        self.btn_ai.setObjectName("hudGhostBtn")
        self.btn_ai.clicked.connect(self.ai_suggest_clicked.emit)
        btn_row.addWidget(self.btn_ai)

        self.btn_explain = QPushButton("深度解析")
        self.btn_explain.setObjectName("hudGhostBtn")
        self.btn_explain.setToolTip("使用大模型 (LLM) 进行深度战术与牌理解析")
        self.btn_explain.clicked.connect(self.llm_analysis_clicked.emit)
        btn_row.addWidget(self.btn_explain)

        self.btn_clear = QPushButton("清空")
        self.btn_clear.setObjectName("hudGhostBtn")
        self.btn_clear.clicked.connect(self.clear_clicked.emit)
        btn_row.addWidget(self.btn_clear)

        self.btn_jev = QPushButton("Jev设置")
        self.btn_jev.setObjectName("hudGhostBtn")
        self.btn_jev.clicked.connect(self.jev_settings_clicked.emit)
        btn_row.addWidget(self.btn_jev)

        self.btn_llm = QPushButton("大模型设置")
        self.btn_llm.setObjectName("hudGhostBtn")
        self.btn_llm.clicked.connect(self.llm_settings_clicked.emit)
        btn_row.addWidget(self.btn_llm)

        layout.addLayout(btn_row)

        # Secondary row: [开始识别] [手动核对]
        sec_row = QHBoxLayout()
        sec_row.setSpacing(6)

        self.btn_toggle = QPushButton("开始识别")
        self.btn_toggle.setObjectName("hudPrimaryBtn")
        self.btn_toggle.clicked.connect(self.toggle_clicked.emit)
        sec_row.addWidget(self.btn_toggle, 3)

        self.btn_manual = QPushButton("手动核对")
        self.btn_manual.setObjectName("hudGhostBtn")
        self.btn_manual.clicked.connect(self.manual_clicked.emit)
        sec_row.addWidget(self.btn_manual, 2)

        layout.addLayout(sec_row)

    def set_status_info(self, title: str, summary: str) -> None:
        self.status_title.setText(title)
        self.status_summary.setText(summary)
