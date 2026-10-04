"""Calibrated screen regions, tile templates and OCR.

The reader is deliberately strict: missing templates or ambiguous samples produce
an uncertain observation, never a guessed tile.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .settings import config_dir
from .state import GameState
from .tiles import TILES, RED_TILES

TEMPLATE_LABELS = TILES + RED_TILES + ("empty",)
TEMPLATE_SIZE = (48, 64)
MIN_MATCH = 0.88

REGIONS: tuple[tuple[str, str, int, int, bool], ...] = (
    ("hand", "手牌（不含摸牌）", 13, 1, True),
    ("draw", "右侧摸牌", 1, 1, True),
    ("river_0", "自己的牌河", 6, 4, False),
    ("river_1", "右家牌河", 6, 4, False),
    ("river_2", "对家牌河", 6, 4, False),
    ("river_3", "左家牌河", 6, 4, False),
    ("meld_0", "自己的副露区", 4, 4, False),
    ("meld_1", "右家副露区", 4, 4, False),
    ("meld_2", "对家副露区", 4, 4, False),
    ("meld_3", "左家副露区", 4, 4, False),
    ("dora", "宝牌指示牌", 5, 1, False),
    ("buttons", "操作按钮区", 1, 1, True),
    ("score_0", "自己点数", 1, 1, False),
    ("score_1", "右家点数", 1, 1, False),
    ("score_2", "对家点数", 1, 1, False),
    ("score_3", "左家点数", 1, 1, False),
    ("round", "场风/局数/本场", 1, 1, False),
    ("riichi_1", "右家立直棒出现的位置", 1, 1, False),
    ("riichi_2", "对家立直棒出现的位置", 1, 1, False),
    ("riichi_3", "左家立直棒出现的位置", 1, 1, False),
)
REGION_INFO = {row[0]: row for row in REGIONS}


@dataclass(frozen=True)
class TileMatch:
    label: str | None
    confidence: float
    occupied: bool


@dataclass(frozen=True)
class Observation:
    state: GameState | None
    confidence: float
    problems: tuple[str, ...]
    debug: dict[str, Any]


def normalized_tile(image: np.ndarray) -> np.ndarray:
    if image.size == 0:
        raise ValueError("Empty tile crop")
    color = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR) if image.ndim == 2 else image
    h, w = color.shape[:2]
    # Remove the border; card frames vary more than the glyphs.
    color = color[max(0, h // 12):max(1, h - h // 12), max(0, w // 12):max(1, w - w // 12)]
    resized = cv2.resize(color, TEMPLATE_SIZE, interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(resized, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = cv2.equalizeHist(lab[:, :, 0])
    return lab


def _prep_sample_vector(img: np.ndarray) -> np.ndarray:
    """Computes per-channel zero-mean and global L2 normalized 1D vector."""
    out = np.zeros(img.shape, dtype=np.float32)
    var_sum = 0.0
    for c in range(3):
        ch = img[:, :, c].astype(np.float32)
        ch -= ch.mean()
        var_sum += float(np.sum(ch * ch))
        out[:, :, c] = ch
    denom = np.sqrt(max(var_sum, 1e-12))
    return (out / denom).flatten()


class TemplateStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (config_dir() / "templates")
        self.samples: list[tuple[str, np.ndarray]] = []
        self._matrix: np.ndarray | None = None
        self._labels: list[str] = []
        self.reload()

    def _rebuild_matrix(self) -> None:
        if not self.samples:
            self._matrix = None
            self._labels = []
            return
        self._labels = [label for label, _ in self.samples]
        vectors = [_prep_sample_vector(sample) for _, sample in self.samples]
        self._matrix = np.stack(vectors, axis=0)

    def reload(self) -> None:
        self.samples.clear()
        if not self.path.exists():
            self._rebuild_matrix()
            return
        for path in self.path.glob("*.png"):
            label = path.stem.split("__", 1)[0]
            if label not in TEMPLATE_LABELS:
                continue
            sample = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if sample is not None and sample.shape == (TEMPLATE_SIZE[1], TEMPLATE_SIZE[0], 3):
                self.samples.append((label, sample))
        self._rebuild_matrix()

    def add(self, label: str, image: np.ndarray) -> Path:
        if label not in TEMPLATE_LABELS:
            raise ValueError(f"Unknown template label: {label}")
        self.path.mkdir(parents=True, exist_ok=True)
        sample = normalized_tile(image)
        number = 1 + len(list(self.path.glob(f"{label}__*.png")))
        path = self.path / f"{label}__{number:03d}.png"
        if not cv2.imwrite(str(path), sample):
            raise OSError(f"Could not save template {path}")
        self.samples.append((label, sample))
        self._rebuild_matrix()
        return path

    def match(self, image: np.ndarray) -> TileMatch:
        results = self.match_batch([image])
        return results[0] if results else TileMatch(None, 0.0, True)

    def match_batch(self, images: list[np.ndarray]) -> list[TileMatch]:
        if not images:
            return []
        if not self.samples or self._matrix is None or len(self._matrix) == 0:
            return [TileMatch(None, 0.0, True) for _ in images]

        crops = [normalized_tile(img) for img in images]
        crop_vectors = [_prep_sample_vector(c) for c in crops]
        crops_matrix = np.stack(crop_vectors, axis=0)  # Shape (M, D)

        # Batch correlation scores: (M, D) @ (D, N) -> (M, N) in single BLAS call
        all_scores = crops_matrix @ self._matrix.T

        results: list[TileMatch] = []
        for row_scores in all_scores:
            scores: dict[str, float] = {}
            for i, label in enumerate(self._labels):
                s = float(row_scores[i])
                if s > scores.get(label, -1.0):
                    scores[label] = s
            ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
            label, score = ordered[0]
            runner_up = ordered[1][1] if len(ordered) > 1 else -1.0
            if score < MIN_MATCH or score - runner_up < 0.025:
                results.append(TileMatch(None, max(0.0, score), True))
            elif label == "empty":
                results.append(TileMatch(None, score, False))
            else:
                results.append(TileMatch(label, score, True))
        return results


def crop_region(frame: np.ndarray, rect: tuple[float, float, float, float]) -> np.ndarray:
    height, width = frame.shape[:2]
    x, y, w, h = rect
    x0, y0 = max(0, round(x * width)), max(0, round(y * height))
    x1, y1 = min(width, round((x + w) * width)), min(height, round((y + h) * height))
    return frame[y0:y1, x0:x1]


def region_cells(frame: np.ndarray, rect: tuple[float, float, float, float], columns: int, rows: int) -> list[np.ndarray]:
    region = crop_region(frame, rect)
    if region.size == 0:
        return []
    height, width = region.shape[:2]
    return [
        region[round(r * height / rows):round((r + 1) * height / rows),
               round(c * width / columns):round((c + 1) * width / columns)]
        for r in range(rows) for c in range(columns)
    ]


def detect_basic_regions(frame: np.ndarray) -> dict[str, tuple[float, float, float, float]]:
    """Detect the standard Mahjong Soul hand strip and derive live input regions.

    The browser game scales its 16:9 canvas uniformly.  The hand itself is a much
    stronger anchor than absolute pixels: thirteen light tile faces form one long
    contour at the bottom of the canvas.  No screenshot is saved by this routine.
    """
    if frame.size == 0 or frame.ndim != 3:
        return {}
    height, width = frame.shape[:2]
    if width < 640 or height < 360 or width / height < 1.35:
        return {}
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    # Includes pale tile faces while excluding the blue table and orange tile backs.
    mask = cv2.inRange(hsv, (0, 0, 125), (179, 210, 255))
    mask[:round(height * 0.68)] = 0
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(5, round(width / 220)), max(7, round(height / 80)))
    )
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    choices: list[tuple[float, tuple[int, int, int, int]]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if (y >= height * 0.68 and width * 0.38 <= w <= width * 0.82
                and height * 0.075 <= h <= height * 0.22 and w / max(h, 1) >= 5.0):
            fill = cv2.contourArea(contour) / max(1, w * h)
            if fill >= 0.48:
                choices.append((w * h * fill, (x, y, w, h)))
    if not choices:
        return {}
    _, (x, y, hand_w, hand_h) = max(choices, key=lambda item: item[0])
    slot_w = hand_w / 13.0
    if not 0.35 <= slot_w / hand_h <= 0.9:
        return {}
    draw_x = min(width - slot_w, x + hand_w + max(3, width * 0.006))

    def norm(px: float, py: float, pw: float, ph: float):
        return (max(0.0, px / width), max(0.0, py / height),
                min(1.0 - px / width, pw / width), min(1.0 - py / height, ph / height))

    return {
        "hand": norm(x, y, hand_w, hand_h),
        "draw": norm(draw_x, y, slot_w, hand_h),
        # Mahjong Soul keeps action buttons in this stable central-bottom band.
        "buttons": (0.40, 0.62, 0.48, 0.25),
        # The upper-left five-slot dora rack is also stable on the common web layout.
        "dora": (0.010, 0.040, 0.145, 0.095),
    }


def learn_templates_from_state(frame: np.ndarray, regions: dict[str, tuple[float, float, float, float]],
                               state: GameState, store: TemplateStore) -> int:
    """Use a verified manual state once to label the current on-screen tile crops."""
    saved = 0
    concealed_count = 13 - 3 * state.open_melds
    hand_labels = list(state.hand[:concealed_count])
    draw_labels = list(state.hand[concealed_count:])
    if "hand" in regions:
        hand_cells = region_cells(frame, regions["hand"], 13, 1)
        for label, cell in zip(hand_labels, hand_cells):
            if cell.size:
                store.add(label, cell)
                saved += 1
    if "draw" in regions:
        draw_cells = region_cells(frame, regions["draw"], 1, 1)
        if draw_cells and draw_cells[0].size:
            store.add(draw_labels[0] if draw_labels else "empty", draw_cells[0])
            saved += 1
    if "dora" in regions and state.dora_indicators:
        dora_cells = region_cells(frame, regions["dora"], 5, 1)
        for index, cell in enumerate(dora_cells):
            if cell.size:
                label = state.dora_indicators[index] if index < len(state.dora_indicators) else "empty"
                store.add(label, cell)
                saved += 1
    return saved


BUTTON_WORDS = {
    "ron": ("荣和", "榮和", "ロン", "ron"),
    "tsumo": ("自摸", "ツモ", "tsumo"),
    "riichi": ("立直", "リーチ", "riichi"),
    "chi": ("吃", "チー", "chi"),
    "pon": ("碰", "ポン", "pon"),
    "kan": ("杠", "槓", "カン", "kan"),
    "pass": ("过", "過", "跳遇", "パス", "pass"),
    "nuki": ("拔北", "抜き", "nuki"),
    "kyuushu": ("九种九牌", "九種九牌"),
}


def parse_buttons(text: str, self_draw: bool | None = None) -> frozenset[str]:
    lower = text.lower().replace(" ", "")
    # English action names need token boundaries: "riichi" contains "chi".
    found = {key for key, words in BUTTON_WORDS.items() if any(
        re.search(r"(?<![a-z])" + re.escape(word.lower()) + r"(?![a-z])", text.lower())
        if word.isascii() else word.lower() in lower
        for word in words
    )}
    if "和" in lower and not found & {"ron", "tsumo"} and self_draw is not None:
        found.add("tsumo" if self_draw else "ron")
    if found & {"chi", "pon", "kan", "ron"}:
        found.add("pass")
    return frozenset(found)


class VisionReader:
    def __init__(self, regions: dict[str, tuple[float, float, float, float]], templates: TemplateStore | None = None):
        self.regions = regions
        self.templates = templates or TemplateStore()
        self._ocr = None
        self._previous_rivers: tuple[tuple[str, ...], ...] | None = None
        self._last_discard: str | None = None
        self._riichi_latched: list[bool | None] = [None] * 4
        self._last_scores: list[int | None] = [None] * 4
        self._last_round_wind = "?"
        self._last_round_number: int | None = None
        self._last_honba = 0
        self._last_meta_scan = 0.0

    @staticmethod
    def _riichi_stick_present(image: np.ndarray) -> bool:
        if image.size == 0:
            return False
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        hue, saturation, value = cv2.split(hsv)
        red = ((hue <= 12) | (hue >= 170)) & (saturation >= 110) & (value >= 90)
        return float(np.mean(red)) >= 0.07

    def _ocr_text(self, image: np.ndarray) -> tuple[str, float]:
        if image.size == 0:
            return "", 0.0
        if self._ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            self._ocr = RapidOCR()
        result, _ = self._ocr(image)
        if not result:
            return "", 0.0
        text = " ".join(item[1] for item in result)
        action_scores = [
            float(item[2]) for item in result
            if any(word.lower() in item[1].lower() for words in BUTTON_WORDS.values() for word in words)
        ]
        # Timers and decorative text often share this crop. Their OCR score must not
        # make a correctly recognized action button look uncertain.
        scores = action_scores or [float(item[2]) for item in result]
        return text, min(scores)

    def _ocr_sections(self, frame: np.ndarray, names: list[str]) -> dict[str, str]:
        available = [(name, crop_region(frame, self.regions[name])) for name in names if name in self.regions]
        available = [(name, crop) for name, crop in available if crop.size]
        if not available:
            return {}
        width = 360
        bands: list[tuple[str, int, int]] = []
        images: list[np.ndarray] = []
        cursor = 0
        for name, crop in available:
            height = max(40, round(crop.shape[0] * min(3.0, width / max(1, crop.shape[1]))))
            resized = cv2.resize(crop, (width, height), interpolation=cv2.INTER_CUBIC)
            images.append(resized)
            bands.append((name, cursor, cursor + height))
            cursor += height + 8
            images.append(np.full((8, width, 3), 255, dtype=np.uint8))
        mosaic = np.vstack(images)
        if self._ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            self._ocr = RapidOCR()
        result, _ = self._ocr(mosaic)
        texts = {name: [] for name, _, _ in bands}
        for item in result or []:
            box, text = item[0], item[1]
            mid_y = sum(point[1] for point in box) / len(box)
            for name, top, bottom in bands:
                if top <= mid_y < bottom:
                    texts[name].append(text)
                    break
        return {name: " ".join(parts) for name, parts in texts.items()}

    def _tiles(self, frame: np.ndarray, name: str) -> tuple[list[str], float, int]:
        if name not in self.regions:
            return [], 0.0, 0
        _, _, cols, rows, _ = REGION_INFO[name]
        cells = region_cells(frame, self.regions[name], cols, rows)
        if not cells:
            return [], 0.0, 0
        tiles: list[str] = []
        confidences: list[float] = []
        unknown = 0
        matches = self.templates.match_batch(cells)
        for match in matches:
            if match.label:
                tiles.append(match.label)
                confidences.append(match.confidence)
            elif match.occupied:
                unknown += 1
        return tiles, min(confidences, default=0.0), unknown

    def analyze(self, frame: np.ndarray, seat: int = 0) -> Observation:
        # Commit temporal state only after the complete frame passes validation.
        history = (self._previous_rivers, self._last_discard, list(self._riichi_latched),
                   list(self._last_scores), self._last_round_wind, self._last_round_number,
                   self._last_honba, self._last_meta_scan)
        problems: list[str] = []
        debug: dict[str, Any] = {}
        missing = [name for name, _, _, _, required in REGIONS if required and name not in self.regions]
        if missing:
            return Observation(None, 0.0, ("未校准区域：" + ", ".join(missing),), {})
        hand, hand_conf, hand_unknown = self._tiles(frame, "hand")
        draw, draw_conf, draw_unknown = self._tiles(frame, "draw")
        if hand_unknown or draw_unknown:
            problems.append(f"有 {hand_unknown + draw_unknown} 张手牌无法识别")
        if len(draw) > 1:
            problems.append("摸牌区识别了多张牌")
        rivers = []
        melds = []
        other_confidences = []
        for prefix, target in (("river", rivers), ("meld", melds)):
            for i in range(4):
                tiles, confidence, unknown = self._tiles(frame, f"{prefix}_{i}")
                target.append(tiles)
                if unknown:
                    problems.append(f"{prefix}_{i} 有 {unknown} 张牌无法识别")
                if tiles:
                    other_confidences.append(confidence)
        dora, dora_conf, dora_unknown = self._tiles(frame, "dora")
        if dora_unknown:
            problems.append("宝牌指示牌无法识别")
        if dora:
            other_confidences.append(dora_conf)
        own_melds = len(melds[seat]) // 3
        own_hand = hand + draw
        button_image = crop_region(frame, self.regions["buttons"])
        button_text, button_conf = self._ocr_text(button_image)
        buttons = parse_buttons(button_text, len(own_hand) == 14 - 3 * own_melds)
        debug["button_text"] = button_text
        if buttons and button_conf < 0.70:
            problems.append("操作按钮 OCR 置信度不足")
        now = time.monotonic()
        if now - self._last_meta_scan >= 10.0:
            self._last_meta_scan = now
            sections = self._ocr_sections(frame, [f"score_{i}" for i in range(4)] + ["round"])
            for i in range(4):
                key = f"score_{i}"
                digits = re.sub(r"\D", "", sections.get(key, ""))
                if digits:
                    self._last_scores[i] = int(digits)
            if "round" in sections:
                round_text = sections["round"]
                debug["round_text"] = round_text
                if "南" in round_text or "S" in round_text.upper():
                    self._last_round_wind = "S"
                elif "东" in round_text or "東" in round_text or "E" in round_text.upper():
                    self._last_round_wind = "E"
                number_match = re.search(r"[東东南ES]\s*([1-4一二三四])\s*局", round_text, re.IGNORECASE)
                if number_match:
                    self._last_round_number = {"一": 1, "二": 2, "三": 3, "四": 4}.get(
                        number_match.group(1), int(number_match.group(1)) if number_match.group(1).isdigit() else None
                    )
                honba_match = re.search(r"(\d+)\s*本", round_text)
                if honba_match:
                    self._last_honba = int(honba_match.group(1))
        riichi = list(self._riichi_latched)
        # A riichi stick remains visible for the rest of the hand; latch across frames.
        for i in range(1, 4):
            key = f"riichi_{i}"
            if key in self.regions:
                riichi[i] = bool(riichi[i]) or self._riichi_stick_present(crop_region(frame, self.regions[key]))
        confidence = min([hand_conf] + ([draw_conf] if draw else []) + other_confidences) if hand else 0.0
        if not hand:
            problems.append("未识别到手牌")
        if problems:
            confidence = min(confidence, 0.5)
        river_tuple = tuple(tuple(x) for x in rivers)
        if self._previous_rivers:
            if sum(map(len, river_tuple)) == 0 and sum(map(len, self._previous_rivers)) > 0:
                self._last_discard = None
                self._last_round_wind = "?"
                self._last_round_number = None
                self._last_honba = 0
                self._last_scores = [None] * 4
                riichi = [None if f"riichi_{i}" not in self.regions else False for i in range(4)]
                self._last_meta_scan = 0.0
            else:
                changed = [i for i in range(4) if river_tuple[i] != self._previous_rivers[i]]
                if len(changed) == 1:
                    i = changed[0]
                    previous = self._previous_rivers[i]
                    if len(river_tuple[i]) == len(previous) + 1 and river_tuple[i][:-1] == previous:
                        self._last_discard = river_tuple[i][-1]
                    else:
                        self._last_discard = None
                elif changed:
                    # Multiple changes between frames do not reveal discard order.
                    self._last_discard = None
                if any(len(river_tuple[i]) < len(self._previous_rivers[i]) for i in range(4)):
                    problems.append("牌河发生缩短，请等待稳定画面或手动核对")
        self._previous_rivers = river_tuple
        self._riichi_latched = riichi
        last_discard = self._last_discard
        try:
            state = GameState.from_dict({
                "hand": own_hand,
                "rivers": rivers,
                "melds": melds,
                "dora_indicators": dora,
                "buttons": list(buttons),
                "last_discard": last_discard,
                "seat": seat,
                "round_wind": self._last_round_wind,
                "round_number": self._last_round_number,
                "scores": self._last_scores,
                "riichi": riichi,
                "honba": self._last_honba,
                "open_melds": own_melds,
                "observation_confidence": max(0.0, min(1.0, confidence)),
            })
        except ValueError as error:
            problems.append(str(error))
            state = None
        if problems or state is None or confidence < 0.90:
            (self._previous_rivers, self._last_discard, self._riichi_latched,
             self._last_scores, self._last_round_wind, self._last_round_number,
             self._last_honba, self._last_meta_scan) = history
        debug["recognized_hand"] = own_hand
        debug["recognized_buttons"] = sorted(buttons)
        return Observation(state, confidence, tuple(problems), debug)
