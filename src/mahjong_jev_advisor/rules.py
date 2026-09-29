"""Deterministic legal-action gate and local advice baseline."""

from __future__ import annotations

from collections import Counter
from functools import lru_cache

from mahjong.shanten import Shanten

from .state import Candidate, GameState
from .tiles import TILES, counts34, index, normal
from .yaku import analyze_yaku_potential, estimate_tenpai_value, evaluate_call_safety


class NoDecision(Exception):
    pass


class UncertainState(Exception):
    pass


@lru_cache(maxsize=16384)
def _shanten_cached(counts: tuple[int, ...], open_melds: int) -> int:
    return Shanten().calculate_shanten(
        counts, use_chiitoitsu=open_melds == 0, use_kokushi=open_melds == 0
    )


def shanten(tiles: tuple[str, ...] | list[str], open_melds: int = 0) -> int:
    return _shanten_cached(tuple(counts34(tiles)), open_melds)


def _visible(state: GameState) -> list[int]:
    all_tiles = list(state.hand) + list(state.dora_indicators)
    for river in state.rivers:
        all_tiles.extend(river)
    for meld in state.melds:
        all_tiles.extend(meld)
    return counts34(all_tiles)


def ukeire_details(tiles_after_discard: tuple[str, ...], state: GameState) -> tuple[int, tuple[str, ...]]:
    base = shanten(tiles_after_discard, state.open_melds)
    visible = _visible(state)
    hand_counts = counts34(tiles_after_discard)
    total = 0
    improving: list[str] = []
    for i, tile in enumerate(TILES):
        remaining = max(0, 4 - visible[i])
        if remaining == 0 or hand_counts[i] >= 4:
            continue
        test = list(hand_counts)
        test[i] += 1
        if _shanten_cached(tuple(test), state.open_melds) < base:
            total += remaining
            improving.append(tile)
    return total, tuple(improving)


def ukeire(tiles_after_discard: tuple[str, ...], state: GameState) -> int:
    total, _ = ukeire_details(tiles_after_discard, state)
    return total


def danger(tile: str, state: GameState) -> int:
    """Relative danger bucket (0 to 6+) based on modern riichi defense theory (Genbutsu, Suji, Kabe)."""
    threats = [i for i, flag in enumerate(state.riichi) if flag and i != state.seat]
    if not threats:
        return 0

    t_norm = normal(tile)
    n, suit = int(t_norm[0]), t_norm[1]

    # Genbutsu against all riichi opponents is completely safe (0 deal-in rate)
    if all(t_norm in {normal(x) for x in state.rivers[i]} for i in threats):
        return 0

    # Honor tiles (字牌)
    if suit == "z":
        vis_count = _visible(state)[index(t_norm)]
        if vis_count >= 4:
            return 0  # 4 visible = completely safe (绝张)
        if vis_count == 3:
            return 1  # 3 visible = 1 remaining, only tanki wait possible
        if vis_count == 2:
            return 2
        dora_extra = 2 if t_norm in {dora_from_indicator(x) for x in state.dora_indicators} else 0
        return 3 + dora_extra + len(threats) - 1

    # Number tiles (数牌) Suji analysis
    def suji_status(p_seat: int) -> int:
        river_tiles = {normal(x) for x in state.rivers[p_seat]}
        if t_norm in river_tiles:
            return 3  # Genbutsu
        if n == 1:
            return 2 if f"4{suit}" in river_tiles else 0
        elif n == 2:
            return 2 if f"5{suit}" in river_tiles else 0
        elif n == 3:
            return 2 if f"6{suit}" in river_tiles else 0
        elif n == 4:
            has_1 = f"1{suit}" in river_tiles
            has_7 = f"7{suit}" in river_tiles
            return 2 if (has_1 and has_7) else (1 if (has_1 or has_7) else 0)
        elif n == 5:
            has_2 = f"2{suit}" in river_tiles
            has_8 = f"8{suit}" in river_tiles
            return 2 if (has_2 and has_8) else (1 if (has_2 or has_8) else 0)
        elif n == 6:
            has_3 = f"3{suit}" in river_tiles
            has_9 = f"9{suit}" in river_tiles
            return 2 if (has_3 and has_9) else (1 if (has_3 or has_9) else 0)
        elif n == 7:
            return 2 if f"4{suit}" in river_tiles else 0
        elif n == 8:
            return 2 if f"5{suit}" in river_tiles else 0
        elif n == 9:
            return 2 if f"6{suit}" in river_tiles else 0
        return 0

    ratings = [suji_status(p) for p in threats]
    if all(r == 3 for r in ratings):
        return 0

    min_r = min(ratings)
    if min_r == 2:
        # Full suji against all threats (1/9 suji = 1, 2/3/7/8 suji = 2, double-suji 4/5/6 = 3)
        score = 1 if n in (1, 9) else (2 if n in (2, 3, 7, 8) else 3)
    elif min_r == 1:
        # Half suji
        score = 3 if n in (1, 9) else 4
    else:
        # Non-suji (无筋)
        score = 3 if n in (1, 9) else (4 if n in (2, 8) else 5)

    if t_norm in {dora_from_indicator(x) for x in state.dora_indicators}:
        score += 2
    return score + len(threats) - 1


