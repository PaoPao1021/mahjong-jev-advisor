"""Locate the unpacked browser extension in source and portable releases."""
from pathlib import Path
import sys


def extension_dir() -> Path:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        if sys.platform == "darwin" and executable.parent.name == "MacOS":
            return executable.parents[3] / "browser-extension"
        return executable.parent / "browser-extension"
    return Path(__file__).resolve().parents[2] / "browser-extension"
