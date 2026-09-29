"""High-fidelity vector mahjong tile renderer for PySide6.

Renders Japanese Riichi Mahjong tiles (1m-9m, 1p-9p, 1s-9s, 1z-7z, and red 5s: 0m, 0p, 0s)
using pure QPainter vector primitives. Highly scalable, crisp on any DPI, zero external assets required.
"""

from __future__ import annotations

import math
from typing import Any
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontDatabase, QLinearGradient, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import QWidget

from .tiles import normal, valid


# Japanese Riichi Mahjong classic color palette
# Japanese Riichi Mahjong classic color palette - authentic pigments
COLOR_TILE_FACE_TOP = QColor(255, 255, 255)
COLOR_TILE_FACE_BOTTOM = QColor(245, 242, 235)  # Warm Japanese ivory
COLOR_TILE_BORDER = QColor(195, 202, 212)
COLOR_TILE_BACK = QColor(230, 160, 40)        # Classic bamboo amber back
COLOR_TILE_SHADOW = QColor(0, 0, 0, 55)

COLOR_RED = QColor(225, 29, 72)         # Cinnabar / Akadora
COLOR_GREEN = QColor(16, 149, 72)       # Lush bamboo green
COLOR_BLUE = QColor(29, 78, 216)        # Deep royal navy
COLOR_CHAR = QColor(24, 30, 42)         # Traditional ink black
COLOR_GLOW = QColor(16, 185, 129)       # Emerald neon glow
COLOR_GLOW_BG = QColor(16, 185, 129, 70)


def _tile_font_family() -> str:
    """Choose a font that contains the CJK glyphs printed on tile faces."""
    installed = set(QFontDatabase.families())
    for family in (
        "Microsoft YaHei UI", "Microsoft YaHei", "SimHei",
        "Noto Sans CJK SC", "Noto Sans CJK JP", "Arial Unicode MS",
    ):
        if family in installed:
            return family
    from PySide6.QtWidgets import QApplication
    return QApplication.font().family()


