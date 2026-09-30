import json
import pytest

from mahjong_jev_advisor.hook import HookProtocol, HookStateBuilder, LiqiDecoder
from mahjong_jev_advisor.tiles import RED_TILES, TILES


def vi(value):
    out = bytearray()
    while value > 0x7f:
        out.append((value & 0x7f) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def field(number, value, wire=2):
    if wire == 0:
        return vi(number << 3) + vi(value)
    return vi((number << 3) | 2) + vi(len(value)) + value


SCHEMA = {"nested": {"lq": {"nested": {
    "Wrapper": {"fields": {"name": {"type": "string", "id": 1}, "data": {"type": "bytes", "id": 2}}},
    "ActionPrototype": {"fields": {"step": {"type": "uint32", "id": 1}, "name": {"type": "string", "id": 2}, "data": {"type": "bytes", "id": 3}}},
    "OptionalOperation": {"fields": {"type": {"type": "uint32", "id": 1}}},
    "OptionalOperationList": {"fields": {"seat": {"type": "uint32", "id": 1}, "operation_list": {"rule": "repeated", "type": "OptionalOperation", "id": 2}}},
    "ActionNewRound": {"fields": {
        "chang": {"type": "uint32", "id": 1}, "ju": {"type": "uint32", "id": 2},
        "ben": {"type": "uint32", "id": 3}, "tiles": {"rule": "repeated", "type": "string", "id": 4},
        "dora": {"type": "string", "id": 5}, "scores": {"rule": "repeated", "type": "int32", "id": 6},
        "operation": {"type": "OptionalOperationList", "id": 7}, "liqibang": {"type": "uint32", "id": 8}
    }},
    "ActionDealTile": {"fields": {"seat": {"type": "uint32", "id": 1}, "tile": {"type": "string", "id": 2}, "operation": {"type": "OptionalOperationList", "id": 4}}},
    "ActionDiscardTile": {"fields": {"seat": {"type": "uint32", "id": 1}, "tile": {"type": "string", "id": 2}, "is_liqi": {"type": "bool", "id": 3}, "operation": {"type": "OptionalOperationList", "id": 4}}}
}}}}


def action_frame(name, payload):
    from mahjong_jev_advisor.hook import decode_action_data
    prototype = field(2, name.encode()) + field(3, decode_action_data(payload))
    wrapper = field(1, b".lq.ActionPrototype") + field(2, prototype)
    return b"\x01" + wrapper


def test_dynamic_decoder_and_live_state_reconstruction():
    protocol = HookProtocol()
    protocol.set_schema(SCHEMA)
    tiles = [b"1m", b"2m", b"3m", b"4p", b"5p", b"6p", b"7s", b"8s", b"9s", b"1z", b"2z", b"3z", b"5m"]
    operation = field(1, 0, 0)
    new_round = field(1, 0, 0) + field(2, 0, 0) + b"".join(field(4, tile) for tile in tiles)
    new_round += field(5, b"7p") + b"".join(field(6, 25000, 0) for _ in range(4)) + field(7, operation)
    state = protocol.feed("receive", action_frame(".lq.ActionNewRound", new_round))
    assert state is not None and len(state.hand) == 13 and state.dora_indicators == ("7p",)

    deal = field(1, 0, 0) + field(2, b"5m") + field(4, operation)
    state = protocol.feed("receive", action_frame(".lq.ActionDealTile", deal))
    assert state is not None and len(state.hand) == 14

    pon_for_self = field(1, 0, 0) + field(2, field(1, 3, 0))
    discard = field(1, 1, 0) + field(2, b"5m") + field(4, pon_for_self)
    state = protocol.feed("receive", action_frame(".lq.ActionDiscardTile", discard))
    assert state.last_discard == "5m" and state.buttons == {"pon", "pass"}


def test_decoder_rejects_unknown_type():
    decoder = LiqiDecoder(SCHEMA)
    try:
        decoder.decode("lq.Unknown", b"")
    except ValueError as error:
        assert "unknown protobuf type" in str(error)
    else:
        raise AssertionError("unknown type must fail")


def test_every_supported_tile_code_reconstructs_in_live_hand():
    for target in TILES + RED_TILES:
        hand = [target]
        hand.extend(tile for tile in TILES if tile != target and len(hand) < 13)
        builder = HookStateBuilder()
        builder.own_seat = 0
        state = builder.apply("ActionNewRound", {
            "chang": 0,
            "ju": 0,
            "tiles": hand,
            "dora": "9s",
            "scores": [25000] * 4,
        })
        assert state is not None
        assert target in state.hand


def test_all_operation_types_are_exposed_as_legal_buttons():
    builder = HookStateBuilder()
    builder._operations({
        "seat": 2,
        "operationList": [{"type": value} for value in range(2, 11)],
    })
    assert builder.own_seat == 2
    assert builder.buttons == {
        "chi", "pon", "kan", "riichi", "tsumo", "ron", "kyuushu", "pass",
    }


def test_snapshot_reconstructs_each_seat_and_all_public_table_columns():
    hand = list(TILES[:13])
    players = [
        {"score": 31000, "qipais": ["5p"], "mings": [], "liqiposition": 0},
        {"score": 27000, "qipais": ["6p", "7p"], "mings": [], "liqiposition": 4},
        {"score": 23000, "qipais": ["8p"], "mings": [], "liqiposition": 0},
        {"score": 19000, "qipais": ["9p"], "mings": [], "liqiposition": 7},
    ]
    for seat in range(4):
        builder = HookStateBuilder()
        state = builder.restore({"gameRestore": {"snapshot": {
            "chang": 1,
            "ju": 2,
            "ben": 1,
            "liqibang": 2,
            "indexPlayer": seat,
            "hands": hand,
            "doras": ["1s"],
            "players": players,
        }}})
        assert state is not None
        assert state.seat == seat
        assert state.scores == (31000, 27000, 23000, 19000)
        assert state.rivers == (("5p",), ("6p", "7p"), ("8p",), ("9p",))
        assert state.riichi == (False, True, False, True)
        assert state.round_wind == "S" and state.round_number == 3


def test_calls_move_tiles_from_river_and_rebuild_concealed_and_added_kans():
    builder = HookStateBuilder()
    builder.own_seat = 0
    builder.hand = ["5s", "5s", "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "1p", "2p"]

    builder.apply("ActionDiscardTile", {"seat": 1, "tile": "5s"})
    state = builder.apply("ActionChiPengGang", {"seat": 0, "type": 1, "tiles": ["5s"] * 3})
    assert state is not None
    assert state.rivers[1] == ()
    assert state.melds[0] == ("5s", "5s", "5s")
    assert state.open_melds == 1 and len(state.hand) == 11

    state = builder.apply("ActionAnGangAddGang", {"seat": 2, "type": 3, "tiles": "6s"})
    assert state is not None
    assert state.melds[2] == ("6s", "6s", "6s", "6s")
    assert builder.meld_groups[2] == 1

    builder.apply("ActionDiscardTile", {"seat": 3, "tile": "7s"})
    builder.apply("ActionChiPengGang", {"seat": 2, "type": 1, "tiles": ["7s"] * 3})
    state = builder.apply("ActionAnGangAddGang", {"seat": 2, "type": 2, "tiles": "7s"})
    assert state is not None
    assert state.rivers[3] == ()
    assert state.melds[2][-4:] == ("7s", "7s", "7s", "7s")
    assert builder.meld_groups[2] == 2


def test_default_schema_loaded():
    protocol = HookProtocol()
    assert protocol.decoder is not None
    assert "lq.ActionNewRound" in protocol.decoder.types
    assert "lq.ActionDealTile" in protocol.decoder.types
    assert "lq.ActionDiscardTile" in protocol.decoder.types


def test_live_xor_known_vector_and_omitted_seat_zero():
    from mahjong_jev_advisor.hook import decode_action_data

    assert decode_action_data(bytes.fromhex("9d757b606ba1")) == bytes.fromhex("08011202356d")
    protocol = HookProtocol()
    protocol.builder.hand = list(TILES[:13])
    # A proto3 zero seat is absent on the wire, but still means seat 0.
    state = protocol.feed("receive", action_frame("ActionDealTile", field(2, b"5m")))
    assert state is not None and state.seat == 0 and len(state.hand) == 14


def test_response_wrappers_and_request_ids_isolated_by_connection():
    protocol = HookProtocol()
    auth_req = field(1, 1001, 0)
    protocol.feed("send", b"\x02\x01\x00" + field(1, b".lq.FastTest.authGame") + field(2, auth_req), "game")
    protocol.feed("send", b"\x02\x01\x00" + field(1, b".lq.Lobby.heatbeat") + field(2, b""), "lobby")
    # Packed seat_list in the real RESPONSE Wrapper.
    auth_res = field(3, vi(2002) + vi(1001) + vi(3003) + vi(4004))
    protocol.feed("receive", b"\x03\x01\x00" + field(1, b"") + field(2, auth_res), "game")
    assert protocol.builder.own_seat == 1
    assert ("lobby", 1) in protocol.requests


@pytest.mark.parametrize("method", ["enterGame", "syncGame"])
def test_wrapped_restore_actions_are_plain_not_xor(method):
    protocol = HookProtocol()
    request = field(1, f".lq.FastTest.{method}".encode()) + field(2, b"")
    protocol.feed("send", b"\x02\x07\x00" + request)
    snapshot = b"".join(field(6, tile.encode()) for tile in TILES[:13])
    # Snapshot index_player=0 is also omitted, as on a proto3 connection.
    deal = field(2, b"5m")
    plain_action = field(2, b"ActionDealTile") + field(3, deal)
    restore = field(1, snapshot) + field(2, plain_action)
    response = field(4, restore)
    state = protocol.feed("receive", b"\x03\x07\x00" + field(1, b"") + field(2, response))
    assert state is not None and len(state.hand) == 14 and state.hand[-1] == "5m"
    assert state.seat == 0


def test_wire_type_mismatch_raises_controlled_error():
    decoder = HookProtocol().decoder
    with pytest.raises(ValueError, match="wire mismatch"):
        decoder.decode("lq.ActionDealTile", field(2, 1, 0))


def test_full_round_flow_with_bundled_schema():
    protocol = HookProtocol()
    # 1. authGame
    auth_req = field(1, 1001, 0)
    wrapper_req = field(1, b".lq.FastTest.authGame") + field(2, auth_req)
    protocol.feed("send", b"\x02\x01\x00" + wrapper_req)

    auth_res = field(3, 2002, 0) + field(3, 1001, 0) + field(3, 3003, 0) + field(3, 4004, 0)
    protocol.feed("receive", b"\x03\x01\x00" + field(1, b"") + field(2, auth_res))
    assert protocol.builder.own_seat == 1

    # 2. ActionNewRound
    tiles = ["1m", "2m", "3m", "4p", "5p", "6p", "7s", "8s", "9s", "1z", "2z", "3z", "4z"]
    new_round = field(1, 0, 0) + field(2, 0, 0) + b"".join(field(4, t.encode()) for t in tiles)
    new_round += field(5, b"7p") + b"".join(field(6, 25000, 0) for _ in range(4))
    state1 = protocol.feed("receive", action_frame(".lq.ActionNewRound", new_round))
    assert state1 is not None and len(state1.hand) == 13

    # 3. ActionDealTile (seat=1 draws 5m)
    deal = field(1, 1, 0) + field(2, b"5m")
    state2 = protocol.feed("receive", action_frame(".lq.ActionDealTile", deal))
    assert state2 is not None and len(state2.hand) == 14 and state2.hand[-1] == "5m"

    # 4. ActionDiscardTile (seat=1 discards 1m)
    discard = field(1, 1, 0) + field(2, b"1m")
    state3 = protocol.feed("receive", action_frame(".lq.ActionDiscardTile", discard))
    assert state3 is not None and len(state3.hand) == 13 and "1m" not in state3.hand
    assert state3.rivers[1] == ("1m",)


def test_red_five_removal_precision():
    protocol = HookProtocol()
    protocol.builder.own_seat = 0
    protocol.builder.hand = ["5m", "0m", "1p", "2p"]
    protocol.builder._remove("0m")
    assert protocol.builder.hand == ["5m", "1p", "2p"]
    protocol.builder._remove("5m")
    assert protocol.builder.hand == ["1p", "2p"]


def test_gang_babei_and_score_updates():
    protocol = HookProtocol()
    protocol.builder.own_seat = 0
    protocol.builder.hand = ["1m", "1m", "1m", "1m", "4z", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p", "1z"]
    protocol.builder.doras = ["1s"]

    # 1. Ankan 1m (adds gang meld, removes 4 copies of 1m, updates dora)
    gang = field(1, 0, 0) + field(2, 1, 0) + field(3, b"1m") + field(5, b"2s")
    state1 = protocol.builder.apply("ActionAnGangAddGang", {
        "seat": 0, "type": 1, "tiles": "1m", "doras": ["1s", "2s"]
    })
    assert state1 is not None
    assert "1m" not in state1.hand
    assert state1.dora_indicators == ("1s", "2s")
    assert state1.melds[0] == ("1m", "1m", "1m", "1m")

    # 2. BaBei (Kita, removes 4z; transient until rinshan draw)
    state_transient = protocol.builder.apply("ActionBaBei", {
        "seat": 0, "doras": ["1s", "2s", "3s"]
    })
    assert state_transient is None
    assert "4z" not in protocol.builder.hand

    # 3. Rinshan deal tile arrives after Kita
    state2 = protocol.builder.apply("ActionDealTile", {
        "seat": 0, "tile": "8s"
    })
    assert state2 is not None
    assert state2.hand[-1] == "8s"
    assert state2.dora_indicators == ("1s", "2s", "3s")

    # 3. ActionNoTile with deltaScores
    scores_info = {
        "oldScores": [25000, 25000, 25000, 25000],
        "deltaScores": [3000, -1000, -1000, -1000],
    }
    state3 = protocol.builder.apply("ActionNoTile", {"scores": [scores_info]})
    assert state3 is not None
    assert state3.scores == (28000, 24000, 24000, 24000)


def test_restore_with_post_snapshot_actions():
    protocol = HookProtocol()
    # ReqSyncGame
    req = field(1, b"round123")
    wrapper_req = field(1, b".lq.FastTest.syncGame") + field(2, req)
    protocol.feed("send", b"\x02\x05\x00" + wrapper_req)

    # ResSyncGame with GameRestore
    snapshot_payload = {
        "chang": 0, "ju": 0, "ben": 0, "liqibang": 0,
        "indexPlayer": 0,
        "hands": ["1m", "2m", "3m", "4p", "5p", "6p", "7s", "8s", "9s", "1z", "2z", "3z", "4z"],
        "doras": ["5m"],
        "players": [
            {"score": 25000, "qipais": [], "mings": [], "liqiposition": 0},
            {"score": 25000, "qipais": [], "mings": [], "liqiposition": 0},
            {"score": 25000, "qipais": [], "mings": [], "liqiposition": 0},
            {"score": 25000, "qipais": [], "mings": [], "liqiposition": 0},
        ]
    }
    action1_data = protocol.decoder.decode  # verify decoder exists
    deal_tile_data = field(1, 0, 0) + field(2, b"9m")  # seat 0 draws 9m
    discard_tile_data = field(1, 0, 0) + field(2, b"1m")  # seat 0 discards 1m

    # Test builder restore directly first
    state = protocol.builder.restore({"gameRestore": {"snapshot": snapshot_payload}})
    assert state is not None and len(state.hand) == 13 and state.seat == 0

    # Apply actions
    state = protocol.builder.apply("ActionDealTile", {"seat": 0, "tile": "9m"})
    assert state is not None and len(state.hand) == 14 and "9m" in state.hand
    state = protocol.builder.apply("ActionDiscardTile", {"seat": 0, "tile": "1m"})
    assert state is not None and len(state.hand) == 13 and "1m" not in state.hand


def test_hook_bridge_http_server():
    import base64
    import urllib.request
    from mahjong_jev_advisor.hook import HookBridge

    bridge = HookBridge(port=18765)
    assert bridge.start() is True

    try:
        # 1. Ping
        req = urllib.request.Request("http://127.0.0.1:18765/ping", data=b"{}", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 204
        assert bridge.connected is True
        assert bridge.live_ready is False

        diagnostic = json.dumps({
            "hookReady": True,
            "extensionVersion": "0.1.3",
            "socketCount": 1,
            "openSocketCount": 1,
            "sentFrames": 2,
            "receivedFrames": 3,
            "lastSocketHost": "route.example.test",
        }).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:18765/diagnostic",
            data=diagnostic,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 204
        assert bridge.browser_diagnostic["socketCount"] == 1
        assert bridge.browser_diagnostic["extensionVersion"] == "0.1.3"
        assert bridge.live_ready is False

        # 2. Frame
        received_states = []
        from PySide6.QtCore import Qt
        bridge.signals.state.connect(received_states.append, Qt.ConnectionType.DirectConnection)
        bridge.protocol.builder.own_seat = 0

        tiles = ["1m", "2m", "3m", "4p", "5p", "6p", "7s", "8s", "9s", "1z", "2z", "3z", "4z"]
        new_round = field(1, 0, 0) + field(2, 0, 0) + b"".join(field(4, t.encode()) for t in tiles)
        new_round += field(5, b"7p") + b"".join(field(6, 25000, 0) for _ in range(4))
        frame_bytes = action_frame(".lq.ActionNewRound", new_round)

        payload = json.dumps({
            "direction": "receive",
            "url": "wss://test.maj-soul.com",
            "data": base64.b64encode(frame_bytes).decode("ascii")
        }).encode("utf-8")

        req = urllib.request.Request("http://127.0.0.1:18765/frame", data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 204

        # Verify state received via signal
        assert len(received_states) == 1
        assert len(received_states[0].hand) == 13
        assert bridge.frames_seen == 1
        assert bridge.states_seen == 1
        assert bridge.live_ready is True
    finally:
        bridge.stop()
