#!/bin/zsh
set -e
trap 'echo "Startup failed. Check Python 3.12+, dependencies and macOS Screen Recording permission." >&2' ZERR
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  if command -v python3.12 >/dev/null; then
    python3.12 -m venv .venv
  else
    python3 -m venv .venv
  fi
fi
.venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)'
if ! .venv/bin/python -c 'import mahjong_jev_advisor, PySide6.QtWidgets, cv2, mss, rapidocr_onnxruntime, mahjong'; then
  .venv/bin/python -m pip install -e .
fi
.venv/bin/python -m pip check
if [[ "${1:-}" == "--check" ]]; then
  shift
  exec .venv/bin/python -m mahjong_jev_advisor.preflight "$@"
fi
exec .venv/bin/python -m mahjong_jev_advisor
