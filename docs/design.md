# relingo-code：Relingo 的 code mode（终端 / Ghostty）设计

> 状态：调研 + 方案 + 最小原型（`bin/rlc`）。日期 2026-11-01。

## 0. 目标

在 Ghostty 终端里看到的英文（命令输出、报错、文档、代码注释、和 Claude Code 等 Agent 的对话）中识别生词、给出释义，并尽量和用户在 Relingo 里的「已掌握 / 生词本」保持一致。

## 1. 调研：Relingo 的能力从哪来

调研对象是本机安装的 Relingo 3.31.1（`dpphkcfmnbkdpmgneljgdhfnccnhmfig/3.31.1_0`），只读了 manifest 和打包后的 JS。没有读取扩展存储（`Local Extension Settings/…`）和账号数据，也没有调用任何接口。

### 1.1 结论

| 能力 | 来源 |
|---|---|
| 分词、词形还原、词性标注（英文） | **本地**。background.js 内嵌 wink-nlp 的英文模型（`contentToken` 消息，返回 `{text, lemma, pos}`）。满足某个账号条件（代码里是 `tt(account)`，推测是 Pro）或页面是中文 / 日文时，会改走服务端 `contentToken` |
| 判断哪些是生词（难度 / 已掌握） | **服务端**。`parseContent3` 一次提交整页的小写词表，服务端按用户的级别词书和已掌握词返回需要高亮的词 |
| 释义、音标 | **服务端**。`parseContent3` 返回结果里带释义；查单词时用 `lookupWord` / `lookupDict2` |
| 段落翻译 | 可选 Google 翻译（`translate.googleapis.com`，客户端直连）、其他第三方引擎，或 Relingo 自家的 `translateParagraph` |
| 用户词库（已掌握、生词本、自定义词书） | **服务端**。见 1.3 |

扩展包里**没有本地词库或难度数据文件**（只有一些单词形态表，供 wink-nlp 做词形还原用）。

### 1.2 接口形态

- Base：`https://api.relingo.net`。国内节点是 `https://cn.relingo.net`，请求失败时会自动切换到另一个节点。
- 调用方式统一是 `POST {base}/api/{fn}`，body 为 JSON，`credentials: "include"`（会带上 cookie）。
- 响应格式 `{code, message, data}`，`code === 0` 表示成功。
- 请求头：
  - `x-relingo-token`：用户 token，存在扩展的 `chrome.storage.local["persist:relingo-v2"].account.token`；
  - `x-relingo-version`：扩展版本号；
  - `x-relingo-platform: extension`；
  - `x-relingo-lang`、`x-relingo-dest-lang`；
  - `x-relingo-referrer`：当前页面 URL。
- token 失效时返回 `illegal user token`、`missing user token`、`invalid signature` 或 code 2001/2002，扩展收到后会触发重新登录。
- `externally_connectable` 允许 `*.relingo.net` 的网页向扩展请求 `getAccount`（官网仪表盘靠这个拿到账号）。
- 关键接口，从调用点反推：

```text
parseContent3  { words: string[](小写), to: "zh", level?: "relingo-Intermediate" | vocabulary?: string[](词书 id), scene?: "video" }
  -> { words: [{ word, translations: [{pos, target}], phonetic: {uk:[], us:[]},
                 mastered, strange, stared, count, modified }] }
  高亮规则（content.js X5）：!mastered && !(strange && !stared)
lookupWord     { word, from, to, scene }
lookupDict2    { words, to, definition }
getVocabulary  { id, type: "builtin"|... } -> { words, masteredWords }
getUserVocabulary { id, before, to, type, size } -> { items, totalCount, hasMore, nextCursor }
submitVocabulary  { id, type: "mastered"|"strange", words }   // 标记已掌握 / 加入生词本
removeVocabularyWords / removeStarWord
exportCSVRows  { id, to } -> [{ word, translation, phonetic, mastered, sentences }]
translateParagraph, aiMeaning, getUserConfig, getUserInfo ...
```

