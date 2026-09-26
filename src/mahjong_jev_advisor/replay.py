"""Offline state and screenshot replay with measurable recognition/latency."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import cv2

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

from .jev import advise
from .rules import NoDecision, UncertainState, candidates, local_choice
from .settings import Settings
from .state import Advice, GameState
from .vision import VisionReader


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


def _frames(path: Path, settings: Settings):
    reader = VisionReader(settings.regions)
    for file in sorted([*path.glob("*.png"), *path.glob("*.jpg")]):
        frame = cv2.imread(str(file))
        if frame is None:
            yield file.name, None, ["图片无法读取"]
            continue
        obs = reader.analyze(frame, 0)
        yield file.name, obs.state, list(obs.problems)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay states or calibrated Mahjong Soul screenshots")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--states", type=Path, help="JSONL with one GameState per line")
    source.add_argument("--frames", type=Path, help="Directory of recorded game-region PNG/JPG frames")
    parser.add_argument("--online", action="store_true", help="Use Jev; default is the local rules baseline")
    parser.add_argument("--truth", type=Path, help="Optional JSONL truth for --frames; fields hand and buttons")
    parser.add_argument("--output", type=Path, help="Optional output JSONL")
    args = parser.parse_args()
    settings = Settings.load()
    if args.frames and not settings.regions:
        parser.error("Calibrate regions in the app before replaying screenshots")
    if args.online and not settings.api_key:
        parser.error("--online requires a TypeSafe API Key in Settings or TYPESAFE_API_KEY")
    truth = {}
    if args.truth:
        for line in args.truth.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                truth[item["frame"]] = item
    source_rows = (
        ((str(n), state, []) for n, state in _states(args.states))
        if args.states else _frames(args.frames, settings)
    )
    latencies: list[float] = []
    results: list[dict] = []
    recognized_hand = total_hand = recognized_button = total_button = 0
    for name, state, problems in source_rows:
        started = time.perf_counter()
        row: dict = {"frame": name, "problems": problems}
        if state:
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
                    advice = advise(state, options, settings.api_key)
                else:
                    selected = local_choice(options, state)
                    advice = Advice(selected, tuple(x for x in options if x != selected)[:3], source="rules")
                row["advice"] = advice.to_dict()
            except (NoDecision, UncertainState) as error:
                row["problems"].append(str(error))
        row["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        latencies.append(row["latency_ms"])
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