def draw_tile_base(painter: QPainter, rect: QRectF, glowing: bool = False) -> None:
    """Draws ivory ceramic tile body with authentic 3D lighting, bevel highlights, and optional neon glow."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # 1. Subtle physical drop shadow
    shadow_rect = rect.adjusted(1.5, 2.5, -1.5, -0.5)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(COLOR_TILE_SHADOW))
    painter.drawRoundedRect(shadow_rect, 4.5, 4.5)

    # 2. Glowing aura if selected / recommended
    if glowing:
        # Outer soft ambient glow
        glow_pen_wide = QPen(QColor(16, 185, 129, 90), 4.0)
        glow_pen_wide.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(glow_pen_wide)
        painter.setBrush(QBrush(COLOR_GLOW_BG))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 5.5, 5.5)

        # Core crisp neon edge
        glow_pen_sharp = QPen(QColor(52, 211, 153), 1.8)
        painter.setPen(glow_pen_sharp)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(1.5, 1.5, -1.5, -1.5), 5.0, 5.0)

    tile_rect = rect.adjusted(2, 2, -2, -2) if glowing else rect.adjusted(1, 1, -1, -1.5)

    # 3. Dual-layer tile effect: subtle bamboo amber back rim at bottom
    if tile_rect.height() > 18:
        back_rect = QRectF(tile_rect.left(), tile_rect.bottom() - 2.5, tile_rect.width(), 2.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(COLOR_TILE_BACK))
        painter.drawRoundedRect(back_rect, 2.0, 2.0)

    # 4. Ivory ceramic face gradient
    face_rect = QRectF(tile_rect.left(), tile_rect.top(), tile_rect.width(), tile_rect.height() - 1.5)
    grad = QLinearGradient(face_rect.topLeft(), face_rect.bottomLeft())
    grad.setColorAt(0.0, COLOR_TILE_FACE_TOP)
    grad.setColorAt(0.2, QColor(254, 253, 250))
    grad.setColorAt(1.0, COLOR_TILE_FACE_BOTTOM)

    border_pen = QPen(QColor(52, 211, 153) if glowing else COLOR_TILE_BORDER, 1.2 if glowing else 0.9)
    painter.setPen(border_pen)
    painter.setBrush(QBrush(grad))
    painter.drawRoundedRect(face_rect, 4.0, 4.0)

    # 5. Top-left specular bevel highlight (gives tangible acrylic/bone shine)
    inner_highlight = face_rect.adjusted(0.8, 0.8, -0.8, -0.8)
    painter.setPen(QPen(QColor(255, 255, 255, 230), 0.9))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(inner_highlight, 3.2, 3.2)

    # 6. Bottom-right subtle bevel shadow
    shadow_line_pen = QPen(QColor(170, 178, 192, 100), 0.8)
    painter.setPen(shadow_line_pen)
    painter.drawLine(inner_highlight.bottomLeft() + QPointF(2, 0), inner_highlight.bottomRight() - QPointF(2, 0))

    painter.restore()


def _draw_text_centered(painter: QPainter, rect: QRectF, text: str, font_size: float, color: QColor, bold: bool = True) -> None:
    painter.save()
    font = QFont(_tile_font_family(), max(6, int(font_size)))
    font.setBold(bold)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    painter.setFont(font)
    painter.setPen(color)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
    painter.restore()


def draw_pin_circle(painter: QPainter, center: QPointF, radius: float, outer_color: QColor, inner_color: QColor | None = None) -> None:
    """Draws high-fidelity concentric pin circle with depth."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Outer circle with crisp stroke
    painter.setPen(QPen(outer_color.darker(115), max(0.8, radius * 0.14)))
    painter.setBrush(QBrush(outer_color))
    painter.drawEllipse(center, radius, radius)

    # Concentric inner groove
    groove_r = radius * 0.65
    painter.setPen(QPen(QColor(255, 255, 255, 120), max(0.6, radius * 0.08)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(center, groove_r, groove_r)

    # Inner core
    inner_r = radius * 0.42
    if inner_color is not None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(inner_color))
        painter.drawEllipse(center, inner_r, inner_r)
    else:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255, 200)))
        painter.drawEllipse(center, inner_r * 0.65, inner_r * 0.65)
    painter.restore()


