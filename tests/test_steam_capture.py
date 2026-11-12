from __future__ import annotations

import json
import socket
import time
import urllib.request
from types import SimpleNamespace

from PySide6.QtCore import Qt

from mahjong_jev_advisor.hook import (
    HookBridge,
    HookStateBuilder,
    build_pac_script,
    decode_action_data,
)
from mahjong_jev_advisor.steam_capture import (
    ReadOnlyLiqiAddon,
    SteamCaptureBridge,
    ensure_mitm_ca_generated,
    generate_clash_rules,
    generate_pac_script,
    generate_surge_rules,
    is_bypass_tls_host,
    is_likely_liqi_frame,
    is_majsoul_host,
)


def _vi(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _field(number: int, value: bytes | int, wire: int = 2) -> bytes:
    if wire == 0:
        assert isinstance(value, int)
        return _vi(number << 3) + _vi(value)
    assert isinstance(value, bytes)
    return _vi((number << 3) | 2) + _vi(len(value)) + value


def _action_frame(name: str, payload: bytes) -> bytes:
    prototype = _field(2, name.encode()) + _field(3, decode_action_data(payload))
    wrapper = _field(1, b".lq.ActionPrototype") + _field(2, prototype)
    return b"\x01" + wrapper


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_host_classification_and_tls_bypass():
    assert is_majsoul_host("game.maj-soul.com")
    assert is_majsoul_host("gateway-hw.maj-soul.com:443")
    assert is_majsoul_host("mahjongsoul.game.yo-star.com")
    assert not is_majsoul_host("store.steampowered.com")
    assert not is_majsoul_host("")

    assert is_bypass_tls_host("api.steampowered.com")
    assert is_bypass_tls_host("steamcommunity.com")
    assert not is_bypass_tls_host("game.maj-soul.com")


def test_liqi_binary_frame_signature_detection():
    tiles = [b"1m", b"2m", b"3m", b"4p", b"5p", b"6p", b"7s", b"8s", b"9s", b"1z", b"2z", b"3z", b"4z"]
    new_round = _field(1, 0, 0) + _field(2, 0, 0) + b"".join(_field(4, t) for t in tiles)
    notify_frame = _action_frame(".lq.ActionNewRound", new_round)
    assert is_likely_liqi_frame(notify_frame)

    req_wrapper = _field(1, b".lq.FastTest.authGame") + _field(2, _field(1, 1001, 0))
    req_frame = b"\x02\x09\x00" + req_wrapper
    assert is_likely_liqi_frame(req_frame)

    res_wrapper = _field(1, b"") + _field(2, _field(3, 1001, 0))
    res_frame = b"\x03\x09\x00" + res_wrapper
    assert is_likely_liqi_frame(res_frame)

    assert not is_likely_liqi_frame(b"")
    assert not is_likely_liqi_frame(b"{\"hello\": \"world\"}")
    assert not is_likely_liqi_frame(b"\x09\x00\x00\x00")


def test_pac_clash_and_surge_rule_generators():
    pac = generate_pac_script(10999)
    assert "PROXY 127.0.0.1:10999; DIRECT" in pac
    assert "maj-soul.com" in pac
    assert pac == build_pac_script(10999)

    clash = generate_clash_rules(10999)
    assert "port: 10999" in clash
    assert "PROCESS-NAME,Jantama_MahjongSoul.exe,mahjong-jev-local" in clash
    assert "DOMAIN-SUFFIX,maj-soul.com,mahjong-jev-local" in clash

    surge = generate_surge_rules(10999)
    assert "127.0.0.1, 10999" in surge
    assert "DOMAIN-SUFFIX,maj-soul.com,mahjong-jev-local" in surge


def test_mitm_ca_generation(tmp_path, monkeypatch):
    monkeypatch.setattr("mahjong_jev_advisor.steam_capture.mitm_confdir", lambda: tmp_path)
    pem_path = ensure_mitm_ca_generated()
    assert pem_path.exists()
    content = pem_path.read_text(encoding="utf-8")
    assert "BEGIN CERTIFICATE" in content


def test_readonly_liqi_addon_never_mutates_packets():
    frames: list[tuple[str, bytes, str, str]] = []
    schemas: list[dict] = []
    addon = ReadOnlyLiqiAddon(
        on_frame=lambda d, p, c, h: frames.append((d, p, c, h)),
        on_schema=schemas.append,
    )

    # 1. Verify TLS ClientHello bypass for Steam / non-game hosts
    steam_hello = SimpleNamespace(
        client_hello=SimpleNamespace(sni="api.steampowered.com"),
        ignore_connection=False,
    )
    addon.tls_clienthello(steam_hello)
    assert steam_hello.ignore_connection is True

    game_hello = SimpleNamespace(
        client_hello=SimpleNamespace(sni="gateway-hw.maj-soul.com"),
        ignore_connection=False,
    )
    addon.tls_clienthello(game_hello)
    assert game_hello.ignore_connection is False

    # 2. Verify WebSocket frame is observed strictly read-only
    raw_frame = _action_frame(".lq.ActionDealTile", _field(1, 0, 0) + _field(2, b"5m"))
    msg = SimpleNamespace(content=raw_frame, from_client=False, is_text=False, dropped=False)
    flow = SimpleNamespace(
        id="flow-123",
        request=SimpleNamespace(pretty_host="gateway-hw.maj-soul.com", path="/gateway"),
        websocket=SimpleNamespace(messages=[msg]),
    )
    addon.websocket_message(flow)
    assert msg.content == raw_frame
    assert msg.dropped is False
    assert len(frames) == 1
    assert frames[0] == ("receive", raw_frame, "flow-123", "gateway-hw.maj-soul.com")

    # 3. Verify liqi.json schema response is observed strictly read-only
    sample_schema = {"nested": {"lq": {"nested": {"Wrapper": {"fields": {}}}}}}
    raw_json = json.dumps(sample_schema).encode("utf-8")
    schema_flow = SimpleNamespace(
        request=SimpleNamespace(pretty_host="game.maj-soul.com", path="/v0.11.210.w/liqi.json"),
        response=SimpleNamespace(status_code=200, get_content=lambda strict=False: raw_json),
    )
    addon.response(schema_flow)
    assert schemas == [sample_schema]


def test_dealer_seat_inference_hand_sorting_and_red_five_kan():
    builder = HookStateBuilder()
    # Dealer receives 14 tiles on ActionNewRound in East-2 (ju=1) without prior authGame.
    # The first 13 concealed tiles are sorted; the 14th tile stays in the drawn slot.
    unsorted_14 = [
        "9s", "1m", "0p", "5p", "2m", "3m", "7z", "1z",
        "4p", "6p", "8s", "7s", "1z", "5m",
    ]
    state = builder.apply("ActionNewRound", {
        "chang": 0,
        "ju": 1,
        "ben": 0,
        "tiles": unsorted_14,
        "dora": "1p",
        "scores": [25000, 25000, 25000, 25000],
    })
    assert state is not None
    assert state.seat == 1
    assert state.seat_wind == "E"
    assert list(state.hand) == [
        "1m", "2m", "3m", "4p", "0p", "5p", "6p",
        "7s", "8s", "9s", "1z", "1z", "7z", "5m",
    ]

    # Discard 7z -> 13 tiles sorted (5m is now sorted into the manzu section)
    state = builder.apply("ActionDiscardTile", {"seat": 1, "tile": "7z"})
    assert state is not None and len(state.hand) == 13
    assert state.hand[:4] == ("1m", "2m", "3m", "5m")

    # Draw 1m -> drawn tile stays at index -1 for UI separation
    state = builder.apply("ActionDealTile", {"seat": 1, "tile": "1m"})
    assert state is not None and state.hand[-1] == "1m"

    # Discard 9s -> 1m is sorted into the concealed hand
    state = builder.apply("ActionDiscardTile", {"seat": 1, "tile": "9s"})
    assert state is not None
    assert state.hand[:2] == ("1m", "1m")

    # Concealed Kan with red five 0m in hand must not duplicate 0m
    builder.hand = ["0m", "5m", "5m", "5m", "1p", "2p", "3p", "4s", "5s", "6s", "1z", "1z", "1z", "2z"]
    state = builder.apply("ActionAnGangAddGang", {"seat": 1, "type": 1, "tiles": "0m"})
    assert state is not None
    assert state.melds[1] == ("0m", "5m", "5m", "5m")
    assert state.melds[1].count("0m") == 1


def test_hook_bridge_pac_and_health_endpoints():
    port = _free_port()
    bridge = HookBridge(port=port, proxy_port=10999)
    assert bridge.start() is True
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/proxy.pac") as resp:
            assert resp.status == 200
            pac_body = resp.read().decode("utf-8")
            assert "PROXY 127.0.0.1:10999; DIRECT" in pac_body

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health") as resp:
            assert resp.status == 200
            health = json.loads(resp.read().decode("utf-8"))
            assert health["ok"] is True
            assert health["proxy_port"] == 10999
    finally:
        bridge.stop()


def test_steam_capture_bridge_live_proxy_lifecycle_and_frame_ingestion():
    port = _free_port()
    bridge = SteamCaptureBridge(mode="proxy", proxy_port=port)
    states = []
    statuses = []
    bridge.signals.state.connect(states.append, Qt.ConnectionType.DirectConnection)
    bridge.signals.status.connect(statuses.append, Qt.ConnectionType.DirectConnection)

    assert bridge.start() is True
    try:
        snap = bridge.diagnostic_snapshot()
        assert snap["running"] is True
        assert snap["active_mode_spec"] == f"regular@127.0.0.1:{port}"

        # Feed a 14-tile dealer ActionNewRound frame directly into the bridge
        tiles = [b"1m", b"2m", b"3m", b"4p", b"5p", b"6p", b"7s", b"8s", b"9s", b"1z", b"2z", b"3z", b"5m", b"5m"]
        new_round = _field(1, 0, 0) + _field(2, 0, 0) + b"".join(_field(4, t) for t in tiles)
        new_round += _field(5, b"7p") + b"".join(_field(6, 25000, 0) for _ in range(4))
        frame = _action_frame(".lq.ActionNewRound", new_round)
        bridge.ingest_frame("receive", frame, "steam-ws-1")

        assert len(states) == 1
        assert len(states[0].hand) == 14
        assert states[0].seat == 0
        assert bridge.frames_seen == 1
        assert bridge.states_seen == 1
        assert bridge.live_ready is True
    finally:
        bridge.stop()
        assert bridge.running is False