def dora_from_indicator(tile: str) -> str:
    tile = normal(tile)
    n, suit = int(tile[0]), tile[1]
    if suit != "z":
        return f"{1 if n == 9 else n + 1}{suit}"
    if n <= 4:
        return f"{1 if n == 4 else n + 1}z"
    return f"{5 if n == 7 else n + 1}z"


def dora_value(tile: str, state: GameState) -> int:
    return int(tile[0] == "0") + sum(
        normal(tile) == dora_from_indicator(indicator) for indicator in state.dora_indicators
    )


def yaku_hints(state: GameState) -> str:
    hints: list[str] = []
    values = {"E": "1z", "S": "2z", "W": "3z", "N": "4z"}
    honor_counts = Counter(normal(tile) for tile in state.hand if tile[1] == "z")
    for wind in (state.round_wind, state.seat_wind):
        tile = values.get(wind)
        if tile and honor_counts[tile] >= 2 and "役牌候选" not in hints:
            hints.append("役牌候选")
    if any(honor_counts[tile] >= 2 for tile in ("5z", "6z", "7z")):
        hints.append("三元牌候选")
    if all(tile[1] != "z" and int(normal(tile)[0]) not in (1, 9) for tile in state.hand):
        hints.append("断幺九候选")
    if state.open_melds == 0:
        hints.append("门清")
    return "、".join(hints) if hints else "暂未识别明确役种"


def shanten_text(s: int) -> str:
    if s == 0:
        return "听牌"
    if s == 1:
        return "一向听"
    if s == 2:
        return "二向听"
    if s == 3:
        return "三向听"
    return f"{s}向听"


