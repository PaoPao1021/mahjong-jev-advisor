from __future__ import annotations

import numpy as np

from mahjong_jev_advisor.tiles import TILES
from mahjong_jev_advisor.vision import TemplateStore, VisionReader, parse_buttons


def test_multilingual_action_buttons():
    assert parse_buttons("立直 过") == {"riichi", "pass"}
    assert parse_buttons("ロン パス") == {"ron", "pass"}
    assert parse_buttons("和", self_draw=True) == {"tsumo"}
    assert parse_buttons("和", self_draw=False) == {"ron"}


def test_template_roundtrip(tmp_path):
    store = TemplateStore(tmp_path)
    image = np.zeros((80, 60, 3), dtype=np.uint8)
    image[15:70, 12:48] = 220
    image[25:52, 24:36] = 35
    store.add("1m", image)
    result = store.match(image)
    assert result.label == "1m" and result.confidence > 0.99


def test_missing_calibration_abstains():
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    obs = VisionReader({}).analyze(frame)
    assert obs.state is None and obs.problems


def test_riichi_stick_color_detection():
    green = np.zeros((20, 100, 3), dtype=np.uint8)
    green[:, :] = (0, 110, 0)
    red = green.copy()
    red[5:15, 10:90] = (0, 0, 220)
    assert not VisionReader._riichi_stick_present(green)
    assert VisionReader._riichi_stick_present(red)


def test_calibrated_synthetic_frame_recovers_complete_hand(tmp_path):
    rng = np.random.default_rng(7)
    frame = np.zeros((800, 1500, 3), dtype=np.uint8)
    store = TemplateStore(tmp_path)
    labels = list(TILES[:13]) + ["5p", "7p", "empty"]
    patterns = {label: rng.integers(20, 235, size=(80, 60, 3), dtype=np.uint8) for label in labels}
    for label, pattern in patterns.items():
        store.add(label, pattern)
    boxes = {
        "hand": (0, 0, 780, 80, 13, 1), "draw": (800, 0, 60, 80, 1, 1),
        "river_0": (0, 100, 360, 320, 6, 4),
        "river_1": (380, 100, 360, 320, 6, 4),
        "river_2": (760, 100, 360, 320, 6, 4),
        "river_3": (1140, 100, 360, 320, 6, 4),
        "meld_0": (0, 450, 240, 320, 4, 4),
        "dora": (300, 450, 300, 80, 5, 1),
        "buttons": (650, 450, 200, 80, 1, 1),
        "riichi_1": (900, 450, 80, 80, 1, 1),
        "riichi_2": (1000, 450, 80, 80, 1, 1),
        "riichi_3": (1100, 450, 80, 80, 1, 1),
    }
    regions = {}
    for name, (x, y, width, height, cols, rows) in boxes.items():
        regions[name] = (x / 1500, y / 800, width / 1500, height / 800)
        if name in {"buttons", "riichi_1", "riichi_2", "riichi_3"}:
            continue
        sequence = list(TILES[:13]) if name == "hand" else ["5p"] if name == "draw" else ["7p"] if name == "dora" else []
        for index in range(cols * rows):
            label = sequence[index] if index < len(sequence) else "empty"
            cx = x + (index % cols) * (width // cols)
            cy = y + (index // cols) * (height // rows)
            frame[cy:cy + 80, cx:cx + 60] = patterns[label]
    reader = VisionReader(regions, store)
    reader._ocr_text = lambda image: ("", 1.0)
    observation = reader.analyze(frame)
    assert not observation.problems
    assert observation.state is not None
    assert observation.state.hand == tuple(TILES[:13]) + ("5p",)
    assert observation.state.dora_indicators == ("7p",)
    assert observation.confidence > 0.99
