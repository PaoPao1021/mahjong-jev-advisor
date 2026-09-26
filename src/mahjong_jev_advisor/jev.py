"""Bounded, typed TypeSafe Jev choice request."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .rules import local_choice
from .state import Advice, Candidate, GameState

ENDPOINT = "https://api.typesafe.ai/v1/systemone"


class JevError(Exception):
    pass


class JevAuthError(JevError):
    pass


def _request_body(state: GameState, options: tuple[Candidate, ...]) -> dict:
    return {
        "model": "jev-latest",
        "state": {
            "game": "four-player Japanese riichi mahjong",
            "hand": list(state.hand),
            "visible_discards": [list(x) for x in state.rivers],
            "visible_melds": [list(x) for x in state.melds],
            "dora_indicators": list(state.dora_indicators),
            "last_discard": state.last_discard,
            "scores": list(state.scores),
            "riichi_players": list(state.riichi),
            "round_wind": state.round_wind,
            "round_number": state.round_number,
            "seat_wind": state.seat_wind,
            "honba": state.honba,
            "riichi_sticks": state.sticks,
        },
        "questions": {
            "action": {
                "type": "choice",
                "instructions": (
                    "Choose the strongest legal action for the current player. "
                    "The options have already passed a deterministic legality and basic efficiency gate. "
                    "Balance hand value, tile efficiency, deal-in risk and table position. "
                    "Never assume hidden opponent tiles are known."
                ),
                "criteria": {
                    f"a{i}": {
                        "action": item.action,
                        "tile": item.tile,
                        "description": item.label,
                        "rule_analysis": item.rationale,
                        "shanten": item.shanten,
                        "ukeire": item.ukeire,
                        "relative_danger": item.danger,
                        "dora_loss": item.dora_loss,
                        "meld_tiles": list(item.meld_tiles),
                        "improving_tiles": list(item.improving_tiles),
                        "potential_yaku": list(item.potential_yaku),
                        "expected_han": item.expected_han,
                    }
                    for i, item in enumerate(options)
                },
            }
        },
    }


def ask_jev(state: GameState, options: tuple[Candidate, ...], api_key: str, timeout: float = 0.9) -> Advice:
    if not api_key:
        raise JevError("未配置 TypeSafe API Key")
    started = time.perf_counter()
    body = json.dumps(_request_body(state, options), ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise JevAuthError("TypeSafe API Key 无效或没有 Jev 访问权限") from error
        raise JevError(f"Jev HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
        raise JevError(str(error)) from error
    try:
        answer = data.get("answers", {}).get("action", {})
        key = answer.get("choice")
        mapping = {f"a{i}": option for i, option in enumerate(options)}
        if key not in mapping:
            raise JevError(f"Jev returned an unknown action: {key!r}")
        probabilities = {
            k: float(v) for k, v in answer.get("probabilities", {}).items()
            if k in mapping and isinstance(v, (int, float))
        }
        confidence = answer.get("confidence")
        if confidence is not None:
            confidence = float(confidence)
            if not 0 <= confidence <= 1:
                confidence = None
    except (AttributeError, TypeError, ValueError) as error:
        raise JevError(f"Invalid Jev response: {error}") from error
    return Advice(
        selected=mapping[key],
        alternatives=tuple(x for x in options if x != mapping[key])[:3],
        source="jev",
        model=str(data.get("model", "jev-latest")),
        confidence=confidence,
        probabilities=probabilities,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


def advise(state: GameState, options: tuple[Candidate, ...], api_key: str) -> Advice:
    if not api_key:
        raise JevError("请先配置 TypeSafe API Key，再启用 Jev 决策")
    if len(options) == 1:
        return Advice(options[0], source="rules", note="确定性规则")
    try:
        return ask_jev(state, options, api_key)
    except JevAuthError:
        raise
    except JevError as error:
        selected = local_choice(options, state)
        return Advice(
            selected, tuple(x for x in options if x != selected)[:3],
            source="rules", note=f"Jev 不可用：{error}",
        )
