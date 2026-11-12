"""Tile notation and counting. 0m/0p/0s denote red fives."""

from __future__ import annotations

import re
from collections import Counter

TILES = tuple(f"{n}{s}" for s in "mps" for n in range(1, 10)) + tuple(
    f"{n}z" for n in range(1, 8)
)
RED_TILES = ("0m", "0p", "0s")
TILE_RE = re.compile(r"^(?:[1-9][mps]|[1-7]z|0[mps])$")
TILE_INDEX = {tile: i for i, tile in enumerate(TILES)}


def valid(tile: str) -> bool:
    return bool(TILE_RE.fullmatch(tile))


def normal(tile: str) -> str:
    if not valid(tile):
        raise ValueError(f"Invalid tile: {tile!r}")
    return "5" + tile[1] if tile[0] == "0" else tile


def index(tile: str) -> int:
    return TILE_INDEX[normal(tile)]


def counts34(tiles: list[str] | tuple[str, ...]) -> list[int]:
    result = [0] * 34
    for tile in tiles:
        result[index(tile)] += 1
    return result


def parse_tiles(value: str) -> tuple[str, ...]:
    """Accept `1m 2m 0p` or compact `123m405p77z` notation."""
    value = value.strip().lower()
    if not value:
        return ()
    if re.search(r"[\s,]", value):
        result = tuple(part for part in re.split(r"[\s,]+", value) if part)
    else:
        groups = re.findall(r"([0-9]+)([mpsz])", value)
        if "".join(ns + s for ns, s in groups) != value:
            raise ValueError(f"Invalid tile notation: {value!r}")
        result = tuple(n + s for ns, s in groups for n in ns)
    for tile in result:
        if not valid(tile):
            raise ValueError(f"Invalid tile: {tile!r}")
    return result


def validate_physical_tiles(tiles: list[str] | tuple[str, ...]) -> None:
    count = Counter(normal(tile) for tile in tiles)
    over = {tile: number for tile, number in count.items() if number > 4}
    if over:
        raise ValueError(f"More than four copies visible: {over}")
    for red in RED_TILES:
        if tiles.count(red) > 1:
            raise ValueError(f"More than one red five visible: {red}")


def sort_key(tile: str) -> tuple[int, int]:
    """Mahjong Soul hand sort order: m, p, s, z; red five (0) precedes normal five (5)."""
    return index(tile), (0 if tile.startswith("0") else 1)


def sort_hand(tiles: list[str] | tuple[str, ...]) -> list[str]:
    """Return tiles sorted in Mahjong Soul concealed-hand order."""
    return sorted(tiles, key=sort_key)


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
    found = {
        key for key, words in BUTTON_WORDS.items()
        if any(
            re.search(r"(?<![a-z])" + re.escape(word.lower()) + r"(?![a-z])", text.lower())
            if word.isascii() else word.lower() in lower
            for word in words
        )
    }
    if "和" in lower and not found & {"ron", "tsumo"} and self_draw is not None:
        found.add("tsumo" if self_draw else "ron")
    if found & {"chi", "pon", "kan", "ron"}:
        found.add("pass")
    return frozenset(found)

