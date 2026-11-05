# termwise（Claude Code mod）

Claude 每次回复后，把回复里的英文生词和中文释义列出来：

- **侧边 pane「生词」**：最近 5 条回复的生词，宽终端下停靠在侧边。点 ✓ 把词标为已掌握，之后不再显示。
- **回复里的括号释义**：像 Relingo 浏览器插件一样，回复正文里生词第一次出现时后面加（释义），比如 meticulous（细致）。只改显示，存下来的回复和模型读到的内容不变；代码块、行内代码、链接不加。
- **提示栏上方一行**：最新回复的生词摘要。

生词判定和释义由 [tw](../../../README.md) 完成（Relingo 服务端，本地 ECDICT 兜底），代码块里的内容不参与判定。需要 `tw` 在 PATH 里，或者在 `/config` 里把 `rlcPath` 设为绝对路径。

命令：`/termwise`（打开 pane）、`/termwise close`、`/termwise band`（切换提示栏上方那一行）、`/termwise inline`（开关回复里的括号释义）、`/termwise star [词…]`（加入生词本）、`/termwise known [词…]`（标为已掌握）、`/termwise clear`。`star` / `known` 不带词时弹出选择框，列出最近一条回复的生词。

提示栏上方那一行末尾三个按钮：`+` 弹出选择框，把选中的词（或在 Other 里输入的词）用 `tw star` 加入生词本；`-` 同样弹框，用 `tw known` 标为已掌握，以后不再标注（登录 Relingo 后两者都会同步到账号）；`x` 隐藏这一行。