def _discard_candidates(state: GameState) -> list[Candidate]:
    seen: set[str] = set()
    scored: list[Candidate] = []
    for tile in state.hand:
        if tile in seen:
            continue
        seen.add(tile)
        rest = list(state.hand)
        rest.remove(tile)
        after = tuple(rest)
        s = shanten(after, state.open_melds)
        u, imp_tiles = ukeire_details(after, state)
        d = danger(tile, state)
        dora = dora_value(tile, state)

        if imp_tiles:
            preview_str = " ".join(imp_tiles[:6]) + (f" 等{len(imp_tiles)}种" if len(imp_tiles) > 6 else "")
            wait_desc = f"{'待牌' if s == 0 else '进张'} {preview_str} 约 {u} 张"
        else:
            wait_desc = f"有效牌约 {u} 张"

        s_desc = shanten_text(s)
        if s == 0:
            min_h, max_h, avg_score, tenpai_yaku = estimate_tenpai_value(state, after, imp_tiles)
            expected_han = max_h
            potential_yaku = tuple(tenpai_yaku)
            if max_h > 0:
                han_str = f"{min_h}番" if min_h == max_h else f"{min_h}~{max_h}番"
                score_str = f"约 {avg_score} 点" if avg_score > 0 else ""
                yaku_str = "、".join(tenpai_yaku[:3]) if tenpai_yaku else ""
                val_desc = f"预计 {han_str}{(' (' + score_str + ')') if score_str else ''}{(' · ' + yaku_str) if yaku_str else ''}"
            else:
                val_desc = "形式听牌 (无役)"
            yaku_summary = val_desc
        else:
            expected_han = None
            potential = analyze_yaku_potential(state, after)
            potential_yaku = tuple(potential[:4])
            yaku_summary = "、".join(potential[:3]) if potential else yaku_hints(state)

        scored.append(Candidate(
            "discard", tile, f"打 {tile}",
            f"{s_desc} · {wait_desc} · 危险级 {d} · 弃宝牌 {dora} 张 · {yaku_summary}",
            s, u, d, dora,
            improving_tiles=imp_tiles,
            potential_yaku=potential_yaku,
            expected_han=expected_han,
        ))
    threatened = any(state.riichi[i] for i in range(4) if i != state.seat)
    if threatened:
        scored.sort(key=lambda c: (c.danger, c.shanten, -c.ukeire, c.dora_loss, index(c.tile)))
    else:
        scored.sort(key=lambda c: (c.shanten, -c.ukeire, c.dora_loss, c.danger, index(c.tile)))
    top = scored[:3]
    if "riichi" in state.buttons and state.open_melds == 0:
        riichi_tiles = [c for c in scored if c.shanten == 0][:3]
        for c in riichi_tiles:
            rest = list(state.hand)
            rest.remove(c.tile)
            after = tuple(rest)
            min_h, max_h, avg_score, r_yaku = estimate_tenpai_value(state, after, c.improving_tiles, is_riichi=True)
            han_str = f"{min_h}番" if min_h == max_h else f"{min_h}~{max_h}番"
            score_str = f"约 {avg_score} 点" if avg_score > 0 else ""
            yaku_str = "、".join(r_yaku[:3]) if r_yaku else "立直"
            wait_str = " ".join(c.improving_tiles[:4]) if c.improving_tiles else ""
            wait_text = f"待牌 {wait_str} " if wait_str else ""
            top.append(Candidate(
                "riichi", c.tile, f"立直打 {c.tile}",
                f"立直听牌 · {wait_text}约 {c.ukeire} 张 · 预计 {han_str}{(' (' + score_str + ')') if score_str else ''} · {yaku_str} · 危险级 {c.danger}",
                c.shanten, c.ukeire, c.danger, c.dora_loss,
                improving_tiles=c.improving_tiles,
                potential_yaku=tuple(r_yaku),
                expected_han=max_h,
            ))
    if "kyuushu" in state.buttons:
        top.append(Candidate("kyuushu", None, "九种九牌流局", "当前界面允许九种九牌流局"))
    if "kan" in state.buttons:
        top.append(Candidate("kan", None, "杠", "当前界面允许杠；需在游戏内确认杠牌组合"))
    return top


def _chi_options(state: GameState) -> list[Candidate]:
    if not state.last_discard or state.last_discard[1] == "z":
        raise UncertainState("吃牌按钮与最近弃牌矛盾，请核对最近弃牌")
    called = normal(state.last_discard)
    n, suit = int(called[0]), called[1]
    have = Counter(normal(t) for t in state.hand)
    result: list[Candidate] = []
    for start in range(max(1, n - 2), min(7, n) + 1):
        run = tuple(f"{v}{suit}" for v in range(start, start + 3))
        need = list(run)
        need.remove(called)
        if all(have[t] >= need.count(t) for t in need):
            cand_temp = Candidate("chi", state.last_discard, f"吃 {' '.join(run)}", "", meld_tiles=run)
            is_safe, safety_msg, y_list = evaluate_call_safety(state, cand_temp)
            result.append(Candidate(
                "chi", state.last_discard, f"吃 {' '.join(run)}",
                f"吃 {' '.join(run)} · {safety_msg}",
                meld_tiles=run,
                potential_yaku=tuple(y_list),
            ))
    if not result:
        raise UncertainState("吃牌按钮与手牌组合矛盾，请核对手牌")
    return result


