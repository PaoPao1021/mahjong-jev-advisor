"""Validated, public-only state and decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .tiles import parse_tiles, validate_physical_tiles, valid

BUTTONS = frozenset({"riichi", "chi", "pon", "kan", "ron", "tsumo", "pass", "kyuushu"})


@dataclass(frozen=True)
class GameState:
    hand: tuple[str, ...] = ()
    rivers: tuple[tuple[str, ...], ...] = ((), (), (), ())
    melds: tuple[tuple[str, ...], ...] = ((), (), (), ())
    dora_indicators: tuple[str, ...] = ()
    buttons: frozenset[str] = frozenset()
    last_discard: str | None = None
    seat: int = 0
    round_wind: str = "?"
    round_number: int | None = None
    seat_wind: str = "?"
    scores: tuple[int | None, ...] = (None, None, None, None)
    riichi: tuple[bool | None, ...] = (None, None, None, None)
    honba: int = 0
    sticks: int = 0
    open_melds: int = 0
    observation_confidence: float = 1.0

    def validate(self) -> None:
        if len(self.rivers) != 4 or len(self.melds) != 4 or len(self.scores) != 4 or len(self.riichi) != 4:
            raise ValueError("A four-player state needs four rivers, meld sets, scores and riichi flags")
        if not 0 <= self.seat < 4 or self.round_wind not in "ES?" or self.seat_wind not in "ESWN?":
            raise ValueError("Invalid seat or wind")
        if self.round_number is not None and not 1 <= self.round_number <= 4:
            raise ValueError("Round number must be from 1 to 4")
        if not 0 <= self.open_melds <= 4:
            raise ValueError("Invalid open meld count")
        if any(flag is not None and not isinstance(flag, bool) for flag in self.riichi):
            raise ValueError("Riichi flags must be true, false or unknown")
        if self.buttons - BUTTONS:
            raise ValueError(f"Unknown action buttons: {self.buttons - BUTTONS}")
        if not 0 <= self.observation_confidence <= 1:
            raise ValueError("Observation confidence must be between 0 and 1")
        all_tiles = list(self.hand) + list(self.dora_indicators)
        for group in self.rivers + self.melds:
            all_tiles.extend(group)
        for tile in all_tiles:
            if not valid(tile):
                raise ValueError(f"Invalid tile: {tile}")
        # A physical copy may appear both in the discard river and in a meld after a call.
        validate_physical_tiles(list(self.hand) + list(self.dora_indicators))
        if self.last_discard is not None and not valid(self.last_discard):
            raise ValueError("Invalid last discard")
        if self.hand:
            expected = {13 - 3 * self.open_melds, 14 - 3 * self.open_melds}
            if len(self.hand) not in expected:
                raise ValueError(f"Hand has {len(self.hand)} tiles, expected one of {expected}")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameState":
        def tile_group(value: Any) -> tuple[str, ...]:
            return parse_tiles(value) if isinstance(value, str) else tuple(value)

        rivers = tuple(tile_group(x) for x in data.get("rivers", [[], [], [], []]))
        melds = tuple(tile_group(x) for x in data.get("melds", [[], [], [], []]))
        obj = cls(
            hand=tile_group(data.get("hand", [])),
            rivers=rivers,
            melds=melds,
            dora_indicators=tile_group(data.get("dora_indicators", [])),
            buttons=frozenset(data.get("buttons", [])),
            last_discard=data.get("last_discard"),
            seat=int(data.get("seat", 0)),
            round_wind=str(data.get("round_wind", "?")).upper(),
            round_number=int(data["round_number"]) if data.get("round_number") is not None else None,
            seat_wind=str(data.get("seat_wind", "?")).upper(),
            scores=tuple(int(x) if x is not None else None for x in data.get("scores", [None] * 4)),
            riichi=tuple(None if x is None else bool(x) for x in data.get("riichi", [None] * 4)),
            honba=int(data.get("honba", 0)),
            sticks=int(data.get("sticks", 0)),
            open_melds=int(data.get("open_melds", 0)),
            observation_confidence=float(data.get("observation_confidence", 1.0)),
        )
        obj.validate()
        return obj

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["buttons"] = sorted(self.buttons)
        return data

    def identity(self) -> tuple[Any, ...]:
        return (
            self.hand, self.rivers, self.melds, self.dora_indicators,
            tuple(sorted(self.buttons)), self.last_discard, self.seat,
            self.round_wind, self.round_number, self.seat_wind, self.scores, self.riichi,
            self.honba, self.sticks, self.open_melds,
        )


@dataclass(frozen=True)
class Candidate:
    action: str
    tile: str | None
    label: str
    rationale: str
    shanten: int | None = None
    ukeire: int | None = None
    danger: int | None = None
    dora_loss: int = 0
    meld_tiles: tuple[str, ...] = ()
    improving_tiles: tuple[str, ...] = ()
    potential_yaku: tuple[str, ...] = ()
    expected_han: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Advice:
    selected: Candidate
    alternatives: tuple[Candidate, ...] = ()
    source: str = "rules"
    model: str | None = None
    confidence: float | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    latency_ms: int = 0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data