- 接口一共约 60 个，名字带版本后缀（`parseContent3`、`lookupDict2`、`exportAnkiDeck2`），说明还在持续迭代。

### 1.3 用户词库能不能拿到

- **能，而且有不碰接口的合规办法**：Relingo 选项页的词书支持「导出 CSV」（对应 `exportCSVRows`），列为 `word,translation,phonetic,mastered,sentences`，mastered 列为 ✓/✗。另外还能导出 Anki 格式。
- 导入时要选对词书：导出的是生词本，未掌握的词进 star，这是默认行为；导出的是级别词书或内置词书，要用 `--rest ignore`，否则满屏高亮。
- 已掌握词和生词本在服务端是**两本不同的词书**（代码里的 `masterID` 和 `currentBookID`）。可能要分别导出，具体以选项页 UI 为准，这一点没有替你点开验证。
- 通过接口拿（`getVocabulary` / `getUserVocabulary`）需要用户 token。

### 1.4 服务条款

`https://relingo.net/en/terms` 是一份通用模板（已用 curl 拉原文 grep 过），**没有任何关于 API、逆向、自动化访问、第三方客户端的条款**，既没有允许也没有禁止。最相关的一条是 License 段落里的 "Reproduce, duplicate or copy material from Relingo"。由于没有明确授权：

- 调用非公开接口属于灰色地带。用户自己的账号、自用、低频访问，风险主要是被封号或接口失效，法律风险较低，但无法排除。
- 把 Relingo 的释义数据缓存、再分发属于 "copy material"，应避免。

## 2. 调研：Ghostty 1.3.1 的扩展点

本机 `/Applications/Ghostty.app` 1.3.1，配置文件 `~/Library/Application Support/com.mitchellh.ghostty/config.ghostty`（目前为空）。

| 扩展点 | 能做什么 | 不能做什么 |
|---|---|---|
| 管道 / shell | `cmd \| rlc`、`cmd 2>&1 \| rlc`、`tail -f log \| rlc` | 不能对已经输出过的内容生效 |
| keybind 内置 action | `write_screen_file` / `write_scrollback_file` / `write_selection_file` 的 `copy\|paste\|open` + `plain` 格式，可以把屏幕、scrollback 或选区写成纯文本临时文件；`text:` 往终端输入任意文本；`toggle_quick_terminal`；`new_split` | **keybind 不能直接执行外部命令** |
| 默认快捷键 | `⌘⇧J` 是 `write_screen_file:paste,plain`（把屏幕文本文件路径粘到命令行）；`⌘⌃⇧J` 是 copy；`⌘⌥⇧J` 是 open | |
| quick terminal | 下拉式终端（`toggle_quick_terminal`，可设 `global:` 全局快捷键），适合放一个常驻的查词 / 生词面板 | 只能运行默认 shell 或 `command` |
| AppleScript（1.3 新增，`Ghostty.sdef`） | 枚举 window / tab / terminal；`perform action "<action>" on terminal`；`input text`、`send key`；`split` / `new tab` 时可以带上 `command` 和 `environment variables` | **没有读取屏幕内容的属性**，只能间接通过 `perform action "write_screen_file:copy,plain"` 拿到；需要 macOS「自动化」授权 |
| macOS Services | Ghostty 只提供 "New Ghostty Tab/Window Here" | 没有文本类服务 |
| 插件 / API | 无 | |

实测记录：
- `osascript -e 'tell application "Ghostty" to get version'` 可以正常返回。
- `perform action` 第一次调用时一直挂起，原因是在等 macOS 的「自动化」授权确认。原型因此加了 15 秒超时和授权提示。授权通过后是否可用**还没有验证**。
- 当前这个 Claude Code 会话跑在 herdr 里（`TERM_PROGRAM=herdr`），不是直接跑在 Ghostty 里。

## 3. 可选架构

所有方案都共用同一个前端，即 code mode 的文本管线：

