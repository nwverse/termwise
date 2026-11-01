# relingo-words（Claude Code mod）

Claude 每次回复后，把回复里的英文生词和中文释义列出来：

- **侧边 pane「生词」**：最近 5 条回复的生词，宽终端下停靠在侧边。点 ✓ 把词标为已掌握，之后不再显示。
- **提示栏上方一行**：最新回复的生词摘要。

生词判定和释义由 [rlc](../../../README.md) 完成（Relingo 服务端，本地 ECDICT 兜底），代码块里的内容不参与判定。需要 `rlc` 在 PATH 里，或者在 `/config` 里把 `rlcPath` 设为绝对路径。

命令：`/relingo`（打开 pane）、`/relingo close`、`/relingo band`（切换提示栏上方那一行）、`/relingo clear`。
