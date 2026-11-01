# relingo-code

Relingo 的「code mode」原型：在终端（Ghostty）里给英文输出标生词、给释义。设计和调研见 [docs/design.md](docs/design.md)。

## 安装

```sh
scripts/fetch-ecdict.sh               # 下载 ECDICT（MIT）并构建 data/ecdict.db（约 37MB）
ln -s "$PWD/bin/rlc" ~/.local/bin/rlc # 可选：放到 PATH 里
```

只依赖 Python 3 标准库。

## 用法

```sh
cargo build 2>&1 | rlc                 # 逐行透传，有生词的行下方加 ↳ 释义
rlc --list < README.md                 # 只列出去重后的生词表
rlc -l advanced -p < notes.txt         # 难度 basic|intermediate|advanced|expert|数字，-p 显示音标
rlc --list <⌘⇧J>                       # 在 Ghostty 里按 ⌘⇧J 粘入屏幕文本文件路径（未实测）
rlc clip                               # 分析剪贴板
rlc screen [--scrollback|--selection]  # 通过 AppleScript 读当前 Ghostty 终端（需要自动化授权，未实测）
rlc lookup idempotent
rlc known mitigate; rlc star ephemeral # 个人词库
rlc import-relingo relingo-xxxx.csv    # 导入 Relingo 选项页导出的 CSV（非生词本请加 --rest ignore）
python3 -m unittest discover -s tests -t .
```

数据目录默认是仓库的 `data/`（已 gitignore），可以用 `RLC_DATA` 修改。
