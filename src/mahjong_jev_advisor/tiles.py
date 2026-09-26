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
