"""Frozen GUI entry point; smoke mode runs without user credentials or screens."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import traceback


def smoke_test(output: Path) -> int:
    try:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        with tempfile.TemporaryDirectory(prefix="mahjong-smoke-") as config:
            os.environ["MAHJONG_ADVISOR_CONFIG_DIR"] = config
            for name in ("AI_GATEWAY_API_KEY", "TYPESAFE_API_KEY", "OPENROUTER_API_KEY"):
                os.environ.pop(name, None)
            from PySide6.QtWidgets import QApplication
            from mahjong_jev_advisor.app import MainWindow
            from mahjong_jev_advisor.edition import launch_edition
            from mahjong_jev_advisor.hook import HookProtocol
            from mahjong_jev_advisor.resources import extension_dir
            from mahjong_jev_advisor.rules import candidates
            from mahjong_jev_advisor.settings import Settings
            from mahjong_jev_advisor.state import GameState
            from mahjong_jev_advisor.steam_capture import (
                SteamCaptureBridge,
                ensure_mitm_ca_generated,
            )

            edition = launch_edition() or "browser"
            if Settings.load().input_source != edition:
                raise RuntimeError("Incorrect initial input source")
            if edition == "browser":
                extension = extension_dir()
                for name in ("manifest.json", "content.js", "page-hook.js"):
                    if not (extension / name).is_file():
                        raise RuntimeError(f"Missing extension file: {name}")
            if HookProtocol().decoder is None:
                raise RuntimeError("Bundled Liqi schema did not load")
            conf_dir = Path(config) / "mitm"
            if not ensure_mitm_ca_generated(conf_dir).exists():
                raise RuntimeError("Failed to generate local MITM CA certificate")
            bridge = SteamCaptureBridge(mode="proxy", proxy_port=19876, confdir=conf_dir)
            if not bridge.start():
                raise RuntimeError(f"Failed to start SteamCaptureBridge: {bridge.last_error}")
            bridge.stop()

            app = QApplication([])
            window = MainWindow()
            try:
                if window.settings.input_source != edition:
                    raise RuntimeError("Wrong edition in GUI")
                if edition == "steam" and window.hook.connected:
                    raise RuntimeError("Steam edition unexpectedly connected to browser Hook")
                for count, hand in ((4, "123m456p789s123z55m"), (3, "19m123456p123s114z")):
                    state = GameState.from_dict({"player_count": count, "hand": hand})
                    if not candidates(state):
                        raise RuntimeError(f"No candidates for {count}-player fixture")
                    window._render_state(state)
                window.show()
                app.processEvents()
                if window.grab().isNull():
                    raise RuntimeError("Qt failed to render the window")
            finally:
                window.close()
                app.processEvents()
        report = {
            "ok": True,
            "edition": edition,
            "checks": (["extension"] if edition == "browser" else [])
            + ["edition-default", "liqi", "steam-mitm-engine", "four-player", "three-player", "qt-render"],
        }
        code = 0
    except Exception:
        report, code = {"ok": False, "error": traceback.format_exc()}, 1
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return code


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        raise SystemExit(smoke_test(Path(sys.argv[2]).resolve()))
    if len(sys.argv) >= 2 and sys.argv[1] == "--steam-capture-worker":
        from mahjong_jev_advisor.steam_capture import run_steam_capture_worker

        raise SystemExit(run_steam_capture_worker(sys.argv[2:]))
    from mahjong_jev_advisor.app import main

    main()