def draw_one_pin(painter: QPainter, rect: QRectF, is_red: bool = False) -> None:
    """1p: Iconic large circular rosette with petals and concentric rings."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    center = rect.center()
    max_r = min(rect.width(), rect.height()) * 0.44

    primary_color = COLOR_RED if is_red else COLOR_BLUE
    secondary_color = COLOR_RED if is_red else COLOR_GREEN

    # 1. Outer 8 circular petal florets
    petal_r = max_r * 0.26
    orbit_r = max_r * 0.72
    painter.setPen(QPen(primary_color.darker(110), 0.7))
    painter.setBrush(QBrush(primary_color))
    for i in range(8):
        angle = i * (2 * math.pi / 8)
        px = center.x() + orbit_r * math.cos(angle)
        py = center.y() + orbit_r * math.sin(angle)
        painter.drawEllipse(QPointF(px, py), petal_r, petal_r)

    # 2. Outer decorative ring
    painter.setPen(QPen(secondary_color, max(1.0, max_r * 0.09)))
    painter.setBrush(QBrush(COLOR_TILE_FACE_TOP))
    painter.drawEllipse(center, max_r * 0.74, max_r * 0.74)

    # 3. Middle ring with gear teeth accents
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(primary_color))
    painter.drawEllipse(center, max_r * 0.54, max_r * 0.54)

    # 4. Core center rosette
    painter.setBrush(QBrush(COLOR_RED))
    painter.drawEllipse(center, max_r * 0.32, max_r * 0.32)

    # 5. Core inner jewel star
    painter.setBrush(QBrush(QColor(255, 255, 255, 230)))
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
    r = min(w, h) * 0.13

    if num == 2:
        draw_pin_circle(painter, QPointF(cx, cy - dy * 0.9), r * 1.15, COLOR_GREEN, COLOR_TILE_FACE_TOP)
        draw_pin_circle(painter, QPointF(cx, cy + dy * 0.9), r * 1.15, COLOR_GREEN, COLOR_TILE_FACE_TOP)
    elif num == 3:
        draw_pin_circle(painter, QPointF(cx - dx * 0.85, cy - dy * 0.9), r, COLOR_BLUE)
        draw_pin_circle(painter, QPointF(cx, cy), r, COLOR_RED)
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
        draw_pin_circle(painter, QPointF(cx, cy), r * 1.2, center_color, QColor(255, 255, 255) if is_red else None)
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
            draw_pin_circle(painter, QPointF(cx - dx * 0.75, ry), r * 0.88, COLOR_BLUE)
            draw_pin_circle(painter, QPointF(cx + dx * 0.75, ry), r * 0.88, COLOR_BLUE)
    elif num == 9:
        for row in range(3):
            ry = cy + (row - 1) * (dy * 0.88)
            col_color = COLOR_BLUE if row == 0 else (COLOR_RED if row == 1 else COLOR_GREEN)
            draw_pin_circle(painter, QPointF(cx - dx * 0.82, ry), r * 0.88, col_color)
            draw_pin_circle(painter, QPointF(cx, ry), r * 0.88, col_color)
            draw_pin_circle(painter, QPointF(cx + dx * 0.82, ry), r * 0.88, col_color)


def draw_bamboo_stick(painter: QPainter, center: QPointF, width: float, height: float, color: QColor) -> None:
    """Draws a bamboo stalk segment with 3D nodes and subtle lighting."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(center.x() - width / 2, center.y() - height / 2, width, height)

    # Bamboo stalk base
    painter.setPen(QPen(color.darker(118), 0.7))
    painter.setBrush(QBrush(color))
    painter.drawRoundedRect(rect, width * 0.45, width * 0.45)

    # Subtle inner highlight on left edge of stick
    highlight_pen = QPen(QColor(255, 255, 255, 90), max(0.5, width * 0.15))
    painter.setPen(highlight_pen)
    painter.drawLine(
        QPointF(rect.left() + width * 0.2, rect.top() + height * 0.15),
        QPointF(rect.left() + width * 0.2, rect.bottom() - height * 0.15),
    )

    # Center node groove with double accent
    mid_y = center.y()
    node_pen = QPen(color.darker(140), max(0.8, height * 0.08))
    painter.setPen(node_pen)
    painter.drawLine(QPointF(rect.left() + 1, mid_y), QPointF(rect.right() - 1, mid_y))

    node_light = QPen(QColor(255, 255, 255, 120), max(0.5, height * 0.05))
    painter.setPen(node_light)
    painter.drawLine(QPointF(rect.left() + 1, mid_y - 1), QPointF(rect.right() - 1, mid_y - 1))

    painter.restore()


