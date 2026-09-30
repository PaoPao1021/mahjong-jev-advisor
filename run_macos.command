#!/bin/zsh
# Finder double-click launcher; run_macos.sh owns environment checks.
cd "$(dirname "$0")" || exit 1
zsh ./run_macos.sh "$@"
result=$?
if (( result != 0 )); then
  printf '\n启动失败，按回车关闭窗口。\n'
  read -r
fi
exit "$result"
