"""Per-user configuration; never stored in the repository."""

from __future__ import annotations

import json
import os
import sys
import math
import tempfile
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def config_dir() -> Path:
    override = os.environ.get("MAHJONG_ADVISOR_CONFIG_DIR")
    if override:
        return Path(override).expanduser().resolve()
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
    model_endpoint: str = "https://ai-gateway.vercel.sh/v4/ai/evaluation-model"
    model_name: str = "typesafe-ai/jev-latest"
    model_protocol: str = "vercel"
    model_timeout: float = 15.0
    local_fallback: bool = False
    logging_enabled: bool = False
    ui_interval_ms: int = 250
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    show_table: bool = True
    opacity: float = 1.0
    load_warning: str = ""

    @classmethod
    def load(cls) -> "Settings":
        path = config_dir() / "settings.json"
        if not path.exists():
            return cls(api_key=os.environ.get("AI_GATEWAY_API_KEY", ""))
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            cls._validate_data(data)
        except (ValueError, TypeError, OSError):
            return cls(load_warning="本机配置格式无效，已使用默认值；原文件保留，重新保存时将备份。")

        protocol = data.get("model_protocol", "vercel")
        endpoint = data.get("model_endpoint", cls.model_endpoint)
        env_name = {
            ("vercel", "https://ai-gateway.vercel.sh"): "AI_GATEWAY_API_KEY",
            ("typesafe", "https://api.typesafe.ai"): "TYPESAFE_API_KEY",
            ("openrouter", "https://openrouter.ai"): "OPENROUTER_API_KEY",
        }.get((protocol, f"{urlsplit(endpoint).scheme}://{urlsplit(endpoint).netloc}"))

        return cls(
            game_box=tuple(data["game_box"]) if data.get("game_box") else None,
            regions={k: tuple(v) for k, v in data.get("regions", {}).items()},
            # Old api_key entries were TypeSafe credentials and must not be sent to Vercel.
            api_key=data.get("gateway_api_key", "") or (os.environ.get(env_name, "") if env_name else ""),
            model_endpoint=str(data.get("model_endpoint", cls.model_endpoint)),
            model_name=str(data.get("model_name", cls.model_name)),
            model_protocol=str(data.get("model_protocol", "vercel")),
            model_timeout=max(0.5, min(120, float(data.get("model_timeout", 15.0)))),
            local_fallback=bool(data.get("local_fallback", False)),
            logging_enabled=bool(data.get("logging_enabled", False)),
            ui_interval_ms=max(150, min(10000, int(data.get("ui_interval_ms", 250)))),
            llm_base_url=str(data.get("llm_base_url", "")),
            llm_api_key=str(data.get("llm_api_key", "")),
            llm_model=str(data.get("llm_model", "deepseek-chat")),
            show_table=bool(data.get("show_table", True)),
            opacity=max(0.45, min(1.0, float(data.get("opacity", 1.0)))) if data.get("ui_revision") == 2 else 1.0,
        )

    @staticmethod
    def _validate_data(data: Any) -> None:
        if not isinstance(data, dict):
            raise ValueError("Configuration must be an object")
        for key in ("gateway_api_key", "model_endpoint", "model_name", "model_protocol", "llm_base_url", "llm_api_key", "llm_model"):
            if key in data and not isinstance(data[key], str):
                raise ValueError("Invalid text field")
        urlsplit(data.get("model_endpoint", Settings.model_endpoint))
        for key in ("local_fallback", "logging_enabled", "show_table"):
            if key in data and type(data[key]) is not bool:
                raise ValueError("Invalid boolean field")
        for key in ("model_timeout", "ui_interval_ms", "opacity"):
            if key in data and (type(data[key]) not in (float, int) or not math.isfinite(data[key])):
                raise ValueError("Invalid numeric field")
        box = data.get("game_box")
        if box is not None and (not isinstance(box, (list, tuple)) or len(box) != 4
                or any(type(v) is not int for v in box) or box[2] <= 0 or box[3] <= 0):
            raise ValueError("Invalid game box")
        regions = data.get("regions", {})
        if not isinstance(regions, dict):
            raise ValueError("Invalid regions")
        for rect in regions.values():
            if (not isinstance(rect, (list, tuple)) or len(rect) != 4
                    or any(type(v) not in (float, int) or not math.isfinite(v) for v in rect)
                    or rect[0] < 0 or rect[1] < 0 or rect[2] <= 0 or rect[3] <= 0
                    or rect[0] + rect[2] > 1.001 or rect[1] + rect[3] > 1.001):
                raise ValueError("Invalid region coordinates")

    def save(self) -> None:
        directory = config_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "settings.json"
        data = {
            "game_box": self.game_box,
            "regions": self.regions,
            "gateway_api_key": self.api_key,
            "model_endpoint": self.model_endpoint,
            "model_name": self.model_name,
            "model_protocol": self.model_protocol,
            "model_timeout": self.model_timeout,
            "local_fallback": self.local_fallback,
            "logging_enabled": self.logging_enabled,
            "ui_interval_ms": self.ui_interval_ms,
            "llm_base_url": self.llm_base_url,
            "llm_api_key": self.llm_api_key,
            "llm_model": self.llm_model,
            "show_table": self.show_table,
            "opacity": self.opacity,
            "ui_revision": 2,
        }
        self._validate_data(data)
        if self.load_warning and path.exists():
            import shutil
            backup = directory / "settings.invalid.json"
            if not backup.exists():
                shutil.copy2(path, backup)
                if os.name != "nt":
                    backup.chmod(0o600)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                             prefix="settings-", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            if os.name != "nt":
                temporary.chmod(0o600)
            os.replace(temporary, path)
            self.load_warning = ""
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def connection(self):
        from .jev import ModelConnection
        return ModelConnection(self.model_endpoint, self.model_name, self.model_protocol,
                               self.model_timeout, self.local_fallback)


def append_decision_log(record: dict[str, Any]) -> None:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "decisions.jsonl"
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    if os.name != "nt":
        path.chmod(0o600)
