"""High-fidelity vector mahjong tile renderer for PySide6.

Renders Japanese Riichi Mahjong tiles (1m-9m, 1p-9p, 1s-9s, 1z-7z, and red 5s: 0m, 0p, 0s)
using pure QPainter vector primitives. Highly scalable, crisp on any DPI, zero external assets required.
"""

from __future__ import annotations

import math
from typing import Any
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import QWidget

from .tiles import normal, valid


# Japanese Riichi Mahjong classic color palette
COLOR_TILE_FACE_TOP = QColor(255, 255, 255)
COLOR_TILE_FACE_BOTTOM = QColor(240, 243, 247)
COLOR_TILE_BORDER = QColor(190, 200, 212)
COLOR_TILE_BACK = QColor(255, 204, 77)  # Classic yellow/bamboo back (if shown)

COLOR_RED = QColor(220, 38, 38)        # Pin 1/5/7/9 red, Chun, Akadora
COLOR_GREEN = QColor(22, 163, 74)      # Sou classic green, Hatsu
COLOR_BLUE = QColor(30, 64, 175)       # Pin classic deep blue
COLOR_CHAR = QColor(26, 32, 44)        # Character / honors deep charcoal
COLOR_GLOW = QColor(16, 185, 129)      # Neon emerald glow highlight
COLOR_GLOW_BG = QColor(16, 185, 129, 65)


def draw_tile_base(painter: QPainter, rect: QRectF, glowing: bool = False) -> None:
    """Draws ivory tile body with subtle 3D lighting and optional neon glow."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Glow effect if recommended
    if glowing:
        glow_pen = QPen(COLOR_GLOW, 2.5)
        glow_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(glow_pen)
        painter.setBrush(QBrush(COLOR_GLOW_BG))
        glow_rect = rect.adjusted(1, 1, -1, -1)
        painter.drawRoundedRect(glow_rect, 5.0, 5.0)

    # Tile base gradient
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    grad.setColorAt(0.0, COLOR_TILE_FACE_TOP)
    grad.setColorAt(1.0, COLOR_TILE_FACE_BOTTOM)

    tile_rect = rect.adjusted(2, 2, -2, -2) if glowing else rect.adjusted(1, 1, -1, -1)
    border_pen = QPen(COLOR_GLOW if glowing else COLOR_TILE_BORDER, 1.5 if glowing else 1.0)
    painter.setPen(border_pen)
    painter.setBrush(QBrush(grad))
    painter.drawRoundedRect(tile_rect, 4.0, 4.0)

    # Subtle inner bevel shadow
    inner_rect = tile_rect.adjusted(1, 1, -1, -1)
    painter.setPen(QPen(QColor(255, 255, 255, 180), 0.8))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(inner_rect, 3.0, 3.0)

    painter.restore()


def _draw_text_centered(painter: QPainter, rect: QRectF, text: str, font_size: float, color: QColor, bold: bool = True) -> None:
    painter.save()
    from PySide6.QtWidgets import QApplication
    base_font = QApplication.font()
    font = QFont(base_font.family(), max(6, int(font_size)))
    font.setBold(bold)
    painter.setFont(font)
    painter.setPen(color)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
    painter.restore()


def draw_pin_circle(painter: QPainter, center: QPointF, radius: float, outer_color: QColor, inner_color: QColor | None = None) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # Outer circle
    painter.setPen(QPen(outer_color.darker(110), max(0.8, radius * 0.15)))
    painter.setBrush(QBrush(outer_color))
    painter.drawEllipse(center, radius, radius)

    # Inner ring/dot
    inner_r = radius * 0.45
    if inner_color is not None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(inner_color))
        painter.drawEllipse(center, inner_r, inner_r)
    else:
        # Subtle light dot
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255, 180)))
        painter.drawEllipse(center, inner_r * 0.6, inner_r * 0.6)
    painter.restore()


def draw_one_pin(painter: QPainter, rect: QRectF, is_red: bool = False) -> None:
    """1p: Iconic large circular rosette with petals and concentric rings."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    center = rect.center()
    max_r = min(rect.width(), rect.height()) * 0.42

    # Draw 8 outer circular petals
    petal_r = max_r * 0.28
    orbit_r = max_r * 0.72
    painter.setPen(QPen(COLOR_RED if is_red else COLOR_BLUE, 0.8))
    painter.setBrush(QBrush(COLOR_RED if is_red else COLOR_BLUE))
    for i in range(8):
        angle = i * (2 * math.pi / 8)
        px = center.x() + orbit_r * math.cos(angle)
        py = center.y() + orbit_r * math.sin(angle)
        painter.drawEllipse(QPointF(px, py), petal_r, petal_r)

    # Outer decorative ring
    painter.setPen(QPen(COLOR_RED if is_red else COLOR_GREEN, max(1.0, max_r * 0.08)))
    painter.setBrush(QBrush(COLOR_TILE_FACE_TOP))
    painter.drawEllipse(center, max_r * 0.75, max_r * 0.75)

    # Middle ring
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(COLOR_RED if is_red else COLOR_BLUE))
    painter.drawEllipse(center, max_r * 0.55, max_r * 0.55)

    # Core center
    painter.setBrush(QBrush(COLOR_RED if is_red else COLOR_RED))
    painter.drawEllipse(center, max_r * 0.32, max_r * 0.32)

    # Core inner star/dot
    painter.setBrush(QBrush(QColor(255, 255, 255, 220)))
    painter.drawEllipse(center, max_r * 0.12, max_r * 0.12)
    painter.restore()