```
输入（管道 / 屏幕文件 / 剪贴板 / Agent transcript）
  → 去 ANSI/OSC → 屏蔽 URL、路径、flag、hash、版本号、行内代码 → 拆 camelCase / snake_case 标识符
  → Lexicon.lookup(词形) → 难度判定（个人词库 > 开发者白名单 > 词频阈值）
  → 渲染：原行原样输出 + 下方一行释义注脚 / 去重后的生词表 / JSONL
```

各方案的区别只在 `Lexicon` 的实现，也就是词典、难度、个人词库从哪来，以及句子翻译怎么做。

### 方案 A：直接调用 Relingo 服务端

用用户的 `x-relingo-token` 调 `parseContent3` 判断生词、拿释义，调 `submitVocabulary` 把终端里新标的词回写到 Relingo。

- **优点**：和浏览器里的 Relingo 判定结果完全一致，在终端里标记的已掌握 / 生词本也能同步回去。释义质量也和 Relingo 一致。
- **风险**：
  1. 非公开接口，条款没有授权，可能导致封号或接口突然失效；
  2. `x-relingo-version` 允许服务端按版本拒绝请求，接口名带版本后缀会持续升级，`credentials: include` 说明鉴权可能还依赖 cookie。这几点都意味着维护成本不可控；
  3. token 怎么拿：让用户手动粘贴可以接受；从 Chrome 的 LevelDB 里读**不推荐**，那等于去读浏览器的私有数据；
  4. 每次标注都要联网，终端输出量大，请求频率可能触发风控；终端内容（可能包含内部代码、日志）会被发到第三方服务器，`x-relingo-referrer` 也会暴露上下文；
  5. 可能占用 Pro 额度。

### 方案 B：本地词库 + 可选 LLM，只同步 Relingo 的个人词库（推荐）

