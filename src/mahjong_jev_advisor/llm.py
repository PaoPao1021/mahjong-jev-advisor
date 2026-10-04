"""OpenAI-compatible LLM tactical reasoning & deep analysis engine.

Supports DeepSeek, OpenAI, Claude (via OpenAI proxy), Qwen, Moonshot, etc.
Provides rich, professional mahjong master commentary on the current board state.
"""

from __future__ import annotations

import json
import http.client
import time
import urllib.error
import urllib.request
from typing import Any

from .rules import shanten_text
from .settings import Settings
from .state import Candidate, GameState


class LLMError(Exception):
    pass


class LLMUnavailableError(LLMError):
    pass


class LLMAuthError(LLMError):
    pass


def _format_state_prompt(state: GameState, chosen: Candidate) -> str:
    winds = {"E": "东", "S": "南", "W": "西", "N": "北", "?": "东"}
    round_desc = f"{winds.get(state.round_wind, '东')}{state.round_number or 1}局 {state.honba}本场"
    seat_desc = f"自风 {winds.get(state.seat_wind, '东')}"
    score_desc = f"当前点数: {state.scores[state.seat] if state.seat < len(state.scores) else '未知'}"
    dora_desc = f"宝牌指示牌: {' '.join(state.dora_indicators) if state.dora_indicators else '无'}"

    # Threats
    threat_seats = [i for i, r in enumerate(state.riichi) if r and i != state.seat]
    threat_desc = (
        f"对手立直: {', '.join(f'玩家{p}' for p in threat_seats)}"
        if threat_seats else "暂无对手立直"
    )

    # Hand & melds
    hand_str = " ".join(state.hand)
    own_melds = state.melds[state.seat] if state.seat < len(state.melds) else ()
    meld_str = f"副露: {' '.join(own_melds)}" if own_melds else "门前清"

    # Action info
    action_str = f"{chosen.label} ({chosen.action} {chosen.tile or ''})"
    s_desc = shanten_text(chosen.shanten) if chosen.shanten is not None else "未知"
    wait_str = " ".join(chosen.improving_tiles[:6]) if chosen.improving_tiles else "未知"

    return f"""【当前局况】
玩法: {state.player_count}人立直麻将；已拔北: {state.nuki}
{("三麻：无二至八万、不能吃、一万指示九万；拔北不破门清，宝牌不能单独成役。" if state.player_count == 3 else "四人标准规则。 ")}
场况: {round_desc} · {seat_desc} · {score_desc}
{dora_desc}
{threat_desc}
己方手牌: {hand_str} ({meld_str})

【AI 推荐动作】
推荐决策: {action_str}
向听状态: {s_desc}
有效牌/待牌: {wait_str} (约 {chosen.ukeire or 0} 张)
放铳危险级: {chosen.danger or 0} · 弃宝牌数: {chosen.dora_loss}
规则解析: {chosen.rationale}

请作为日本麻将十段/天凤位顶级高手，以精练专业的行文点评：
1. 为什么推荐该切牌/动作？
2. 牌效率、手役走向与鸣牌规划。
3. 针对场上立直/副露的防守与防铳要点。
字数控制在250字以内，重点突出，切中要害。"""


def _query_llm_analysis_once(
    state: GameState,
    chosen: Candidate,
    settings: Settings,
    timeout: float = 20.0,
) -> str:
    if not settings.llm_api_key:
        raise LLMAuthError("尚未配置大模型 API Key，请在【大模型设置】中填入。")

    base_url = settings.llm_base_url.strip() or "https://api.deepseek.com/v1"
    url = f"{base_url.rstrip('/')}/chat/completions"
    model = settings.llm_model.strip() or "deepseek-chat"

    prompt = _format_state_prompt(state, chosen)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一位拥有天凤位/十段水平的日本立直麻将大师级战术专家。"
                    "请根据玩家牌局信息，给出极具深度、干练精准的战术复盘与牌理分析。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 500,
    }

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {settings.llm_api_key}",
            "Content-Type": "application/json",
            "User-Agent": "MahjongJevAdvisor/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise LLMAuthError(f"大模型认证失败 (HTTP {error.code})：API Key 无效或过期。") from error
        if error.code == 429 or error.code >= 500:
            raise LLMUnavailableError(f"大模型服务异常 HTTP {error.code}") from error
        raise LLMError(f"大模型服务异常 HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        raise LLMUnavailableError(f"网络连接大模型超时或失败：{error}") from error
    except ValueError as error:
        raise LLMError(f"大模型响应数据解析失败：{error}") from error

    try:
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise LLMError("大模型响应缺少文本内容")
        content = content.strip()
        if not content:
            raise LLMError("大模型返回了空内容")
        return content
    except (KeyError, IndexError, TypeError) as error:
        raise LLMError(f"大模型响应格式不匹配: {error}") from error


def query_llm_analysis(state: GameState, chosen: Candidate, settings: Settings, timeout: float = 20.0) -> str:
    for attempt in range(3):
        try:
            return _query_llm_analysis_once(state, chosen, settings, timeout)
        except LLMUnavailableError:
            if attempt == 2:
                raise
            time.sleep(0.5 * (2 ** attempt))