def draw_pin_pattern(painter: QPainter, rect: QRectF, num: int, is_red: bool = False) -> None:
    if num == 1:
        draw_one_pin(painter, rect, is_red)
        return

    w, h = rect.width(), rect.height()
    cx, cy = rect.center().x(), rect.center().y()
    dx = w * 0.24
    dy = h * 0.24
    r = min(w, h) * 0.125

    if num == 2:
        draw_pin_circle(painter, QPointF(cx, cy - dy * 0.9), r * 1.1, COLOR_GREEN, COLOR_TILE_FACE_TOP)
        draw_pin_circle(painter, QPointF(cx, cy + dy * 0.9), r * 1.1, COLOR_GREEN, COLOR_TILE_FACE_TOP)
    elif num == 3:
        draw_pin_circle(painter, QPointF(cx - dx * 0.85, cy - dy * 0.9), r, COLOR_BLUE)
        draw_pin_circle(painter, QPointF(cx, cy), r, COLOR_RED if is_red else COLOR_RED)
        draw_pin_circle(painter, QPointF(cx + dx * 0.85, cy + dy * 0.9), r, COLOR_GREEN)
    elif num == 4:
        draw_pin_circle(painter, QPointF(cx - dx * 0.8, cy - dy * 0.85), r * 1.05, COLOR_BLUE)
        draw_pin_circle(painter, QPointF(cx + dx * 0.8, cy - dy * 0.85), r * 1.05, COLOR_GREEN)
        draw_pin_circle(painter, QPointF(cx - dx * 0.8, cy + dy * 0.85), r * 1.05, COLOR_GREEN)
        draw_pin_circle(painter, QPointF(cx + dx * 0.8, cy + dy * 0.85), r * 1.05, COLOR_BLUE)
    elif num == 5:
        draw_pin_circle(painter, QPointF(cx - dx * 0.8, cy - dy * 0.85), r, COLOR_BLUE)
        draw_pin_circle(painter, QPointF(cx + dx * 0.8, cy - dy * 0.85), r, COLOR_GREEN)
        center_color = COLOR_RED
        draw_pin_circle(painter, QPointF(cx, cy), r * 1.15, center_color, QColor(255, 255, 255) if is_red else None)
        draw_pin_circle(painter, QPointF(cx - dx * 0.8, cy + dy * 0.85), r, COLOR_GREEN)
        draw_pin_circle(painter, QPointF(cx + dx * 0.8, cy + dy * 0.85), r, COLOR_BLUE)
    elif num == 6:
        draw_pin_circle(painter, QPointF(cx - dx * 0.8, cy - dy), r * 0.95, COLOR_GREEN)
        draw_pin_circle(painter, QPointF(cx + dx * 0.8, cy - dy), r * 0.95, COLOR_GREEN)
        draw_pin_circle(painter, QPointF(cx - dx * 0.8, cy), r * 0.95, COLOR_RED)
        draw_pin_circle(painter, QPointF(cx + dx * 0.8, cy), r * 0.95, COLOR_RED)
        draw_pin_circle(painter, QPointF(cx - dx * 0.8, cy + dy), r * 0.95, COLOR_RED)
        draw_pin_circle(painter, QPointF(cx + dx * 0.8, cy + dy), r * 0.95, COLOR_RED)
    elif num == 7:
        draw_pin_circle(painter, QPointF(cx - dx * 0.8, cy - dy * 1.05), r * 0.85, COLOR_GREEN)
        draw_pin_circle(painter, QPointF(cx, cy - dy * 0.65), r * 0.85, COLOR_GREEN)
        draw_pin_circle(painter, QPointF(cx + dx * 0.8, cy - dy * 0.25), r * 0.85, COLOR_GREEN)
        draw_pin_circle(painter, QPointF(cx - dx * 0.8, cy + dy * 0.45), r * 0.9, COLOR_RED)
        draw_pin_circle(painter, QPointF(cx + dx * 0.8, cy + dy * 0.45), r * 0.9, COLOR_RED)
        draw_pin_circle(painter, QPointF(cx - dx * 0.8, cy + dy * 1.15), r * 0.9, COLOR_RED)
        draw_pin_circle(painter, QPointF(cx + dx * 0.8, cy + dy * 1.15), r * 0.9, COLOR_RED)
    elif num == 8:
        for row in range(4):
            ry = cy + (row - 1.5) * (dy * 0.75)
            draw_pin_circle(painter, QPointF(cx - dx * 0.75, ry), r * 0.85, COLOR_BLUE)
            draw_pin_circle(painter, QPointF(cx + dx * 0.75, ry), r * 0.85, COLOR_BLUE)
    elif num == 9:
        for row in range(3):
            ry = cy + (row - 1) * (dy * 0.88)
            col_color = COLOR_BLUE if row == 0 else (COLOR_RED if row == 1 else COLOR_GREEN)
            draw_pin_circle(painter, QPointF(cx - dx * 0.82, ry), r * 0.88, col_color)
            draw_pin_circle(painter, QPointF(cx, ry), r * 0.88, col_color)
            draw_pin_circle(painter, QPointF(cx + dx * 0.82, ry), r * 0.88, col_color)


