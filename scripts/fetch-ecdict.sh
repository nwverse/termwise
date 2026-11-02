#!/usr/bin/env bash
# 下载 ECDICT（MIT 协议，https://github.com/skywind3000/ECDICT）并构建本地 sqlite 词库。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${TW_DATA:-$ROOT/data}"
mkdir -p "$DATA/ecdict"
if [ ! -s "$DATA/ecdict/ecdict.csv" ]; then
  curl -sSfL -o "$DATA/ecdict/ecdict.csv" \
    https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv
fi
"$ROOT/bin/tw" build-db "$DATA/ecdict/ecdict.csv"
