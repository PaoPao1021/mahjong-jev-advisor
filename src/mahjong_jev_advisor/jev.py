"""Typed Jev evaluation through Vercel AI Gateway."""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from dataclasses import dataclass

from .rules import local_choice
from .state import Advice, Candidate, GameState

ENDPOINT = "https://ai-gateway.vercel.sh/v4/ai/evaluation-model"
MODEL = "typesafe-ai/jev-latest"


@dataclass(frozen=True)
class ModelConnection:
    endpoint: str = ENDPOINT
    model: str = MODEL
    protocol: str = "vercel"
    timeout: float = 15.0
    local_fallback: bool = False

    def validate(self) -> None:
        url = urlsplit(self.endpoint)
        if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password or url.fragment:
            raise JevError("请输入完整的 HTTP(S) 请求地址，地址中不能含账号密码或 # 片段")
        if self.protocol not in ("vercel", "typesafe", "openrouter", "openai"):
            raise JevError("不支持的接口协议")
        if not self.model.strip():
            raise JevError("请填写模型名称")
        if not 0.5 <= self.timeout <= 120:
            raise JevError("超时需在 0.5–120 秒之间")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A custom endpoint must not forward the user's credential to another host.
        return None


class JevError(Exception):
    pass


class JevAuthError(JevError):
    pass


class JevUnavailableError(JevError):
    """A transient gateway or network failure that permits a labeled rule fallback."""