- 词典与难度：本地 [ECDICT](https://github.com/skywind3000/ECDICT)（MIT 协议，77 万词条，提供中文释义、COCA/BNC 词频、考试标签和 exchange 词形）。构建成 sqlite 后约 37MB，查询在毫秒级。
- 个人词库：本地 `known.txt` / `star.txt`。用 Relingo 选项页导出的 CSV 导入（`rlc import-relingo`），**不调任何接口**；也可以在终端里直接维护（`rlc known/star`）。
- 句子 / 段落翻译、上下文释义（可选）：调用 LLM，按需触发，不默认翻译所有输出。
- **优点**：离线、快、隐私友好（终端内容不出本机，除非主动调用 LLM），没有条款风险，也不依赖 Relingo 接口的稳定性。
- **缺点**：
  1. 难度判定和 Relingo 不完全一致。Relingo 用 CEFR 级别词书，这里用词频排名阈值近似；
  2. 同步是手动单向的，从 Relingo 导到本地；在终端里标记的词不会回写到 Relingo；
  3. ECDICT 释义偏传统词典风格，需要做 code mode 的义项挑选。

### 方案 C（A + B 混合，后续可选）

默认走方案 B。用户显式开启后，加一个可插拔的 `RelingoLexicon` adapter，只做两件低频的事：定时（比如每天一次）拉取个人词库（`getUserVocabulary`），以及可选地把终端里新加入生词本的词回写。实时标注仍然完全在本地完成，把对非公开接口的依赖压到最小。

## 4. 推荐

**采用方案 B**，代码按方案 C 预留 adapter 接口（`rlc/lexicon.py` 里的 `Lexicon` Protocol）。是否启用 Relingo 接口由用户决定，见第 7 节。

Ghostty 集成从轻到重分四档：

1. **管道**：`cmd 2>&1 | rlc`。零配置，可以流式处理。
2. **事后分析屏幕**：输入 `rlc --list `，按 Ghostty 默认的 `⌘⇧J`（`write_screen_file:paste,plain`）把屏幕文本文件路径粘进来，再回车。不需要授权，也不碰剪贴板。可以加一个 keybind 省掉手动输入：

   ```ini
   # 只负责输入 "rlc --list "（\x20 是结尾空格，Zig 字符串语法），之后仍要按 ⌘⇧J 和回车。
   # 已通过 ghostty +validate-config 校验，还没在 Ghostty 里实际按过
   keybind = super+shift+l=text:rlc --list\x20
   ```
3. **quick terminal 当查词面板**：在选区上按 ⌘C，然后呼出 quick terminal 运行 `rlc clip`。（以上 2–4 档都还没在 Ghostty 里实测，见第 2 节的实测记录）

   ```ini
   keybind = global:super+grave_accent=toggle_quick_terminal
   quick-terminal-position = right
   ```
4. **AppleScript**：`rlc screen [--scrollback|--selection]` 从当前终端直接取文本。需要授权，并且会临时占用剪贴板（只能还原纯文本）。后续可以用 Hammerspoon 或快捷指令绑定全局热键，通过 AppleScript `split … with configuration {command: "rlc screen; read"}` 开一个分屏显示结果。

## 5. MVP 范围（本次原型已实现）

`bin/rlc`，Python 3 标准库实现，无第三方依赖：

- 默认模式 `cmd | rlc`：逐行读取，实时处理；原行原样输出，有生词时在下方加一行 `↳ 词 释义 · …`；同一个生词默认只注释第一次（`-a` 关闭去重）；只在 TTY 下上色，并且只有原行不含 ANSI 时才给生词加下划线。
- `--list`：输出去重后的生词表。`--json`：每个生词输出一行 JSON，方便其他工具集成。
- `rlc screen`、`rlc clip`、`rlc lookup`、`rlc known/star/forget`、`rlc import-relingo`、`rlc build-db`。
- code mode 处理：
  - 去掉 ANSI/OSC 转义；
  - 跳过 URL、路径、文件名、flag、hash、版本号、`$VAR`、行内代码、单引号数据、缩写形式；
  - 全大写短词视为缩写，跳过；
  - 拆分 camelCase、snake_case、kebab-case；
  - 内置开发者常用词白名单（`rlc/devwords.txt`）；
  - 释义优先附上 `[计]` 义项（如 latency：潜伏｜[计] 传输延迟）。
- 难度判定：
  - 优先级：生词本 > 已掌握或白名单 > 词频阈值；
  - 档位：`basic=3000`、`intermediate=6000`（默认）、`advanced=10000`、`expert=20000`，也可以直接写数字；
  - 屈折变化按原形算（deprecated → deprecate）；
  - 前缀派生词按词根算（unused 看 used 的词频）；
  - 没有词频的词：带 zk/gk/cet4 标签的视为基础词，其余视为生词。

## 6. 里程碑

| 阶段 | 内容 |
|---|---|
| M0（完成） | 调研、设计文档、`rlc` CLI 原型 |
| M1 | 调整默认档位和白名单，减少噪音；`rlc` 打包安装（`pipx` 或单文件）；决定 `rlc screen` 是否值得保留；在 Ghostty 里实测 keybind 片段 |
| M2 | 常驻 TUI 面板（quick terminal 或分屏）：显示最近的生词，按键标记已掌握或加入生词本，展示例句；Hammerspoon / 快捷指令一键开分屏 |
| M3 | 接入 Claude Code：通过 hook（比如 Stop hook 读取 transcript）把 Agent 回复中的生词汇总到面板。动手前要先核实 hook 的输入字段 |
| M4 | 可选 LLM：选中一句话后给出翻译和上下文释义，结果缓存 |
| M5（可选，需要授权） | `RelingoLexicon` adapter：低频同步个人词库，可选回写 |

## 7. 待决策

1. 是否接受方案 B 作为主线？是否授权做方案 A 或 C 的 Relingo 接口同步？如果授权，token 由你手动提供（推荐），而不是从 Chrome 存储里读。
2. LLM：句子翻译要不要做，用哪家模型？默认不发送任何终端内容。
3. 默认难度档位（目前 intermediate，即词频排名 6000）。
4. 是否允许我修改你的 Ghostty 配置（目前为空），还是只在文档里给出配置片段。
5. 词库和个人数据放在哪：目前放在仓库的 `data/`（已 gitignore），可以用 `RLC_DATA` 修改；也可以改成 `~/.local/share/rlc`。