def draw_one_sou(painter: QPainter, rect: QRectF, is_red: bool = False) -> None:
    """1s: Exquisite Japanese peacock silhouette with crested crown and flowing tail feathers."""
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
    path.lineTo(bx - s * 0.22, by - s * 0.25)
    path.lineTo(bx - s * 0.08, by - s * 0.20)
    # Neck & breast
    path.quadTo(bx - s * 0.15, by - s * 0.04, bx - s * 0.08, by + s * 0.16)
    # Belly & perch
    path.quadTo(bx, by + s * 0.26, bx + s * 0.12, by + s * 0.18)
    # Wing & tail fan
    path.quadTo(bx + s * 0.30, by - s * 0.04, bx + s * 0.24, by - s * 0.22)
    path.quadTo(bx + s * 0.06, by - s * 0.28, bx - s * 0.08, by - s * 0.20)
    path.closeSubpath()

    painter.setPen(QPen(color.darker(120), 0.9))
    painter.setBrush(QBrush(color))
    painter.drawPath(path)

    # Red eye and crest
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(COLOR_RED))
    painter.drawEllipse(QPointF(bx - s * 0.10, by - s * 0.24), s * 0.04, s * 0.04)

    # Crest feather
    crest = QPainterPath()
    crest.moveTo(bx - s * 0.06, by - s * 0.26)
    crest.lineTo(bx + s * 0.02, by - s * 0.36)
    crest.lineTo(bx + s * 0.06, by - s * 0.28)
    crest.closeSubpath()
    painter.drawPath(crest)

    # Flowing tail feather plume lines
    tail_pen = QPen(color.lighter(135), 1.2)
    tail_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(tail_pen)
    painter.drawLine(QPointF(bx + s * 0.04, by + s * 0.06), QPointF(bx + s * 0.26, by - s * 0.15))
    painter.drawLine(QPointF(bx + s * 0.05, by + s * 0.09), QPointF(bx + s * 0.22, by - s * 0.21))
    painter.drawLine(QPointF(bx + s * 0.03, by + s * 0.12), QPointF(bx + s * 0.16, by - s * 0.25))

    # Red jewel in breast/wing
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(COLOR_RED))
    painter.drawEllipse(QPointF(bx + s * 0.06, by - s * 0.02), s * 0.05, s * 0.05)

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
        draw_bamboo_stick(painter, QPointF(cx, cy - dy * 0.95), sw * 0.9, sh * 0.75, COLOR_RED if is_red else color)
        draw_bamboo_stick(painter, QPointF(cx - dx * 0.8, cy - dy * 0.85), sw * 0.9, sh * 0.75, color)
        draw_bamboo_stick(painter, QPointF(cx + dx * 0.8, cy - dy * 0.85), sw * 0.9, sh * 0.75, color)
        draw_bamboo_stick(painter, QPointF(cx - dx * 0.8, cy + dy * 0.85), sw * 0.9, sh * 0.85, color)
        draw_bamboo_stick(painter, QPointF(cx - dx * 0.28, cy + dy * 0.85), sw * 0.9, sh * 0.85, color)
        draw_bamboo_stick(painter, QPointF(cx + dx * 0.28, cy + dy * 0.85), sw * 0.9, sh * 0.85, color)
        draw_bamboo_stick(painter, QPointF(cx + dx * 0.8, cy + dy * 0.85), sw * 0.9, sh * 0.85, color)
    elif num == 8:
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
    """Man characters: Upper number in Japanese Kanji, lower '萬' in cinnabar red."""
    kanji_digits = ["一", "二", "三", "四", "五", "六", "七", "八", "九"]
    digit_char = kanji_digits[num - 1]

    top_rect = QRectF(rect.left(), rect.top() + rect.height() * 0.06, rect.width(), rect.height() * 0.44)
    bottom_rect = QRectF(rect.left(), rect.top() + rect.height() * 0.48, rect.width(), rect.height() * 0.46)

    font_size = rect.height() * 0.34
    digit_color = COLOR_RED if is_red else COLOR_CHAR
    _draw_text_centered(painter, top_rect, digit_char, font_size, digit_color, bold=True)
    _draw_text_centered(painter, bottom_rect, "萬", font_size * 0.94, COLOR_RED, bold=True)


