"""Loopback-only Mahjong Soul WebSocket event decoder.

The browser extension forwards binary frames and the liqi.json schema. Raw frames
are decoded in memory, never logged or persisted, and only game-state events are
exposed to the desktop UI.
"""
from __future__ import annotations

import base64
import json
import math
import struct
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from .state import GameState
from .tiles import normal

HOOK_HOST = "127.0.0.1"
HOOK_PORT = 8765
MAX_BODY = 6 * 1024 * 1024
EXPECTED_EXTENSION_VERSION = "0.1.3"
DEFAULT_SCHEMA_PATH = Path(__file__).parent / "liqi.json"


def decode_action_data(data: bytes) -> bytes:
    """Undo Liqi's live-notification XOR; restore actions are already plain.

    Protocol reference: Sunalamye/Naki, LiqiParser.swift (liqiDecode).
    """
    keys = (0x84, 0x5e, 0x4e, 0x42, 0x39, 0xa2, 0x1f, 0x60, 0x1c)
    return bytes(value ^ (((23 ^ len(data)) + 5 * index + keys[index % 9]) & 0xff)
                 for index, value in enumerate(data))


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7f) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise ValueError("invalid protobuf varint")


class LiqiDecoder:
    _VARINTS = {"int32", "int64", "uint32", "uint64", "sint32", "sint64", "bool", "enum"}
    _FIXED32 = {"fixed32", "sfixed32", "float"}
    _FIXED64 = {"fixed64", "sfixed64", "double"}

    def __init__(self, schema: dict[str, Any]):
        self.types: dict[str, dict[str, Any]] = {}
        self.enums: set[str] = set()
        self.methods: dict[str, str] = {}
        nested = schema.get("nested", {})
        self._walk(nested, [])

    def _walk(self, nested: dict[str, Any], parents: list[str]) -> None:
        for name, item in nested.items():
            path = parents + [name]
            full = ".".join(path)
            if "fields" in item:
                self.types[full] = item
            if "values" in item:
                self.enums.add(full)
            for method, spec in item.get("methods", {}).items():
                response = self._resolve(spec.get("responseType", ""), full)
                self.methods[f"{full}.{method}"] = response or spec.get("responseType", "")
            self._walk(item.get("nested", {}), path)

    def _resolve(self, name: str, context: str = "") -> str | None:
        name = name.lstrip(".")
        if name in self.types or name in self.enums:
            return name
        scope = context.split(".")[:-1]
        for end in range(len(scope), 0, -1):
            candidate = ".".join(scope[:end] + [name])
            if candidate in self.types or candidate in self.enums:
                return candidate
        matches = [key for key in (*self.types, *self.enums) if key == name or key.endswith("." + name)]
        return matches[0] if len(matches) == 1 else None

    def decode(self, type_name: str, data: bytes) -> dict[str, Any]:
        resolved = self._resolve(type_name) or type_name.lstrip(".")
        spec = self.types.get(resolved)
        if spec is None:
            raise ValueError(f"unknown protobuf type: {type_name}")
        fields = {int(value["id"]): (name, value) for name, value in spec.get("fields", {}).items()}
        result: dict[str, Any] = {}
        offset = 0
        while offset < len(data):
            key, offset = _varint(data, offset)
            number, wire = key >> 3, key & 7
            field = fields.get(number)
            if wire == 0:
                value, offset = _varint(data, offset)
                raw: Any = value
            elif wire == 1:
                if offset + 8 > len(data):
                    raise ValueError("truncated fixed64")
                raw = data[offset:offset + 8]
                offset += 8
            elif wire == 2:
                length, offset = _varint(data, offset)
                if length < 0 or offset + length > len(data):
                    raise ValueError("truncated protobuf field")
                raw = data[offset:offset + length]
                offset += length
            elif wire == 5:
                if offset + 4 > len(data):
                    raise ValueError("truncated fixed32")
                raw = data[offset:offset + 4]
                offset += 4
            else:
                raise ValueError("unsupported protobuf wire type")
            if field is None:
                continue
            field_name, field_spec = field
            field_type = field_spec.get("type", "bytes")
            repeated = field_spec.get("rule") == "repeated"
            scalar_type = self._resolve(field_type, resolved)
            expected_wire = (0 if field_type in self._VARINTS or scalar_type in self.enums
                             else 5 if field_type in self._FIXED32
                             else 1 if field_type in self._FIXED64 else 2)
            if wire != expected_wire and not (repeated and wire == 2 and expected_wire == 0):
                raise ValueError(f"protobuf wire mismatch: {resolved}.{field_name}")
            value = self._convert(raw, wire, field_type, resolved, repeated)
            if repeated:
                bucket = result.setdefault(field_name, [])
                bucket.extend(value if isinstance(value, list) else [value])
            else:
                result[field_name] = value
        # Proto3 omits default scalars, including seat 0 and East 1 metadata.
        for field_name, field_spec in spec.get("fields", {}).items():
            if field_name in result:
                continue
            field_type = field_spec.get("type", "bytes")
            if field_spec.get("rule") == "repeated":
                result[field_name] = []
            elif field_type == "bool":
                result[field_name] = False
            elif field_type in self._VARINTS or self._resolve(field_type, resolved) in self.enums:
                result[field_name] = 0
            elif field_type in self._FIXED32 | self._FIXED64:
                result[field_name] = 0
            elif field_type == "string":
                result[field_name] = ""
            elif field_type == "bytes":
                result[field_name] = b""
        return result

    def _convert(self, raw: Any, wire: int, field_type: str, context: str, repeated: bool) -> Any:
        resolved = self._resolve(field_type, context)
        scalar = field_type if resolved is None else ("enum" if resolved in self.enums else "message")
        if wire == 2 and repeated and scalar in self._VARINTS:
            values, position = [], 0
            while position < len(raw):
                value, position = _varint(raw, position)
                values.append(self._number(value, field_type))
            return values
        if scalar == "message":
            return self.decode(resolved or field_type, raw)
        if field_type == "string":
            return raw.decode("utf-8", errors="replace")
        if field_type == "bytes":
            return bytes(raw)
        if wire == 0:
            return self._number(int(raw), field_type)
        if field_type == "float":
            return struct.unpack("<f", raw)[0]
        if field_type == "double":
            return struct.unpack("<d", raw)[0]
        if field_type in ("fixed32", "sfixed32"):
            return int.from_bytes(raw, "little", signed=field_type.startswith("s"))
        if field_type in ("fixed64", "sfixed64"):
            return int.from_bytes(raw, "little", signed=field_type.startswith("s"))
        return raw

    @staticmethod
    def _number(value: int, field_type: str) -> int | bool:
        if field_type == "bool":
            return bool(value)
        if field_type.startswith("sint"):
            return (value >> 1) ^ -(value & 1)
        bits = 32 if field_type.endswith("32") else 64
        if field_type.startswith("int") and value >= 1 << (bits - 1):
            value -= 1 << bits
        return value


