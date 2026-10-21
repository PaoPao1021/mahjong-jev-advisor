#!/bin/zsh
export MAHJONG_ADVISOR_EDITION=steam
exec zsh "$(dirname "$0")/run_macos.sh" "$@"