def draw_bamboo_stick(painter: QPainter, center: QPointF, width: float, height: float, color: QColor) -> None:
    """Draws a bamboo stalk segment with nodes."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(center.x() - width / 2, center.y() - height / 2, width, height)
    painter.setPen(QPen(color.darker(115), 0.8))
    painter.setBrush(QBrush(color))
    painter.drawRoundedRect(rect, width * 0.4, width * 0.4)

    # Node groove in the middle
    mid_y = center.y()
    painter.setPen(QPen(color.darker(130), max(0.8, height * 0.08)))
    painter.drawLine(QPointF(rect.left() + 1, mid_y), QPointF(rect.right() - 1, mid_y))
    painter.restore()


def draw_one_sou(painter: QPainter, rect: QRectF, is_red: bool = False) -> None:
    """1s: Elegant peacock/bird silhouette."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    center = rect.center()
    s = min(rect.width(), rect.height())

    color = COLOR_RED if is_red else COLOR_GREEN

    # Peacock body path
    path = QPainterPath()
    bx = center.x()
    by = center.y() + s * 0.05
    # Head & beak
    path.moveTo(bx - s * 0.12, by - s * 0.30)
    path.lineTo(bx - s * 0.20, by - s * 0.25)
    path.lineTo(bx - s * 0.08, by - s * 0.20)
    # Neck & breast
    path.quadTo(bx - s * 0.14, by - s * 0.05, bx - s * 0.08, by + s * 0.15)
    # Belly & branch
    path.quadTo(bx, by + s * 0.25, bx + s * 0.10, by + s * 0.18)
    # Wing & tail fan
    path.quadTo(bx + s * 0.28, by - s * 0.05, bx + s * 0.22, by - s * 0.22)
    path.quadTo(bx + s * 0.05, by - s * 0.28, bx - s * 0.08, by - s * 0.20)
    path.closeSubpath()

    painter.setPen(QPen(color.darker(120), 1.0))
    painter.setBrush(QBrush(color))
    painter.drawPath(path)

    # Red eye and crest
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(COLOR_RED))
    painter.drawEllipse(QPointF(bx - s * 0.09, by - s * 0.24), s * 0.04, s * 0.04)
    # Crest feather
    crest = QPainterPath()
    crest.moveTo(bx - s * 0.06, by - s * 0.26)
    crest.lineTo(bx + s * 0.02, by - s * 0.35)
    crest.lineTo(bx + s * 0.05, by - s * 0.28)
    crest.closeSubpath()
    painter.drawPath(crest)

    # Tail feather fan lines
    painter.setPen(QPen(color.lighter(130), 1.2))
    painter.drawLine(QPointF(bx + s * 0.05, by + s * 0.08), QPointF(bx + s * 0.24, by - s * 0.15))
    painter.drawLine(QPointF(bx + s * 0.06, by + s * 0.10), QPointF(bx + s * 0.20, by - s * 0.20))
    painter.drawLine(QPointF(bx + s * 0.04, by + s * 0.12), QPointF(bx + s * 0.15, by - s * 0.24))

    painter.restore()