class HookStateBuilder:
    OPERATION_BUTTONS = {2: "chi", 3: "pon", 4: "kan", 5: "kan", 6: "kan",
                         7: "riichi", 8: "tsumo", 9: "ron", 10: "kyuushu"}

    def __init__(self):
        self.reset(keep_seat=False)

    def reset(self, keep_seat: bool = True) -> None:
        if not keep_seat:
            self.own_seat = None
        self.hand: list[str] = []
        self.rivers: list[list[str]] = [[], [], [], []]
        self.melds: list[list[str]] = [[], [], [], []]
        self.meld_groups = [0, 0, 0, 0]
        self.doras: list[str] = []
        self.scores: list[int | None] = [None] * 4
        self.riichi: list[bool | None] = [False] * 4
        self.chang = 0
        self.ju = 0
        self.honba = 0
        self.sticks = 0
        self.last_discard: str | None = None
        self.buttons: set[str] = set()

    def _operations(self, value: Any) -> None:
        self.buttons.clear()
        if not isinstance(value, dict):
            return
        seat = value.get("seat")
        if self.own_seat is None and isinstance(seat, int) and 0 <= seat < 4:
            self.own_seat = seat
        op_list = value.get("operationList") or value.get("operation_list") or []
        types = [item.get("type") for item in op_list if isinstance(item, dict)]
        self.buttons.update(self.OPERATION_BUTTONS[t] for t in types if t in self.OPERATION_BUTTONS)
        if any(t in {2, 3, 5, 9} for t in types):
            self.buttons.add("pass")

    def apply(self, name: str, action: dict[str, Any]) -> GameState | None:
        short = name.rsplit(".", 1)[-1]
        if short == "ActionNewRound":
            self.reset(keep_seat=True)
            self.chang, self.ju = int(action.get("chang", 0)), int(action.get("ju", 0))
            self.honba, self.sticks = int(action.get("ben", 0)), int(action.get("liqibang", 0))
            self.hand = list(action.get("tiles", []))
            self.doras = list(action.get("doras") or ([action["dora"]] if action.get("dora") else []))
            scores = list(action.get("scores", []))
            if len(scores) == 4:
                self.scores = scores
            self._operations(action.get("operation"))
        elif short == "ActionDealTile":
            tile, seat = action.get("tile"), action.get("seat")
            if tile and isinstance(seat, int):
                self.own_seat = seat
                self.hand.append(tile)
            self.doras = list(action.get("doras") or self.doras)
            self._operations(action.get("operation"))
        elif short == "ActionDiscardTile":
            seat, tile = action.get("seat"), action.get("tile")
            if isinstance(seat, int) and 0 <= seat < 4 and isinstance(tile, str) and tile:
                self.rivers[seat].append(tile)
                self.last_discard = tile
                if seat == self.own_seat:
                    self._remove(tile)
                if action.get("isLiqi") or action.get("is_liqi") or action.get("isWliqi") or action.get("is_wliqi"):
                    self.riichi[seat] = True
            self.doras = list(action.get("doras") or self.doras)
            self._operations(action.get("operation"))
        elif short == "ActionChiPengGang":
            seat, tiles = action.get("seat"), list(action.get("tiles", []))
            if isinstance(seat, int) and 0 <= seat < 4 and tiles:
                self.melds[seat].extend(tiles)
                self.meld_groups[seat] += 1
                if seat == self.own_seat:
                    needed = Counter(normal(tile) for tile in tiles)
                    if self.last_discard:
                        needed[normal(self.last_discard)] -= 1
                    for tile, count in needed.items():
                        for _ in range(max(0, count)):
                            self._remove(tile)
                self._remove_called_discard()
            self._operations(action.get("operation"))
        elif short == "ActionAnGangAddGang":
            seat, tile = action.get("seat"), action.get("tiles") or action.get("tile")
            if isinstance(seat, int) and 0 <= seat < 4 and isinstance(tile, str):
                in_hand = sum(normal(value) == normal(tile) for value in self.hand)
                # Mahjong Soul uses type=3 for a concealed kan and type=2 for
                # an added kan. Opponents' concealed tiles are unavailable, so
                # the action type is required to reconstruct their meld.
                concealed = action.get("type") == 3 or (seat == self.own_seat and in_hand >= 4)
                copies = 4 if concealed else 1
                self.melds[seat].extend([tile] * copies)
                if concealed:
                    self.meld_groups[seat] += 1
                if seat == self.own_seat:
                    for _ in range(copies):
                        self._remove(tile)
            self.doras = list(action.get("doras") or self.doras)
            self._operations(action.get("operation"))
        elif short == "ActionBaBei":
            seat = action.get("seat")
            if seat == self.own_seat:
                self._remove("4z")
            self.doras = list(action.get("doras") or self.doras)
            self._operations(action.get("operation"))
        elif short in {"ActionHule", "ActionNoTile", "ActionLiuJu"}:
            self.buttons.clear()
            scores = list(action.get("scores", []))
            if len(scores) == 4 and all(isinstance(x, int) for x in scores):
                self.scores = scores
            elif scores and isinstance(scores[0], dict):
                first = scores[0]
                old = first.get("oldScores") or first.get("old_scores") or []
                delta = first.get("deltaScores") or first.get("delta_scores") or []
                if len(old) == 4 and len(delta) == 4:
                    self.scores = [int(o) + int(d) for o, d in zip(old, delta)]
        return self.state()

    def restore(self, payload: dict[str, Any]) -> GameState | None:
        restore = payload.get("gameRestore") or payload.get("game_restore") or {}
        snapshot = restore.get("snapshot") or {}
        if snapshot:
            self.reset()
            self.chang, self.ju = int(snapshot.get("chang", 0)), int(snapshot.get("ju", 0))
            self.honba, self.sticks = int(snapshot.get("ben", 0)), int(snapshot.get("liqibang", 0))
            self.own_seat = snapshot.get("indexPlayer") if snapshot.get("indexPlayer") is not None else snapshot.get("index_player")
            self.hand = list(snapshot.get("hands", []))
            self.doras = list(snapshot.get("doras", []))
            for seat, player in enumerate(snapshot.get("players", [])[:4]):
                self.scores[seat] = player.get("score")
                self.rivers[seat] = list(player.get("qipais", []))
                for meld in player.get("mings", []):
                    tiles = list(meld.get("tile", []))
                    self.melds[seat].extend(tiles)
                    self.meld_groups[seat] += 1
                self.riichi[seat] = int(player.get("liqiposition", 0)) > 0
        return self.state()

    def _remove(self, target: str) -> None:
        if target in self.hand:
            self.hand.remove(target)
            return
        try:
            wanted = normal(target)
        except ValueError:
            return
        for index, tile in enumerate(self.hand):
            if normal(tile) == wanted:
                self.hand.pop(index)
                return

    def _remove_called_discard(self) -> None:
        """Move the called tile from its river into the caller's meld once."""
        if not self.last_discard:
            return
        wanted = normal(self.last_discard)
        for river in self.rivers:
            for index in range(len(river) - 1, -1, -1):
                if normal(river[index]) == wanted:
                    river.pop(index)
                    return

    def state(self) -> GameState | None:
        if self.own_seat is None or not 0 <= self.own_seat < 4 or not self.hand:
            return None
        winds = "ESWN"
        try:
            return GameState.from_dict({
                "hand": self.hand, "rivers": self.rivers, "melds": self.melds,
                "dora_indicators": self.doras, "buttons": sorted(self.buttons),
                "last_discard": self.last_discard, "seat": self.own_seat,
                "round_wind": "E" if self.chang == 0 else "S" if self.chang == 1 else "?",
                "round_number": self.ju + 1 if 0 <= self.ju < 4 else None,
                "seat_wind": winds[(self.own_seat - self.ju) % 4],
                "scores": self.scores, "riichi": self.riichi, "honba": self.honba,
                "sticks": self.sticks, "open_melds": self.meld_groups[self.own_seat],
                "observation_confidence": 1.0,
            })
        except (ValueError, TypeError):
            return None