def _call_candidates(state: GameState) -> list[Candidate]:
    have = Counter(normal(t) for t in state.hand)
    for action, required in (("pon", 2), ("kan", 3)):
        if action in state.buttons and (not state.last_discard or have[normal(state.last_discard)] < required):
            raise UncertainState("碰杠按钮与手牌或最近弃牌矛盾，请手动核对")
    result = [Candidate("pass", None, "过", "保留门清与当前手牌形状")]
    if "chi" in state.buttons:
        result.extend(_chi_options(state))
    if "pon" in state.buttons:
        meld_t = (normal(state.last_discard),) * 3 if state.last_discard else ()
        cand_temp = Candidate("pon", state.last_discard, "碰", "", meld_tiles=meld_t)
        is_safe, safety_msg, y_list = evaluate_call_safety(state, cand_temp)
        result.append(Candidate(
            "pon", state.last_discard, "碰", f"碰 · {safety_msg}",
            meld_tiles=meld_t,
            potential_yaku=tuple(y_list),
        ))
    if "kan" in state.buttons:
        meld_t = (normal(state.last_discard),) * 4 if state.last_discard else ()
        cand_temp = Candidate("kan", state.last_discard, "杠", "", meld_tiles=meld_t)
        is_safe, safety_msg, yaku_list = evaluate_call_safety(state, cand_temp)
        result.append(Candidate(
            "kan", state.last_discard, "杠", f"杠 · {safety_msg}",
            meld_tiles=meld_t,
            potential_yaku=tuple(yaku_list),
        ))
    return result


def candidates(state: GameState) -> tuple[Candidate, ...]:
    state.validate()
    if state.observation_confidence < 0.90:
        raise UncertainState("识别置信度不足 0.90，请手动核对牌面")
    for action, label in (("ron", "荣和"), ("tsumo", "自摸")):
        if action in state.buttons:
            return (Candidate(action, None, label, "游戏界面确认可以和牌"),)
    if not state.hand:
        raise NoDecision("未识别到手牌")
    drawn_count = 14 - 3 * state.open_melds
    if len(state.hand) == drawn_count:
        result = _discard_candidates(state)
        if result:
            return tuple(result)
    if state.buttons & {"chi", "pon", "kan", "pass"}:
        return tuple(_call_candidates(state))
    raise NoDecision("当前没有可建议的操作")


def _remove_normal(tiles: list[str], target: str) -> bool:
    for i, tile in enumerate(tiles):
        if normal(tile) == normal(target):
            tiles.pop(i)
            return True
    return False


def _call_yaku_available(state: GameState, candidate: Candidate) -> bool:
    is_safe, _, _ = evaluate_call_safety(state, candidate)
    return is_safe


def _call_shanten_after_discard(state: GameState, candidate: Candidate) -> int | None:
    if candidate.action not in {"chi", "pon"} or not candidate.tile or not candidate.meld_tiles:
        return None
    needed = list(candidate.meld_tiles)
    if not _remove_normal(needed, candidate.tile):
        return None
    remaining = list(state.hand)
    for tile in needed:
        if not _remove_normal(remaining, tile):
            return None
    if not remaining:
        return None
    return min(
        shanten(tuple(remaining[:i] + remaining[i + 1:]), state.open_melds + 1)
        for i in range(len(remaining))
    )


def local_choice(options: tuple[Candidate, ...], state: GameState | None = None) -> Candidate:
    if not options:
        raise NoDecision("无候选动作")
    if state is None:
        return options[0]
    if options[0].action in {"ron", "tsumo"}:
        return options[0]
    abort = next((item for item in options if item.action == "kyuushu"), None)
    if abort is not None and shanten(state.hand, state.open_melds) >= 5:
        return abort
    riichi = [item for item in options if item.action == "riichi"]
    threatened = any(state.riichi[i] for i in range(4) if i != state.seat)
    if riichi and not threatened:
        best = max(riichi, key=lambda item: (item.ukeire or 0, -(item.danger or 0)))
        if (best.ukeire or 0) >= 4:
            return best
    if options[0].action == "pass":
        current_shanten = shanten(state.hand, state.open_melds)
        good_calls: list[tuple[int, Candidate]] = []
        for item in options:
            after = _call_shanten_after_discard(state, item)
            if after is not None and after < current_shanten and _call_yaku_available(state, item):
                good_calls.append((after, item))
        if good_calls:
            return min(good_calls, key=lambda pair: pair[0])[1]
    return options[0]
