"""Offline GameState and Liqi WebSocket frame replay with measurable latency."""

from __future__ import annotations

import argparse
import base64
import json
import statistics
import sys
import time
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from .hook import HookProtocol
from .jev import JevError, advise
from .rules import NoDecision, UncertainState, candidates, local_choice
from .settings import Settings
from .state import Advice, GameState


def percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * (len(ordered) - 1) + 0.999999))]


def _states(path: Path):
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        data = json.loads(line)
        yield line_number, GameState.from_dict(data.get("state", data))


def _frames(path: Path):
    protocol = HookProtocol()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        packet = json.loads(line)
        if "seat" in packet and isinstance(packet["seat"], int):
            protocol.builder.own_seat = packet["seat"]
        try:
            raw = (
                bytes.fromhex(packet["hex"])
                if "hex" in packet
                else base64.b64decode(packet["data"], validate=True)
            )
            state = protocol.feed(
                packet.get("direction", "receive"),
                raw,
                str(packet.get("socketId") or packet.get("url", "")),
            )
            if state is not None:
                yield str(packet.get("frame", line_number)), state, []
        except Exception as error:
            yield str(packet.get("frame", line_number)), None, [str(error)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay GameStates or recorded Liqi WebSocket frames")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--states", type=Path, help="JSONL with one GameState per line")
    source.add_argument("--frames", type=Path, help="JSONL with recorded Liqi WebSocket frames (base64 data or hex)")
    parser.add_argument("--online", action="store_true", help="Use Jev; default is the local rules baseline")
    parser.add_argument("--truth", type=Path, help="Optional JSONL truth for --frames; fields hand and buttons")
    parser.add_argument("--output", type=Path, help="Optional output JSONL")
    args = parser.parse_args()
    settings = Settings.load()
    if args.online and not settings.api_key:
        parser.error("--online requires an API Key for the selected provider in Settings")
    truth = {}
    if args.truth:
        for line in args.truth.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                truth[str(item["frame"])] = item
    source_rows = (
        ((str(n), state, []) for n, state in _states(args.states))
        if args.states
        else _frames(args.frames)
    )
    latencies: list[float] = []
    results: list[dict] = []
    recognized_hand = total_hand = recognized_button = total_button = 0
    for name, state, problems in source_rows:
        started = time.perf_counter()
        row: dict = {"frame": name, "problems": problems}
        if state and not problems:
            row["state"] = state.to_dict()
            if name in truth:
                expected = truth[name]
                total_hand += 1
                recognized_hand += tuple(expected.get("hand", [])) == state.hand
                total_button += 1
                recognized_button += set(expected.get("buttons", [])) == set(state.buttons)
            try:
                options = candidates(state)
                if args.online:
                    advice = advise(state, options, settings.api_key, connection=settings.connection())
                else:
                    selected = local_choice(options, state)
                    advice = Advice(selected, tuple(x for x in options if x != selected)[:3], source="rules")
                row["advice"] = advice.to_dict()
            except (NoDecision, UncertainState, JevError) as error:
                row["problems"].append(str(error))
        row["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        latencies.append(row["latency_ms"])
        results.append(row)
        try:
            print(json.dumps(row, ensure_ascii=False))
        except UnicodeEncodeError:
            print(json.dumps(row, ensure_ascii=True))
    if args.output:
        args.output.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in results) + "\n", encoding="utf-8")
    summary = {
        "frames": len(results),
        "p95_latency_ms": percentile_95(latencies),
        "mean_latency_ms": round(statistics.mean(latencies), 1) if latencies else 0,
        "hand_exact_accuracy": recognized_hand / total_hand if total_hand else None,
        "button_exact_accuracy": recognized_button / total_button if total_button else None,
        "abstentions": sum("advice" not in row for row in results),
    }
    summary_str = "SUMMARY " + json.dumps(summary, ensure_ascii=False)
    try:
        print(summary_str)
    except UnicodeEncodeError:
        print("SUMMARY " + json.dumps(summary, ensure_ascii=True))


if __name__ == "__main__":
    main()