class HookProtocol:
    def __init__(self, schema: dict[str, Any] | None = None):
        self.decoder: LiqiDecoder | None = None
        self.requests: dict[tuple[str, int], str] = {}
        self.builder = HookStateBuilder()
        self.auth_account_id: int | None = None
        self.messages_seen = 0
        self.last_message_name = ""
        self.last_action_name = ""
        if schema is not None:
            self.set_schema(schema)
        elif DEFAULT_SCHEMA_PATH.exists():
            try:
                with open(DEFAULT_SCHEMA_PATH, "r", encoding="utf-8") as f:
                    self.set_schema(json.load(f))
            except Exception:
                pass

    def set_schema(self, schema: dict[str, Any]) -> None:
        self.decoder = LiqiDecoder(schema)

    def feed(self, direction: str, frame: bytes, connection: str = "") -> GameState | None:
        decoder = self.decoder
        if decoder is None or len(frame) < 2:
            return None
        category = frame[0]
        if category == 1:
            wrapper = decoder.decode("lq.Wrapper", frame[1:])
            return self._message(wrapper.get("name", ""), wrapper.get("data", b""))
        if len(frame) < 4:
            return None
        request_id = int.from_bytes(frame[1:3], "little")
        if direction == "send" and category == 2:
            wrapper = decoder.decode("lq.Wrapper", frame[3:])
            name = str(wrapper.get("name", "")).lstrip(".")
            self.requests[connection, request_id] = name
            if name.endswith("FastTest.authGame"):
                try:
                    payload = decoder.decode("lq.ReqAuthGame", wrapper.get("data", b""))
                    self.auth_account_id = payload.get("accountId") or payload.get("account_id")
                except Exception:
                    pass
            return None
        if direction == "receive" and category == 3:
            request = self.requests.pop((connection, request_id), "")
            response_type = decoder.methods.get(request)
            if response_type:
                # Responses have the same Wrapper as requests; the method is empty.
                wrapper = decoder.decode("lq.Wrapper", frame[3:])
                payload = decoder.decode(response_type, wrapper.get("data", b""))
                if response_type.endswith("ResAuthGame"):
                    seat_list = payload.get("seatList") or payload.get("seat_list") or []
                    if self.auth_account_id is not None and self.auth_account_id in seat_list:
                        self.builder.own_seat = seat_list.index(self.auth_account_id)
                elif response_type.endswith(("ResSyncGame", "ResEnterGame")):
                    state = self.builder.restore(payload)
                    restore = payload.get("gameRestore") or payload.get("game_restore") or {}
                    for proto in restore.get("actions", []):
                        action_name = str(proto.get("name", "")).lstrip(".")
                        action_data = proto.get("data", b"")
                        if action_name and isinstance(action_data, bytes):
                            action = decoder.decode(action_name, action_data)
                            self.last_action_name = action_name
                            state = self.builder.apply(action_name, action) or state
                    return state
        return None

    def _message(self, name: str, data: bytes) -> GameState | None:
        assert self.decoder is not None
        short = name.lstrip(".")
        self.messages_seen += 1
        self.last_message_name = short
        payload = self.decoder.decode(short, data)
        if short.endswith("ActionPrototype"):
            action_name = str(payload.get("name", "")).lstrip(".")
            action_data = payload.get("data", b"")
            if action_name and isinstance(action_data, bytes):
                self.last_action_name = action_name
                action = self.decoder.decode(action_name, decode_action_data(action_data))
                return self.builder.apply(action_name, action)
        if short.rsplit(".", 1)[-1].startswith("Action"):
            return self.builder.apply(short, payload)
        return None


