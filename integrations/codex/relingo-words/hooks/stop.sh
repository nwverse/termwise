#!/bin/bash
# Codex Stop hook：把 hook JSON（stdin）交给 rlc codex-hook，
# 它输出 {"systemMessage": "生词：…"} 或什么都不输出。任何错误都不打断 Codex。
RLC="${RLC_BIN:-$HOME/.local/bin/rlc}"
[ -x "$RLC" ] || exit 0
exec "$RLC" codex-hook