def draw_sou_pattern(painter: QPainter, rect: QRectF, num: int, is_red: bool = False) -> None:
    if num == 1:
        draw_one_sou(painter, rect, is_red)
        return

    w, h = rect.width(), rect.height()
    cx, cy = rect.center().x(), rect.center().y()
    sw = w * 0.13
    sh = h * 0.32
    dx = w * 0.23
    dy = h * 0.25
    color = COLOR_RED if is_red else COLOR_GREEN

    if num == 2:
        draw_bamboo_stick(painter, QPointF(cx, cy - dy * 0.8), sw * 1.1, sh, color)
        draw_bamboo_stick(painter, QPointF(cx, cy + dy * 0.8), sw * 1.1, sh, color)
    elif num == 3:
        draw_bamboo_stick(painter, QPointF(cx, cy - dy * 0.85), sw, sh, color)
        draw_bamboo_stick(painter, QPointF(cx - dx * 0.8, cy + dy * 0.85), sw, sh, color)
        draw_bamboo_stick(painter, QPointF(cx + dx * 0.8, cy + dy * 0.85), sw, sh, color)
    elif num == 4:
        draw_bamboo_stick(painter, QPointF(cx - dx * 0.8, cy - dy * 0.85), sw, sh, color)
        draw_bamboo_stick(painter, QPointF(cx + dx * 0.8, cy - dy * 0.85), sw, sh, color)
        draw_bamboo_stick(painter, QPointF(cx - dx * 0.8, cy + dy * 0.85), sw, sh, color)
        draw_bamboo_stick(painter, QPointF(cx + dx * 0.8, cy + dy * 0.85), sw, sh, color)
    elif num == 5:
        draw_bamboo_stick(painter, QPointF(cx - dx * 0.8, cy - dy * 0.85), sw, sh * 0.9, color)
        draw_bamboo_stick(painter, QPointF(cx + dx * 0.8, cy - dy * 0.85), sw, sh * 0.9, color)
        # Center red or green
        draw_bamboo_stick(painter, QPointF(cx, cy), sw * 1.1, sh * 0.9, COLOR_RED if is_red else color)
        draw_bamboo_stick(painter, QPointF(cx - dx * 0.8, cy + dy * 0.85), sw, sh * 0.9, color)
        draw_bamboo_stick(painter, QPointF(cx + dx * 0.8, cy + dy * 0.85), sw, sh * 0.9, color)
    elif num == 6:
        for r in (-1, 1):
            ry = cy + r * dy * 0.85
            draw_bamboo_stick(painter, QPointF(cx - dx * 0.85, ry), sw * 0.9, sh * 0.9, color)
            draw_bamboo_stick(painter, QPointF(cx, ry), sw * 0.9, sh * 0.9, color)
            draw_bamboo_stick(painter, QPointF(cx + dx * 0.85, ry), sw * 0.9, sh * 0.9, color)
    elif num == 7:
        # Top 3 straight, bottom 4
        draw_bamboo_stick(painter, QPointF(cx, cy - dy * 0.95), sw * 0.9, sh * 0.75, COLOR_RED if is_red else color)
        draw_bamboo_stick(painter, QPointF(cx - dx * 0.8, cy - dy * 0.85), sw * 0.9, sh * 0.75, color)
        draw_bamboo_stick(painter, QPointF(cx + dx * 0.8, cy - dy * 0.85), sw * 0.9, sh * 0.75, color)
        draw_bamboo_stick(painter, QPointF(cx - dx * 0.8, cy + dy * 0.85), sw * 0.9, sh * 0.85, color)
        draw_bamboo_stick(painter, QPointF(cx - dx * 0.28, cy + dy * 0.85), sw * 0.9, sh * 0.85, color)
        draw_bamboo_stick(painter, QPointF(cx + dx * 0.28, cy + dy * 0.85), sw * 0.9, sh * 0.85, color)
        draw_bamboo_stick(painter, QPointF(cx + dx * 0.8, cy + dy * 0.85), sw * 0.9, sh * 0.85, color)
    elif num == 8:
        # 8 bamboo sticks arranged in V / classic pattern
        for c in (-1.1, -0.37, 0.37, 1.1):
            draw_bamboo_stick(painter, QPointF(cx + c * dx * 0.75, cy - dy * 0.8), sw * 0.85, sh * 0.8, color)
            draw_bamboo_stick(painter, QPointF(cx + c * dx * 0.75, cy + dy * 0.8), sw * 0.85, sh * 0.8, color)
    elif num == 9:
        for r in (-1, 0, 1):
            ry = cy + r * dy * 0.9
            draw_bamboo_stick(painter, QPointF(cx - dx * 0.85, ry), sw * 0.85, sh * 0.8, color)
            draw_bamboo_stick(painter, QPointF(cx, ry), sw * 0.85, sh * 0.8, color)
            draw_bamboo_stick(painter, QPointF(cx + dx * 0.85, ry), sw * 0.85, sh * 0.8, color)


