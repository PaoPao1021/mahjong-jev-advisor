"""Per-user configuration; never stored in the repository."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "mahjong-jev-advisor"


@dataclass
class Settings:
    game_box: tuple[int, int, int, int] | None = None
    regions: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)
    api_key: str = ""
    logging_enabled: bool = False
    ui_interval_ms: int = 250
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    show_table: bool = True
    opacity: float = 0.88

    @classmethod
    def load(cls) -> "Settings":
        path = config_dir() / "settings.json"
        if not path.exists():
            return cls(api_key=os.environ.get("TYPESAFE_API_KEY", ""))
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls(api_key=os.environ.get("TYPESAFE_API_KEY", ""))

        return cls(
            game_box=tuple(data["game_box"]) if data.get("game_box") else None,
            regions={k: tuple(v) for k, v in data.get("regions", {}).items()},
            api_key=os.environ.get("TYPESAFE_API_KEY") or data.get("api_key", ""),
            logging_enabled=bool(data.get("logging_enabled", False)),
            ui_interval_ms=max(150, int(data.get("ui_interval_ms", 250))),
            llm_base_url=str(data.get("llm_base_url", "")),
            llm_api_key=str(data.get("llm_api_key", "")),
            llm_model=str(data.get("llm_model", "deepseek-chat")),
            show_table=bool(data.get("show_table", True)),
            opacity=max(0.4, min(1.0, float(data.get("opacity", 0.88)))),
        )

    def save(self) -> None:
        directory = config_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "settings.json"
        data = {
            "game_box": self.game_box,
            "regions": self.regions,
            "api_key": self.api_key,
            "logging_enabled": self.logging_enabled,
            "ui_interval_ms": self.ui_interval_ms,
            "llm_base_url": self.llm_base_url,
            "llm_api_key": self.llm_api_key,
            "llm_model": self.llm_model,
            "show_table": self.show_table,
            "opacity": self.opacity,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)


def append_decision_log(record: dict[str, Any]) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "decisions.jsonl"
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    if os.name != "nt":
        path.chmod(0o600)
