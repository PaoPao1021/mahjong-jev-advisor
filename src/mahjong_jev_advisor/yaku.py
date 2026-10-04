"""Comprehensive Japanese Riichi Mahjong Yaku evaluation and potential analyzer.

Pre-computes deterministic yaku analysis, tenpai expected value (Han, Fu, Score),
and call safety (preventing no-yaku dead hands) to accelerate AI decision speed
and provide master-level tactical annotations.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from mahjong.constants import EAST, NORTH, SOUTH, WEST
from mahjong.hand_calculating.hand import HandCalculator
from mahjong.hand_calculating.hand_config import HandConfig, OptionalRules
from mahjong.meld import Meld

from .state import Candidate, GameState
from .tiles import normal

# Complete Japanese-Chinese Yaku and Yakuman dictionary (60 types)
YAKU_TRANSLATIONS: dict[str, str] = {
    # 1 Han
    "Menzen Tsumo": "门清自摸",
    "Riichi": "立直",
    "Ippatsu": "一发",
    "Chankan": "枪杠",
    "Rinshan Kaihou": "岭上开花",
    "Haitei Raoyue": "海底摸月",
    "Houtei Raoyui": "河底捞鱼",
    "Pinfu": "平和",
    "Tanyao": "断幺九",
    "Iipeiko": "一盃口",
    "Yakuhai (chun)": "役牌:中",
    "Yakuhai (haku)": "役牌:白",
    "Yakuhai (hatsu)": "役牌:发",
    "Yakuhai (east)": "役牌:东",
    "Yakuhai (south)": "役牌:南",
    "Yakuhai (west)": "役牌:西",
    "Yakuhai (north)": "役牌:北",
    "Yakuhai (wind of place)": "自风牌",
    "Yakuhai (wind of round)": "场风牌",
    "Dora": "宝牌",
    "Aka Dora": "赤宝牌",
    # 2 Han
    "Double Riichi": "双立直",
    "Chiitoitsu": "七对子",
    "Chantai": "混全带幺九",
    "Ittsu": "一气通贯",
    "Sanshoku Doujun": "三色同顺",
    "Sanshoku Doukou": "三色同刻",
    "San Kantsu": "三杠子",
    "Toitoi": "对对和",
    "San Ankou": "三暗刻",
    "Shou Sangen": "小三元",
    "Honroutou": "混老头",
    # 3 Han
    "Honitsu": "混一色",
    "Junchan": "纯全带幺九",
    "Ryanpeikou": "二盃口",
    # 6 Han
    "Chinitsu": "清一色",
    # Yakuman
    "Kokushi Musou": "国士无双",
    "Kokushi Musou Juusanmen Matchi": "国士无双十三面",
    "Suu Ankou": "四暗刻",
    "Suu Ankou Tanki": "四暗刻单骑",
    "Daisangen": "大三元",
    "Shousuushii": "小四喜",
    "Dai Suushii": "大四喜",
    "Tsuu Iisou": "字一色",
    "Ryuuiisou": "绿一色",
    "Chinroutou": "清老头",
    "Chuuren Poutou": "九莲宝灯",
    "Daburu Chuuren Poutou": "纯正九莲宝灯",
    "Suu Kantsu": "四杠子",
    "Tenhou": "天和",
    "Chiihou": "地和",
    "Renhou (yakuman)": "人和(役满)",
    "Daisharin": "大车轮",
}

_MAHJONG_OPTIONS = OptionalRules(has_open_tanyao=True, has_aka_dora=True)
_CALCULATOR = HandCalculator()

_WIND_MAP = {"E": EAST, "S": SOUTH, "W": WEST, "N": NORTH, "?": EAST}


def translate_yaku(name: str) -> str:
    return YAKU_TRANSLATIONS.get(name, name)


def tiles_to_136(tiles: tuple[str, ...] | list[str]) -> list[int]:
    """Converts a sequence of tile strings into mahjong 136-array format.

    Accurately handles red fives (0m->16, 0p->52, 0s->88) and regular 5s (17..19, 53..55, 89..91).
    """
    red_indices = {"m": 16, "p": 52, "s": 88}
    normal_5_offsets = {"m": [17, 18, 19, 16], "p": [53, 54, 55, 52], "s": [89, 90, 91, 88]}
    base_offsets = {"m": 0, "p": 36, "s": 72, "z": 108}

    used_tiles: set[int] = set()
    result: list[int] = []

    for t in tiles:
        if not t:
            continue
        suit = t[-1]
        val = t[0]
        if val == "0" and suit in red_indices:
            red_idx = red_indices[suit]
            used_tiles.add(red_idx)
            result.append(red_idx)
        elif val == "5" and suit in ("m", "p", "s"):
            chosen = None
            for idx in normal_5_offsets[suit]:
                if idx not in used_tiles:
                    chosen = idx
                    break
            if chosen is None:
                chosen = normal_5_offsets[suit][0]
            used_tiles.add(chosen)
            result.append(chosen)
        else:
            n = int(val)
            offset = base_offsets[suit]
            base_idx = offset + (n - 1) * 4
            chosen = None
            for i in range(4):
                if (base_idx + i) not in used_tiles:
                    chosen = base_idx + i
                    break
            if chosen is None:
                chosen = base_idx
            used_tiles.add(chosen)
            result.append(chosen)
    return result


def parse_melds_to_objects(meld_groups: tuple[tuple[str, ...], ...] | list[list[str]]) -> list[Meld]:
    """Converts meld tile lists to mahjong.meld.Meld instances."""
    result: list[Meld] = []
    for group in meld_groups:
        if len(group) == 3:
            t0, t1, t2 = normal(group[0]), normal(group[1]), normal(group[2])
            tiles_136 = tiles_to_136(group)
            if t0 == t1 == t2:
                result.append(Meld(meld_type=Meld.PON, tiles=tiles_136))
            else:
                result.append(Meld(meld_type=Meld.CHI, tiles=tiles_136))
        elif len(group) == 4:
            result.append(Meld(meld_type=Meld.KAN, tiles=tiles_to_136(group)))
    return result


def analyze_yaku_potential(state: GameState, hand: tuple[str, ...]) -> list[str]:
    """Evaluates all potential yaku directions for the current hand at 1-2 shanten."""
    if not hand:
        return []

    hints: list[str] = []
    norm_hand = [normal(t) for t in hand]
    counts = Counter(norm_hand)

    # 1. 门前清
    if state.open_melds == 0:
        hints.append("门清")

    # 2. 役牌潜能 (白、发、中、场风、自风)
    yakuhai_map = {"E": "1z", "S": "2z", "W": "3z", "N": "4z"}
    round_wind_tile = yakuhai_map.get(state.round_wind)
    seat_wind_tile = yakuhai_map.get(state.seat_wind)
    for zh, tile in (("中", "7z"), ("发", "6z"), ("白", "5z")):
        if counts[tile] >= 3:
            hints.append(f"役牌:{zh}刻子")
        elif counts[tile] == 2:
            hints.append(f"役牌:{zh}对子")
    if round_wind_tile:
        if counts[round_wind_tile] >= 3 and f"场风{state.round_wind}" not in hints:
            hints.append("场风役牌")
        elif counts[round_wind_tile] == 2:
            hints.append("场风对子")
    if seat_wind_tile:
        if counts[seat_wind_tile] >= 3 and "自风役牌" not in hints:
            hints.append("自风役牌")
        elif counts[seat_wind_tile] == 2:
            hints.append("自风对子")

    # 3. 断幺九潜能
    non_tanyao = [t for t in norm_hand if t[1] == "z" or int(t[0]) in (1, 9)]
    own_melds = state.melds[state.seat] if state.seat < len(state.melds) else ()
    meld_non_tanyao = [t for t in own_melds if t[1] == "z" or int(normal(t)[0]) in (1, 9)]
    if not meld_non_tanyao:
        if len(non_tanyao) == 0:
            hints.append("确定断幺九")
        elif len(non_tanyao) <= 2:
            hints.append("断幺九走向")

    # 4. 混一色 / 清一色潜能
    suit_counts = {s: sum(1 for t in norm_hand if t[1] == s) for s in "mps"}
    max_suit = max(suit_counts, key=suit_counts.get)
    max_count = suit_counts[max_suit]
    honor_count = sum(1 for t in norm_hand if t[1] == "z")
    if max_count + honor_count >= 10:
        if honor_count == 0 and max_count >= 10:
            hints.append("清一色走向")
        else:
            hints.append("混一色走向")

    # 5. 平和潜能 (门清且字牌少，以顺子为主)
    if state.open_melds == 0 and honor_count <= 2:
        pairs = sum(1 for cnt in counts.values() if cnt >= 2)
        if pairs <= 2:
            hints.append("平和倾向")

    # 6. 七对子潜能
    pair_count = sum(1 for cnt in counts.values() if cnt >= 2)
    if state.open_melds == 0 and pair_count >= 4:
        hints.append(f"七对子({pair_count}对)")

    # 7. 对对和潜能
    triplet_count = sum(1 for cnt in counts.values() if cnt >= 3)
    if triplet_count >= 2 or (triplet_count >= 1 and pair_count >= 3):
        hints.append("对对和走向")

    # 8. 一气通贯潜能 (123 + 456 + 789 同门胚子)
    for s in "mps":
        has_123 = any(f"{n}{s}" in counts for n in (1, 2, 3))
        has_456 = any(f"{n}{s}" in counts for n in (4, 5, 6))
        has_789 = any(f"{n}{s}" in counts for n in (7, 8, 9))
        if sum([has_123, has_456, has_789]) >= 3:
            s_name = {"m": "万", "p": "筒", "s": "索"}[s]
            hints.append(f"{s_name}一通潜能")
            break

    # 9. 三色同顺潜能
    common_nums = set()
    for n in range(1, 8):
        seq_m = {f"{n}m", f"{n+1}m", f"{n+2}m"}
        seq_p = {f"{n}p", f"{n+1}p", f"{n+2}p"}
        seq_s = {f"{n}s", f"{n+1}s", f"{n+2}s"}
        match_count = sum([bool(seq_m & set(norm_hand)), bool(seq_p & set(norm_hand)), bool(seq_s & set(norm_hand))])
        if match_count == 3:
            common_nums.add(n)
    if common_nums and state.player_count == 4:
        hints.append("三色同顺潜能")

    # 10. 国士无双潜能
    terminals_and_honors = set(norm_hand) & {
        "1m", "9m", "1p", "9p", "1s", "9s", "1z", "2z", "3z", "4z", "5z", "6z", "7z"
    }
    if state.open_melds == 0 and len(terminals_and_honors) >= 9:
        hints.append(f"国士无双({len(terminals_and_honors)}种)")

    return hints


def estimate_tenpai_value(
    state: GameState,
    hand_after_discard: tuple[str, ...],
    improving_tiles: tuple[str, ...],
    is_riichi: bool = False,
) -> tuple[int, int, int, list[str]]:
    """Calculates min_han, max_han, expected_score, and top yaku when in tenpai (0-shanten).

    Accurately handles:
    - Guaranteed Yaku hands (e.g. Riichi, Pinfu, Tanyao, Honitsu, Yakuhai)
    - Kataten hands (片和, where some wait tiles have yaku while others have no yaku)
    - Dead hands / Keiten (形式听牌, 0-yaku hands where Ron/Tsumo is impossible)
    """
    if not improving_tiles:
        return 0, 0, 0, []

    own_melds_raw = state.melds[state.seat] if state.seat < len(state.melds) else ()
    # Group meld tiles into chunks of 3 or 4
    meld_groups: list[list[str]] = []
    curr: list[str] = []
    for t in own_melds_raw:
        curr.append(t)
        if len(curr) >= 3:
            meld_groups.append(curr)
            curr = []
    meld_objs = parse_melds_to_objects(meld_groups)

    # The upstream calculator uses four-player indicator order. Translate 1m
    # to 8m internally so it awards the sanma 9m bonus correctly.
    dora_136 = [tiles_to_136(["8m" if state.player_count == 3 and ind == "1m" else ind])[0]
                for ind in state.dora_indicators if ind]
    player_wind = _WIND_MAP.get(state.seat_wind, EAST)
    round_wind = _WIND_MAP.get(state.round_wind, EAST)

    is_menzen = state.open_melds == 0
    riichi_active = is_menzen and (is_riichi or any(state.riichi[state.seat:state.seat+1]))
    config_tsumo = HandConfig(
        is_tsumo=True,
        is_riichi=riichi_active,
        player_wind=player_wind,
        round_wind=round_wind,
        options=_MAHJONG_OPTIONS,
    )

    all_hand_tiles = list(hand_after_discard) + list(own_melds_raw)

    han_values: list[int] = []
    scores: list[int] = []
    yaku_frequency: Counter[str] = Counter()
    has_no_yaku_wait = False

    for wait_tile in improving_tiles:
        full_hand = all_hand_tiles + [wait_tile]
        if len(full_hand) != 14:
            continue
        try:
            full_136 = tiles_to_136(full_hand)
            win_136 = tiles_to_136([wait_tile])[0]
            res = _CALCULATOR.estimate_hand_value(
                full_136, win_136, melds=meld_objs,
                dora_indicators=dora_136, config=config_tsumo
            )
            if res.error is None and res.cost:
                yakuman = any(y.is_yakuman for y in (res.yaku or []))
                north_bonus = 0
                if state.player_count == 3 and state.nuki and not yakuman:
                    north_bonus = state.nuki[state.seat] * (1 + state.dora_indicators.count("3z"))
                han_values.append(res.han + north_bonus)
                # The scoring library assumes four payers. Do not present its
                # total as a sanma payout (room tsumo-loss rules can vary).
                scores.append(res.cost.get("total", 0) if state.player_count == 4 else 0)
                if north_bonus:
                    yaku_frequency["拔北宝牌"] += 1
                for y in (res.yaku or []):
                    yaku_frequency[translate_yaku(y.name)] += 1
            elif res.error == "no_yaku":
                has_no_yaku_wait = True
                han_values.append(0)
                scores.append(0)
                yaku_frequency["无役"] += 1
        except Exception:
            continue

    if not han_values:
        return 0, 0, 0, []

    min_h = min(han_values)
    max_h = max(han_values)

    # If completely no yaku across all waits
    if max_h == 0:
        return 0, 0, 0, ["形式听牌(无役)"]

    valid_scores = [s for s in scores if s > 0]
    avg_score = int(round(sum(valid_scores) / len(valid_scores))) if valid_scores else 0

    top_yaku = [y for y, _ in yaku_frequency.most_common(4)]
    if has_no_yaku_wait and "无役(片和)" not in top_yaku:
        top_yaku.append("无役(片和)")

    return min_h, max_h, avg_score, top_yaku


def evaluate_call_safety(state: GameState, candidate: Candidate) -> tuple[bool, str, list[str]]:
    """Evaluates if calling Chi/Pon/Kan guarantees yaku or risks producing a 0-yaku dead hand."""
    values = {"E": "1z", "S": "2z", "W": "3z", "N": "4z"}
    yakuhai = {"5z", "6z", "7z", values.get(state.round_wind), values.get(state.seat_wind)}

    called = normal(candidate.tile) if candidate.tile else None
    own_melds = state.melds[state.seat] if state.seat < len(state.melds) else ()

    # 1. 役牌碰牌确定有役
    if candidate.action == "pon" and called in yakuhai:
        zh_map = {"5z": "白", "6z": "发", "7z": "中", "1z": "东", "2z": "南", "3z": "西", "4z": "北"}
        name = zh_map.get(called, "役牌")
        return True, f"役牌:{name}刻子 · 确定有役", [f"役牌:{name}"]

    # 2. 已有役牌副露支持
    meld_counts = Counter(normal(tile) for tile in own_melds)
    for yk in yakuhai:
        if yk and meld_counts[yk] >= 3:
            return True, "已有役牌副露支持 · 安全鸣牌", ["役牌"]

    # 3. 役牌对子/刻子在手
    hand_counts = Counter(normal(tile) for tile in state.hand)
    held_yakuhai = [y for y in yakuhai if y and hand_counts[y] >= 2]
    if held_yakuhai:
        return True, f"手牌保留役牌对子，后续可成役", ["役牌保留"]

    # 4. 断幺九判定
    all_melds_similes = all(tile[1] != "z" and int(normal(tile)[0]) not in (1, 9) for tile in own_melds)
    candidate_similes = all(tile[1] != "z" and int(normal(tile)[0]) not in (1, 9) for tile in candidate.meld_tiles)
    hand_non_tanyao = [t for t in state.hand if t[1] == "z" or int(normal(t)[0]) in (1, 9)]

    if all_melds_similes and candidate_similes:
        if len(hand_non_tanyao) <= 1:
            return True, "断幺九确定或极易成型", ["断幺九"]
        elif len(hand_non_tanyao) <= 3:
            return True, "鸣牌走断幺九，需注意处理幺九浮牌", ["断幺九走向"]

    # 5. 染手判定 (混一色/清一色)
    suits = [normal(t)[1] for t in state.hand if normal(t)[1] != "z"]
    if suits:
        main_suit = Counter(suits).most_common(1)[0][0]
        dominant_count = sum(1 for t in state.hand if normal(t)[1] in (main_suit, "z"))
        if dominant_count >= len(state.hand) - 2:
            s_name = {"m": "万", "p": "筒", "s": "索"}[main_suit]
            return True, f"鸣牌走{s_name}混一色/清一色", ["混一色"]

    # 6. 无役风险警示
    return False, "注意：鸣牌后暂无确切役种支持，谨防无役死手", []