def draw_man_pattern(painter: QPainter, rect: QRectF, num: int, is_red: bool = False) -> None:
    """Man characters: Upper number in Chinese character, lower '萬' in red."""
    kanji_digits = ["一", "二", "三", "四", "五", "六", "七", "八", "九"]
    digit_char = kanji_digits[num - 1]

    top_rect = QRectF(rect.left(), rect.top() + rect.height() * 0.08, rect.width(), rect.height() * 0.42)
    bottom_rect = QRectF(rect.left(), rect.top() + rect.height() * 0.48, rect.width(), rect.height() * 0.44)

    font_size = rect.height() * 0.32
    digit_color = COLOR_RED if is_red else (COLOR_RED if num == 5 else COLOR_CHAR)
    _draw_text_centered(painter, top_rect, digit_char, font_size, digit_color, bold=True)
    _draw_text_centered(painter, bottom_rect, "萬", font_size * 0.92, COLOR_RED, bold=True)


def draw_honor_pattern(painter: QPainter, rect: QRectF, honor_idx: int) -> None:
    """1z:東, 2z:南, 3z:西, 4z:北, 5z:白, 6z:發, 7z:中"""
    honors_map = {
        1: ("東", COLOR_CHAR),
        2: ("南", COLOR_CHAR),
        3: ("西", COLOR_CHAR),
        4: ("北", COLOR_CHAR),
        5: ("", COLOR_TILE_BORDER),   # Haku (white dragon, elegant blank with inner thin border)
        6: ("發", COLOR_GREEN),
        7: ("中", COLOR_RED),
    }
    char, color = honors_map.get(honor_idx, ("?", COLOR_CHAR))
    if honor_idx == 5:
        # Haku: Draw pristine blank tile with subtle modern double inner border
        inner_box = rect.adjusted(rect.width() * 0.16, rect.height() * 0.14, -rect.width() * 0.16, -rect.height() * 0.14)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(180, 195, 210, 140), 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(inner_box, 2.0, 2.0)
        painter.restore()
    else:
        _draw_text_centered(painter, rect, char, rect.height() * 0.62, color, bold=True)


