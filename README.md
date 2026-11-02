# termwise

Relingo 的「code mode」：在终端（Ghostty）和 Agent 对话（Claude Code、Codex）里标出英文生词、给出中文释义。生词判定和释义来自 Relingo 服务端，与你的 Relingo 级别 / 已掌握词保持一致；离线或接口失败时用本地 ECDICT 兜底。设计和调研见 [docs/design.md](docs/design.md)。

## 安装

```sh
scripts/fetch-ecdict.sh                  # 下载 ECDICT（MIT）并构建本地兜底词库 data/ecdict.db（约 37MB）
ln -s "$PWD/bin/tw" ~/.local/bin/tw    # 放进 PATH
tw config init                          # 生成 ~/.config/termwise/config.toml（难度、来源、缓存等）
```

只依赖 Python 3.11+ 标准库。

## 登录 Relingo（一次即可）

```sh
tw auth login            # 打开浏览器用 Google 账号授权，终端自动拿到 token
tw auth login --apple    # 用 Apple 账号
tw auth login --email you@example.com   # 用邮箱验证码
tw auth status           # 查看登录状态（只显示打码后的邮箱）
tw auth logout
```

登录走的是和 Relingo 扩展相同的流程：浏览器里完成授权，终端轮询取回账号。token 存在 macOS 钥匙串（service `termwise-relingo`），不会打印出来。如果当前环境访问不了钥匙串，可以加 `--insecure-storage`，改存到 `~/.config/termwise/credentials`，文件权限 600。

不登录也能用：会按 Relingo 级别判断生词，但不会计入你的已掌握词。

## 用法

```sh
cargo build 2>&1 | tw                   # 逐行透传，有生词的行下方加 ↳ 释义
tw --list < README.md                   # 只列出去重后的生词表
tw -l advanced -p < notes.txt           # 难度 basic|intermediate|advanced|expert，-p 显示音标
tw -s local < notes.txt                 # 只用本地词库（不联网）
tw lookup idempotent                    # 同时显示 Relingo 判定和本地释义
tw clip                                 # 分析剪贴板
tw history -f                           # 持续显示 Claude Code / Codex 回复里的生词
tw cache clear                          # 在 Relingo 里改了已掌握词后立即生效
tw known mitigate; tw star ephemeral   # 本地个人词库，叠加在 Relingo 结果之上
python3 -m unittest discover -s tests -t .
```

Ghostty 快捷键（已写入 `~/Library/Application Support/com.mitchellh.ghostty/config.ghostty`）：

| 快捷键 | 动作 |
|---|---|
| ⌘⇧L | 在 shell 提示符下填好 `tw --list <屏幕文本文件>`，按回车执行 |
| ⌘⌥⇧L | 同上，范围是整个 scrollback |
| ⌘⌃L | 在右侧开分屏，运行 `tw history -f` |

改完配置要按 ⌘⇧, 重新加载。这三个快捷键都还没实际按过，见 design.md §4.2。原配置备份在同一目录的 `config.ghostty.bak-20261101`。

## Agent 集成

- **Claude Code**：`integrations/claude-code/termwise` 是一个 mod，每次回复后在侧边 pane「生词」和提示栏上方一行列出生词。命令：`/termwise [open|close|band|clear]`。
- **Codex（ChatGPT 桌面 app）**：`integrations/codex/termwise` 是一个 app 插件。每次回复后显示一行「生词：…」，也可以直接在对话里让它查词或标记已掌握。安装步骤：
  1. 重启 ChatGPT app，在 Codex 里打开本仓库（termwise）这个项目；
  2. 打开插件目录，选择「termwise (local)」这个 marketplace，安装「Relingo 生词」；
  3. 按提示审核并信任插件的 hook，插件里的 hook 不信任就不会运行；
  4. 新建一个对话开始使用。修改插件后要重新安装或重启 app 才会生效。

数据（词库、缓存、个人词库、Agent 生词历史）默认放在仓库的 `data/`（已 gitignore），可以用 `TW_DATA` 修改；配置文件位置可以用 `TW_CONFIG` 修改。
