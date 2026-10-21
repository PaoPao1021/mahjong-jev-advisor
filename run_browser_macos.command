#!/bin/zsh
export MAHJONG_ADVISOR_EDITION=browser
exec zsh "$(dirname "$0")/run_macos.sh" "$@"
