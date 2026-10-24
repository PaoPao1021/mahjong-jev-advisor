"""Identify a portable edition or an explicit source-launch preference."""
from __future__ import annotations

import json
import os
from pathlib import Path


def launch_edition() -> str:
    marker = Path(__file__).with_name("_edition.json")
    # A portable package has a fixed identity; source launchers use the environment.
    value = json.loads(marker.read_text(encoding="utf-8"))["edition"] if marker.is_file() else os.environ.get("MAHJONG_ADVISOR_EDITION", "")
    if value not in ("", "browser", "steam"):
        raise ValueError("MAHJONG_ADVISOR_EDITION must be browser or steam")
    return value


def default_input_source() -> str:
    return launch_edition() or "browser"
