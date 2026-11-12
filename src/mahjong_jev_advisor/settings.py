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

from .edition import default_input_source, launch_edition


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
    return base / ("mahjong-jev-advisor-steam" if launch_edition() == "steam" else "mahjong-jev-advisor")


@dataclass
class Settings:
    input_source: str = field(default_factory=default_input_source)
    steam_capture_mode: str = "process"
    steam_process_name: str = "Jantama_MahjongSoul"
    steam_proxy_port: int = 10999
    steam_upstream_proxy: str = ""
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
    always_on_top: bool = True
    window_geometry: tuple[int, int, int, int] | None = None
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
            input_source=data.get("input_source", default_input_source()),
            steam_capture_mode=str(data.get("steam_capture_mode", "process")),
            steam_process_name=str(data.get("steam_process_name", "Jantama_MahjongSoul")).strip() or "Jantama_MahjongSoul",
            steam_proxy_port=max(1024, min(65535, int(data.get("steam_proxy_port", 10999)))),
            steam_upstream_proxy=str(data.get("steam_upstream_proxy", "")).strip(),
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
            always_on_top=data.get("always_on_top", True),
            window_geometry=tuple(data["window_geometry"]) if data.get("window_geometry") else None,
            show_table=bool(data.get("show_table", True)),
            opacity=max(0.45, min(1.0, float(data.get("opacity", 1.0)))) if data.get("ui_revision") == 2 else 1.0,
        )

    @staticmethod
    def _validate_data(data: Any) -> None:
        if not isinstance(data, dict):
            raise ValueError("Configuration must be an object")
        if data.get("input_source", "browser") not in {"browser", "steam"}:
            raise ValueError("Invalid input source")
        if data.get("steam_capture_mode", "process") not in {"process", "proxy"}:
            raise ValueError("Invalid steam capture mode")
        for key in (
            "gateway_api_key",
            "model_endpoint",
            "model_name",
            "model_protocol",
            "llm_base_url",
            "llm_api_key",
            "llm_model",
            "steam_process_name",
            "steam_upstream_proxy",
        ):
            if key in data and not isinstance(data[key], str):
                raise ValueError("Invalid text field")
        urlsplit(data.get("model_endpoint", Settings.model_endpoint))
        for key in ("local_fallback", "logging_enabled", "show_table", "always_on_top"):
            if key in data and type(data[key]) is not bool:
                raise ValueError("Invalid boolean field")
        for key in ("model_timeout", "ui_interval_ms", "opacity", "steam_proxy_port"):
            if key in data and (type(data[key]) not in (float, int) or not math.isfinite(data[key])):
                raise ValueError("Invalid numeric field")
        if "steam_proxy_port" in data and (
            type(data["steam_proxy_port"]) is not int or not (1 <= data["steam_proxy_port"] <= 65535)
        ):
            raise ValueError("Invalid steam proxy port")
        geometry = data.get("window_geometry")
        if geometry is not None and (not isinstance(geometry, (list, tuple)) or len(geometry) != 4
                or any(type(v) is not int or abs(v) > 100000 for v in geometry)
                or geometry[2] <= 0 or geometry[3] <= 0):
            raise ValueError("Invalid window geometry")

    def save(self) -> None:
        directory = config_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "settings.json"
        data = {
            "input_source": self.input_source,
            "steam_capture_mode": self.steam_capture_mode,
            "steam_process_name": self.steam_process_name,
            "steam_proxy_port": self.steam_proxy_port,
            "steam_upstream_proxy": self.steam_upstream_proxy,
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
            "always_on_top": self.always_on_top,
            "window_geometry": self.window_geometry,
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

    def switch_input_source(self, source: str) -> None:
        if source not in {"browser", "steam"}:
            raise ValueError("Invalid input source")
        self.input_source = source

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