def render_tile(painter: QPainter, rect: QRectF, tile_code: str, glowing: bool = False) -> None:
    """Main entrypoint to draw any Riichi mahjong tile into the given QRectF."""
    draw_tile_base(painter, rect, glowing=glowing)

    if not tile_code or not valid(tile_code):
        return

    is_red = tile_code.startswith("0")
    norm = normal(tile_code)
    num = int(norm[0])
    suit = norm[1]

    # Leave a margin around content
    content_rect = rect.adjusted(rect.width() * 0.08, rect.height() * 0.08, -rect.width() * 0.08, -rect.height() * 0.08)

    if suit == "p":
        draw_pin_pattern(painter, content_rect, num, is_red)
    elif suit == "s":
        draw_sou_pattern(painter, content_rect, num, is_red)
    elif suit == "m":
        draw_man_pattern(painter, content_rect, num, is_red)
    elif suit == "z":
        draw_honor_pattern(painter, content_rect, num)


class MahjongTileWidget(QWidget):
    """Interactive / standalone Mahjong Tile widget with code label & glow."""

    clicked = Signal(str)

    def __init__(
        self,
        tile: str = "",
        width: int = 28,
        height: int = 38,
        show_code: bool = True,
        glowing: bool = False,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.tile = tile
        self.tile_w = width
        self.tile_h = height
        self.show_code = show_code
        self.glowing = glowing
        self._update_geometry()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_tile(self, tile: str, glowing: bool | None = None) -> None:
        self.tile = tile
        if glowing is not None:
            self.glowing = glowing
        self.update()

    def set_glowing(self, glowing: bool) -> None:
        if self.glowing != glowing:
            self.glowing = glowing
            self.update()

    def _update_geometry(self) -> None:
        total_h = self.tile_h + (14 if self.show_code else 0)
        self.setFixedSize(self.tile_w + (6 if self.glowing else 4), total_h + 4)

    def set_tile_size(self, width: int, height: int, show_code: bool = True) -> None:
        self.tile_w = width
        self.tile_h = height
        self.show_code = show_code
        self._update_geometry()
        self.update()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.tile:
            self.clicked.emit(self.tile)
        super().mousePressEvent(event)

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        tile_rect = QRectF(2, 2, self.tile_w, self.tile_h)
        render_tile(painter, tile_rect, self.tile, glowing=self.glowing)

        if self.show_code and self.tile:
            label_rect = QRectF(0, self.tile_h + 2, self.width(), 13)
            font = painter.font()
            font.setPointSize(max(7, int(self.tile_h * 0.22)))
            font.setBold(self.glowing)
            painter.setFont(font)
            text_color = COLOR_GLOW if self.glowing else QColor("#94a3b8")
            painter.setPen(text_color)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, self.tile)


class CompactTileBadge(QWidget):
    """Pill / chip tile for rivers and alternatives, like [1p], [3m]."""

    clicked = Signal(str)

    def __init__(self, tile: str = "", parent: QWidget | None = None, highlighted: bool = False):
        super().__init__(parent)
        self.tile = tile
        self.highlighted = highlighted
        self.setFixedHeight(22)
        self.setMinimumWidth(32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_tile(self, tile: str, highlighted: bool = False) -> None:
        self.tile = tile
        self.highlighted = highlighted
        self.update()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.tile:
            self.clicked.emit(self.tile)
        super().mousePressEvent(event)

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        # Suit color accent
        suit = self.tile[-1] if self.tile and len(self.tile) >= 2 else ""
        is_red = self.tile.startswith("0")

        if self.highlighted:
            bg_color = QColor(16, 185, 129, 45)
            border_color = QColor(16, 185, 129)
            text_color = QColor("#34d399")
        else:
            bg_color = QColor(22, 29, 46)
            border_color = QColor(42, 54, 82)
            if is_red:
                text_color = QColor("#f87171")
            elif suit == "m":
                text_color = QColor("#fca5a5")
            elif suit == "p":
                text_color = QColor("#93c5fd")
            elif suit == "s":
                text_color = QColor("#86efac")
            else:
                text_color = QColor("#cbd5e1")

        painter.setPen(QPen(border_color, 1.0))
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(rect, 4.0, 4.0)

        # Draw tile text
        font = painter.font()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.tile)
