#!/usr/bin/env bash
# 安装 rlc 的 Codex Stop hook（用户级 ~/.codex/hooks.json）。已有该文件时不覆盖。
# 装好后需在 Codex 里运行 /hooks 审核并信任这个 hook，之后它才会运行。
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)/hooks.json"
DST="$HOME/.codex/hooks.json"
if [ -e "$DST" ]; then
  echo "已存在 $DST，未覆盖。请把 $SRC 里的 Stop 条目手动合并进去。" >&2
  exit 1
fi
mkdir -p "$HOME/.codex"
cp "$SRC" "$DST"
echo "已安装 $DST。在 Codex 中运行 /hooks 信任它。"