def draw_honor_pattern(painter: QPainter, rect: QRectF, honor_idx: int) -> None:
    """1z:東, 2z:南, 3z:西, 4z:北, 5z:白, 6z:發, 7z:中"""
    honors_map = {
        1: ("東", COLOR_CHAR),
        2: ("南", COLOR_CHAR),
        3: ("西", COLOR_CHAR),
        4: ("北", COLOR_CHAR),
        5: ("", COLOR_TILE_BORDER),
        6: ("發", COLOR_GREEN),
        7: ("中", COLOR_RED),
    }
    char, color = honors_map.get(honor_idx, ("?", COLOR_CHAR))
    if honor_idx == 5:
        # Haku: Pristine blank tile with elegant double inner frame
        inner_box = rect.adjusted(rect.width() * 0.14, rect.height() * 0.12, -rect.width() * 0.14, -rect.height() * 0.12)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(170, 185, 205, 150), 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(inner_box, 2.5, 2.5)

        sub_box = inner_box.adjusted(1.5, 1.5, -1.5, -1.5)
        painter.setPen(QPen(QColor(220, 230, 242, 180), 0.6))
        painter.drawRoundedRect(sub_box, 1.8, 1.8)
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
        self._is_hovered = False
        self._update_geometry()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, event: Any) -> None:
        self._is_hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:
        self._is_hovered = False
        self.update()
        super().leaveEvent(event)

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

        # Subtle hover brightness lift
        if self._is_hovered and self.tile:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(255, 255, 255, 30)))
            painter.drawRoundedRect(tile_rect, 4.0, 4.0)

        if self.show_code and self.tile:
            label_rect = QRectF(0, self.tile_h + 2, self.width(), 13)
            font = painter.font()
            font.setPointSize(max(7, int(self.tile_h * 0.22)))
            font.setBold(self.glowing)
            font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
            painter.setFont(font)
            text_color = COLOR_GLOW if self.glowing else QColor("#94a3b8")
            painter.setPen(text_color)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, self.tile)


class CompactTileBadge(QWidget):
    """Pill / chip tile for rivers and alternatives, like [1p], [3m], with tactile ceramic feel."""

    clicked = Signal(str)

    def __init__(self, tile: str = "", parent: QWidget | None = None, highlighted: bool = False):
        super().__init__(parent)
        self.tile = tile
        self.highlighted = highlighted
        self._is_hovered = False
        self.setFixedHeight(22)
        self.setMinimumWidth(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, event: Any) -> None:
        self._is_hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:
        self._is_hovered = False
        self.update()
        super().leaveEvent(event)

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

        suit = self.tile[-1] if self.tile and len(self.tile) >= 2 else ""
        is_red = self.tile.startswith("0")

        if self.highlighted:
            bg_color = QColor(16, 185, 129, 50)
            border_color = QColor(16, 185, 129)
            text_color = QColor("#34d399")
            accent_color = QColor("#10b981")
        else:
            if self._is_hovered:
                bg_color = QColor(30, 42, 68, 220)
                border_color = QColor(56, 189, 248, 180)
            else:
                bg_color = QColor(20, 26, 42, 200)
                border_color = QColor(42, 54, 82, 220)

            if is_red:
                text_color = QColor("#f87171")
                accent_color = QColor("#ef4444")
            elif suit == "m":
                text_color = QColor("#fca5a5")
                accent_color = QColor("#f87171")
            elif suit == "p":
                text_color = QColor("#93c5fd")
                accent_color = QColor("#38bdf8")
            elif suit == "s":
                text_color = QColor("#86efac")
                accent_color = QColor("#34d399")
            else:
                text_color = QColor("#e2e8f0")
                accent_color = QColor("#a78bfa")

        # Background capsule with subtle gradient
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0.0, bg_color.lighter(115))
        grad.setColorAt(1.0, bg_color)
        painter.setBrush(QBrush(grad))
        painter.setPen(QPen(border_color, 1.0))
        painter.drawRoundedRect(rect, 4.5, 4.5)

        # Left subtle suit color indicator bar
        accent_rect = QRectF(rect.left() + 2, rect.top() + 3, 2, rect.height() - 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(accent_color))
        painter.drawRoundedRect(accent_rect, 1.0, 1.0)

        # Draw tile text
        font = painter.font()
        font.setPointSize(9)
        font.setBold(True)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        painter.setFont(font)
        painter.setPen(text_color)
        text_rect = rect.adjusted(3, 0, 0, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.tile)