class HookSignals(QObject):
    state = Signal(object)
    status = Signal(str)


class HookBridge:
    def __init__(self, host: str = HOOK_HOST, port: int = HOOK_PORT):
        self.host, self.port = host, port
        self.signals = HookSignals()
        self.protocol = HookProtocol()
        self.last_seen = 0.0
        self._announced = False
        self._schema_announced = False
        self.ping_count = 0
        self.frames_seen = 0
        self.states_seen = 0
        self.decode_errors = 0
        self.last_error = ""
        self.browser_diagnostic: dict[str, Any] = {}
        self._last_stage = ""
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        return time.monotonic() - self.last_seen < 6.0

    @property
    def live_ready(self) -> bool:
        """A browser ping is insufficient; require one decoded game state."""
        return self.states_seen > 0

    def _emit_stage(self, stage: str, message: str) -> None:
        if stage != self._last_stage:
            self._last_stage = stage
            self.signals.status.emit(message)

    def _update_browser_diagnostic(self, value: dict[str, Any]) -> None:
        self.browser_diagnostic = value
        sockets = int(value.get("socketCount", 0) or 0)
        browser_frames = int(value.get("sentFrames", 0) or 0) + int(value.get("receivedFrames", 0) or 0)
        version = str(value.get("extensionVersion", ""))
        if version and version != EXPECTED_EXTENSION_VERSION:
            self._emit_stage(
                "extension-old",
                f"网页 Hook 扩展版本 {version} 过旧 · 请在 Chrome 扩展页重新加载",
            )
        elif not value.get("hookReady"):
            self._emit_stage("bridge-only", "扩展已连接，但页面 Hook 尚未注入")
        elif sockets <= 0:
            self._emit_stage("hook-ready", "网页 Hook 已注入 · 等待雀魂建立连接")
        elif self.states_seen <= 0 and self.decode_errors:
            self._emit_stage(
                "decode-error",
                f"已收到 {self.frames_seen} 个数据帧，但协议解析失败 {self.decode_errors} 次",
            )
        elif self.states_seen <= 0 and self.frames_seen > 0:
            self._emit_stage("frames-ready", f"已收到 {self.frames_seen} 个数据帧 · 等待开局状态")
        elif browser_frames <= 0:
            self._emit_stage("socket-ready", "已截获雀魂 WebSocket · 等待牌局数据")
        elif self.states_seen <= 0:
            self._emit_stage("frames-ready", f"已收到 {self.frames_seen} 个数据帧 · 等待开局状态")

    def start(self) -> bool:
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, code=204):
                self.send_response(code)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_OPTIONS(self):
                self._reply()

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length <= 0 or length > MAX_BODY or self.path not in {"/ping", "/schema", "/frame", "/diagnostic"}:
                    self._reply(400)
                    return
                raw = self.rfile.read(length)
                try:
                    bridge.last_seen = time.monotonic()
                    if not bridge._announced:
                        bridge._announced = True
                        bridge.signals.status.emit("网页 Hook 已连接")
                    if bridge.protocol.decoder is not None and not bridge._schema_announced:
                        bridge._schema_announced = True
                        bridge.signals.status.emit("网页 Hook 协议已就绪")
                    if self.path == "/ping":
                        bridge.ping_count += 1
                        if bridge.ping_count >= 3 and not bridge.browser_diagnostic:
                            bridge._emit_stage(
                                "extension-old",
                                "扩展可连接但没有实时诊断 · 请在 Chrome 扩展页重新加载",
                            )
                    elif self.path == "/schema":
                        bridge.protocol.set_schema(json.loads(raw))
                        bridge._schema_announced = True
                        bridge.signals.status.emit("网页 Hook 协议已就绪")
                    elif self.path == "/diagnostic":
                        value = json.loads(raw)
                        if not isinstance(value, dict):
                            raise TypeError("diagnostic must be an object")
                        bridge._update_browser_diagnostic(value)
                    elif self.path == "/frame":
                        packet = json.loads(raw)
                        frame = base64.b64decode(packet["data"], validate=True)
                        bridge.frames_seen += 1
                        if bridge.frames_seen == 1:
                            bridge._emit_stage("frames-ready", "已捕获雀魂数据帧 · 正在解析牌局")
                        state = bridge.protocol.feed(packet.get("direction", "receive"), frame,
                                                     str(packet.get("socketId") or packet.get("url", "")))
                        if state is not None:
                            bridge.states_seen += 1
                            bridge._last_stage = "state-ready"
                            bridge.signals.status.emit(f"实时牌局已识别 · 手牌 {len(state.hand)} 张")
                            bridge.signals.state.emit(state)
                    self._reply()
                except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                    if self.path == "/frame":
                        bridge.decode_errors += 1
                        bridge.last_error = f"{type(error).__name__}: {str(error)[:160]}"
                        bridge._last_stage = ""
                        bridge._update_browser_diagnostic(bridge.browser_diagnostic)
                    self._reply(400)

            def log_message(self, *_):
                pass

        try:
            self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        except OSError:
            self.signals.status.emit("网页 Hook 端口 8765 被占用")
            return False
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True,
                                        name="mahjong-jev-hook")
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(1)
            self._thread = None