def _request_body(state: GameState, options: tuple[Candidate, ...]) -> dict:
    return {
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


def _post(payload: dict, api_key: str, connection: ModelConnection) -> tuple[dict, int]:
    connection.validate()
    if not api_key.strip():
        raise JevError("请填写 API Key")
    headers = {"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json",
               "Accept": "application/json", "User-Agent": "mahjong-jev-advisor/0.2"}
    if connection.protocol == "vercel":
        headers.update({"ai-gateway-auth-method": "api-key", "ai-gateway-protocol-version": "0.0.1",
                        "ai-evaluation-model-specification-version": "4", "ai-model-id": connection.model})
    elif connection.protocol in ("typesafe", "openrouter"):
        payload = {"model": connection.model, **payload}
    else:
        payload = {
            "model": connection.model, "stream": False,
            "messages": [
                {"role": "system", "content": 'Choose exactly one supplied action ID. Return JSON only: {"choice":"a0"}. Never invent IDs or probabilities.'},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
    request = urllib.request.Request(connection.endpoint, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers=headers, method="POST")
    started = time.perf_counter()
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=connection.timeout) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        raw = error.read(8192).decode("utf-8", errors="replace").replace(api_key.strip(), "[已隐藏]")
        try:
            detail = json.loads(raw).get("error", {})
            message = detail.get("message", "") if isinstance(detail, dict) else str(detail)
            kind = detail.get("type", "") if isinstance(detail, dict) else ""
        except (ValueError, AttributeError):
            message, kind = "服务端返回非 JSON 错误，请检查请求地址", ""
        if kind == "customer_verification_required":
            message = "Vercel 账户尚未完成账单验证：请在 Vercel AI Gateway 绑定有效信用卡后重试。"
        elif error.code == 402:
            message = "账户余额或额度不足，请检查服务商账单。 " + message
        elif error.code in (301, 302, 303, 307, 308):
            message = "请求地址发生重定向，请填写最终的完整接口地址。"
        detail_text = f"HTTP {error.code} · {message or error.reason}"[:900]
        if error.code in (401, 403):
            raise JevAuthError(detail_text) from error
        if error.code == 429 or error.code >= 500:
            raise JevUnavailableError(detail_text) from error
        raise JevError(detail_text) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        detail = str(error).replace(api_key.strip(), "[已隐藏]")
        raise JevUnavailableError(f"网络请求失败（超时上限 {connection.timeout:g}s）：{detail}") from error
    except ValueError as error:
        raise JevError("服务返回的不是有效 JSON，请检查接口协议和完整请求地址") from error
    if not isinstance(data, dict):
        raise JevError("模型响应必须是 JSON 对象")
    return data, int((time.perf_counter() - started) * 1000)


def _parse(data: dict, options: tuple[Candidate, ...], connection: ModelConnection, elapsed: int) -> Advice:
    try:
        metadata = data.get("providerMetadata") or {}
        typesafe = metadata.get("typesafe") or {}
        if connection.protocol == "openai":
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("没有文本答案")
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            answer = json.loads(content)
            # Generic language models do not supply calibrated choice probabilities.
            probabilities, confidence = {}, None
        else:
            answer = data["answers"]["action"]
            if answer.get("type") != "choice":
                raise ValueError("返回的不是 choice 答案")
            probabilities = answer.get("probabilities") or {}
            confidence = (typesafe.get("confidence") or {}).get("action") if connection.protocol == "vercel" else answer.get("confidence")
        mapping = {f"a{i}": option for i, option in enumerate(options)}
        key = answer.get("choice")
        if key not in mapping:
            raise ValueError("返回了候选列表以外的动作")
        if not isinstance(probabilities, dict) or any(
            k not in mapping or isinstance(v, bool) or not isinstance(v, (int, float))
            or not math.isfinite(v) or not 0 <= v <= 1 for k, v in probabilities.items()
        ):
            raise ValueError("候选概率无效")
        if confidence is not None:
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError("置信度无效")
        if probabilities:
            if set(probabilities) != set(mapping) or abs(sum(probabilities.values()) - 1) > 0.02:
                raise ValueError("候选概率分布不完整或总和不为 1")
        actual_model = typesafe.get("model") or data.get("model") or connection.model
        return Advice(selected=mapping[key], alternatives=tuple(x for x in options if x != mapping[key])[:3],
                      source="jev", model=str(actual_model), confidence=confidence,
                      probabilities=probabilities, latency_ms=elapsed)
    except (KeyError, IndexError, AttributeError, TypeError, ValueError) as error:
        raise JevError(f"HTTP 200，但模型答案无法使用：{error}") from error


def ask_jev(state: GameState, options: tuple[Candidate, ...], api_key: str, timeout: float | None = None,
            *, connection: ModelConnection | None = None) -> Advice:
    connection = connection or ModelConnection(timeout=timeout if timeout is not None else 15.0)
    if not options or len(options) > 255:
        raise JevError("模型候选动作数量无效")
    data, elapsed = _post(_request_body(state, options), api_key, connection)
    return _parse(data, options, connection, elapsed)


def test_connection(api_key: str, connection: ModelConnection) -> Advice:
    """Send an actual inference request through the exact same transport/parser as live advice."""
    options = (Candidate("probe", None, "连接成功", "a0"), Candidate("probe", None, "其他", "a1"))
    payload = {"state": {"message": "ping"}, "questions": {"action": {
        "type": "choice", "instructions": "For the message ping, select a0.",
        "criteria": {"a0": "The message is ping", "a1": "The message is not ping"}}}}
    data, elapsed = _post(payload, api_key, connection)
    advice = _parse(data, options, connection, elapsed)
    if advice.selected != options[0]:
        raise JevError("HTTP 200，但模型未通过 ping 校验（应选择 a0）")
    return advice


def advise(state: GameState, options: tuple[Candidate, ...], api_key: str,
           *, connection: ModelConnection | None = None) -> Advice:
    connection = connection or ModelConnection()
    if not api_key:
        raise JevError("请先填写 API Key，再启用在线决策")
    if len(options) == 1:
        return Advice(options[0], source="rules", note="唯一合法动作 · 确定性规则")
    try:
        return ask_jev(state, options, api_key, connection=connection)
    except JevUnavailableError as error:
        if not connection.local_fallback:
            raise
        selected = local_choice(options, state)
        return Advice(selected, tuple(x for x in options if x != selected)[:3],
                      source="rules", note=f"Jev 不可用：{error}")
