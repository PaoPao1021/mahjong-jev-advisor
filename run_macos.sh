#!/bin/zsh
set -e
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  if command -v python3.12 >/dev/null; then
    python3.12 -m venv .venv
  else
    python3 -m venv .venv
  fi
fi
if [[ ! -x .venv/bin/mahjong-jev-advisor ]]; then
  .venv/bin/python -m pip install -e .
fi
exec .venv/bin/mahjong-jev-advisor
