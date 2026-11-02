# termwise：Relingo 的 code mode（终端 / Ghostty）设计

> 状态：M0–M2 已完成，主线已切换为方案 A（Relingo 服务端），本地 ECDICT 兜底。日期 2026-11-01。

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

`https://termwise.net/en/terms` 是一份通用模板（已用 curl 拉原文 grep 过），**没有任何关于 API、逆向、自动化访问、第三方客户端的条款**，既没有允许也没有禁止。最相关的一条是 License 段落里的 "Reproduce, duplicate or copy material from Relingo"。由于没有明确授权：

- 调用非公开接口属于灰色地带。用户自己的账号、自用、低频访问，风险主要是被封号或接口失效，法律风险较低，但无法排除。
- 把 Relingo 的释义数据缓存、再分发属于 "copy material"，应避免。

## 2. 调研：Ghostty 1.3.1 的扩展点

本机 `/Applications/Ghostty.app` 1.3.1，配置文件 `~/Library/Application Support/com.mitchellh.ghostty/config.ghostty`（目前为空）。

| 扩展点 | 能做什么 | 不能做什么 |
|---|---|---|
| 管道 / shell | `cmd \| tw`、`cmd 2>&1 \| tw`、`tail -f log \| tw` | 不能对已经输出过的内容生效 |
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

### 方案 B：本地词库 + 可选 LLM，只同步 Relingo 的个人词库（最初推荐；现在作为兜底）

- 词典与难度：本地 [ECDICT](https://github.com/skywind3000/ECDICT)（MIT 协议，77 万词条，提供中文释义、COCA/BNC 词频、考试标签和 exchange 词形）。构建成 sqlite 后约 37MB，查询在毫秒级。
- 个人词库：本地 `known.txt` / `star.txt`。用 Relingo 选项页导出的 CSV 导入（`tw import-relingo`），**不调任何接口**；也可以在终端里直接维护（`tw known/star`）。
- 句子 / 段落翻译、上下文释义（可选）：调用 LLM，按需触发，不默认翻译所有输出。
- **优点**：离线、快、隐私友好（终端内容不出本机，除非主动调用 LLM），没有条款风险，也不依赖 Relingo 接口的稳定性。
- **缺点**：
  1. 难度判定和 Relingo 不完全一致。Relingo 用 CEFR 级别词书，这里用词频排名阈值近似；
  2. 同步是手动单向的，从 Relingo 导到本地；在终端里标记的词不会回写到 Relingo；
  3. ECDICT 释义偏传统词典风格，需要做 code mode 的义项挑选。

### 方案 C（A + B 混合，后续可选）

默认走方案 B。用户显式开启后，加一个可插拔的 `RelingoLexicon` adapter，只做两件低频的事：定时（比如每天一次）拉取个人词库（`getUserVocabulary`），以及可选地把终端里新加入生词本的词回写。实时标注仍然完全在本地完成，把对非公开接口的依赖压到最小。

## 4. 决策与当前方案（2026-11-01 更新）

用户决定：**主线走方案 A**，由 Relingo 服务端判生词、给释义，和浏览器里的级别、已掌握保持一致；本地 ECDICT 只在离线或接口失败时兜底。只做单词释义，不做整句翻译，不接 LLM。难度做成配置项，默认中级。

### 4.1 Relingo 接入（`termwise/termwise.py`）

- **请求**：`POST /api/parseContent3`，body 为 `{words, to: "zh", level}`，只含**去重后的小写词表**。不发送行文本、文件路径和 `x-relingo-referrer`。
  - 默认只发本地词典里查得到的词（`only_dictionary_words`），项目代号、内部标识符这类词不会发出去。
  - 请求头带 `x-relingo-version`（读本机已装扩展的版本号）、`x-relingo-platform: extension`，有 token 时再带 `x-relingo-token`。
  - User-Agent 写明 `termwise/0.2 (terminal client)`。Python 默认的 UA 会被 Cloudflare 拦截（error 1010）。
- **实测（2026-11-01）**：
  - 不带 token 也能调通，服务端按级别只返回生词（`the` 不返回），字段有 `word, lemma, phonetic{us,uk}, translations[{pos,target}], wordFrequency`。
  - `vocabularyTest` 返回四个级别：`relingo-Beginner / Intermediate / Advanced / Proficient`，分别对应配置里的 `basic / intermediate / advanced / expert`。
  - **带 token 的请求还没验证**：要等用户执行 `tw auth login`。预期返回结果会多出 `mastered / strange / stared` 字段，tw 按扩展的规则判定是否高亮：`!mastered && !(strange && !stared)`。
- **token**：存在 macOS 钥匙串（service `termwise-relingo`）。
  - 写入时经 `security -i` 的 stdin 传进去，不出现在进程参数里。
  - 不落文件、不打印；`tw auth status` 只显示打码后的邮箱。
  - 登录方式：`tw auth login` 走和扩展相同的 OAuth 设备流。
    - 先调 `oauthGoogleUrl`（或 `oauthAppleUrl`）拿到 `{url, id}`，在浏览器里打开 `url` 完成授权；再每 2 秒用 `id` 调一次 `oauthGoogleProfile`，返回里出现 `email` 就说明拿到了带 token 的账号，最多轮询 120 次。
    - 备选是邮箱验证码登录：`sendPasscode({email, type: "LOGIN"})`，再 `login({type: "PASSCODE", email, code})`。
    - 2026-11-01 实测：`oauthGoogleUrl` 返回 `{id, url}`，`url` 指向 `accounts.google.com/o/oauth2/v2/auth`；授权完成前，`oauthGoogleProfile` 返回空对象。授权完成后的返回还没实测，因为要你本人完成 Google 授权。
    - 钥匙串不可用时（比如这个开发会话，`security` 返回 36 "interaction not allowed"），可以显式加 `--insecure-storage`，改存到 `~/.config/termwise/credentials`，权限 600。
  - token 失效（`illegal user token` 或 code 2001/2002）时提示一次，然后改用未登录模式。
  - **钥匙串读写还没验证**：在开发用的沙盒会话里，`security` 报 "User interaction is not allowed"，写不进去；在用户自己的终端里应该能正常写入。用 `security` 写入的条目，再用 `security` 读取时不会弹授权框，hook 进程里读 token 不会卡住。
- **解释器**：从 GUI 启动的程序（ChatGPT.app 里的 Codex、桌面端 Claude Code）PATH 很精简，`python3` 会指向系统自带的 3.9。`bin/tw` 会自己找 3.11+ 的解释器（`TW_PYTHON` 优先，然后是 Homebrew）。已用 `env -i PATH=/usr/bin:/bin` 模拟这种环境验证过。Claude Code mod 调用 tw 时也会把 `~/.local/bin` 和 Homebrew 补进 PATH。
- **缓存**：`data/termwise-cache.db`，按「级别 + 是否登录」分开存。
  - 「不是生词」的结果也缓存，默认有效 12 小时（`cache_ttl_hours`）。在浏览器里新标的已掌握词，最多 12 小时同步到终端；`tw cache clear` 可以立即生效。
- **限频**：同一批行只发一次请求。管道模式按停顿约 150ms，或最多 1 秒、200 行，攒成一批。两次请求至少间隔 2 秒，这个限制跨进程生效。
- **退避**：遇到网络错误、HTTP 5xx 或 429，退避 30 秒，每次翻倍，最长 30 分钟，状态跨进程共享。退避期间完全不发请求，直接用 ECDICT，并提示一次。
- **补充**：
  - 服务端没返回、本地又判定为计算机术语的词（本地无词频、无考试标签、只有 `[计]` 义项，比如 idempotent），用本地释义补上。Relingo 词库本身不收录这类词，补上它们不会和你的已掌握词冲突（`local_jargon`）。
  - 本地的 `known / star` 和开发者白名单叠加在 Relingo 结果之上。
- **配置**：`~/.config/termwise/config.toml`（`tw config init` 生成），可配 `source`（relingo/local）、`level`、`devwords` 和 `[relingo]` 下的各项。

### 4.2 Ghostty 集成（已写入用户配置）

原配置文件为空，备份在 `config.ghostty.bak-20261101`。新增的快捷键已用 `ghostty +validate-config` 校验通过：

| 快捷键 | 动作 |
|---|---|
| ⌘⇧L | 依次执行 `text:tw --list ` 和 `write_screen_file:paste,plain`，在 shell 提示符处填好命令，**由你按回车执行**。故意不自动回车：焦点在 Claude Code / Codex 输入框里时误按，不会把它当消息发给 Agent |
| ⌘⌥⇧L | 同上，范围是整个 scrollback |
| ⌘⌃L | 执行 `new_split:right` 后再执行 `text:tw history -f\r`，持续显示 Claude Code / Codex 回复里的生词 |

**这三个快捷键都还没在 Ghostty 里实际按过**，只通过了 `+validate-config`。特别是 ⌘⌃L：`new_split` 之后焦点会不会转到新分屏，决定了 `text:` 输入到哪个分屏，这一点还没确认。配置改完后要按 ⌘⇧,（reload_config）或重启 Ghostty 才会生效。

`tw` 通过 `~/.local/bin/tw` 符号链接放进了 PATH。管道用法 `cmd 2>&1 | tw` 不变。`tw screen`（AppleScript）仍是可选功能，没有绑定快捷键。

## 5. Agent 集成：Claude Code 与 Codex

两者共用 tw 核心：

- `tw agent --name <agent> --format json|text`：从 stdin 读回复，去掉 fenced 代码块后列出生词，结果追加到 `data/agent-words.jsonl`。
- `tw history [-f]`：读取这份历史，适合放在 Ghostty 分屏里看，两个 Agent 共用。

### 5.1 Claude Code mod：`integrations/claude-code/termwise`

基于 Claude Code 的 function-hooks 插件：`.claude-plugin/plugin.json` 加上 `hooks/register.tsx`。

- **触发**：`turn.complete` 事件里的 `e.answer` 就是主循环本轮的最终回复，跳过子 agent、空回复和被中断的回复。
  - 在 hook 返回之后才异步执行 `$.process.run(['/usr/bin/env', tw, 'agent', ...], { stdin: answer })`，不阻塞对话。
- **展示**：
  - **侧边 pane「生词」**：列出最近 5 条回复的生词。会话开始时自动打开，终端宽度不小于 144 列时停靠在侧边。也可以用 `/termwise` 打开、`/termwise close` 关闭。每个词前有一个 ✓ 按钮，点了会执行 `tw known <词>`，之后不再显示这个词。
  - **提示栏上方一行**：最新回复的生词摘要，按宽度截断。窄终端下 pane 停靠不了，这一行能保证回复后总能看到生词。`/termwise band` 切换显示，点 × 隐藏。
- **状态**：存在 `$.state`（`turns`、`known`、`isBandHidden`），热重载后保留。
- **配置项**（`userConfig`）：`rlcPath`（默认 `tw`，经 `/usr/bin/env` 从 PATH 查找）、`autoOpenPane`。
- **验证**：`claude plugin validate` 通过，`tsc` 类型检查通过，`claude plugin test` 2 个用例通过（回复 → 调 tw → pane 和提示栏渲染；忽略子 agent、空回复、中断）。
- **加载**：开发期放在本会话的 dev-mods 目录，通过符号链接指向仓库。需要用户在 Claude Code 弹出的「Turn on the mods…」里选择开启。
  - 长期使用的话，可以在仓库加 `.claude-plugin/marketplace.json`，再用 `claude plugin marketplace add <repo>` 和 `claude plugin install termwise` 安装。

### 5.2 Codex（ChatGPT 桌面 app 插件）：`integrations/codex/termwise`

用户要求做成 ChatGPT app 里 Codex 的**插件**，不用裸 hook，也不走命令行。按官方打包文档（developers.openai.com/plugins/build/plugins），并对照 ChatGPT.app 自带插件的实际文件来写。

**插件内容**
- `.codex-plugin/plugin.json`：名称、说明，以及 `interface`（app 插件目录里显示的名字、描述和示例提示），`skills: "./skills/"`。
- `hooks/hooks.json`：放在插件根目录，属于默认发现路径，不需要在清单里声明。
  - 注册一个 `Stop` hook，命令是 `/bin/bash "${PLUGIN_ROOT}/hooks/stop.sh"`。`stop.sh` 再调用 `~/.local/bin/tw codex-hook`。
  - hook 读 `last_assistant_message`，输出 `{"systemMessage": "生词：…"}`，同时写进共享历史，供 Ghostty 里 ⌘⌃L 打开的分屏显示。
  - 插件里的 hooks 只有**手动安装的桌面 app 插件**才支持，而且这类插件进不了公开插件目录。这和我们的用途相符。
- `skills/termwise/SKILL.md`：你在对话里说「查一下 X」「列出上一条回复的生词」「把 X 标为已掌握」时，Codex 会调用 `tw lookup / history / known / star`。

**marketplace**：放在仓库里的 `.agents/plugins/marketplace.json`，格式和 ChatGPT.app 自带的 `openai-bundled` marketplace 一致（`source: {source: "local", path}`、`policy`、`category`）。

**已验证**
- 用临时 `CODEX_HOME` 跑 `codex plugin marketplace add` 和 `plugin add`，能识别 marketplace，插件能完整安装到 `plugins/cache/termwise/termwise/0.1.0/`。你的 `~/.codex` 没有动。
- 在精简 PATH 下模拟 `PLUGIN_ROOT` 运行 hook 命令，输出正确的 `systemMessage`。

**未验证**
- 在 app 里的实际安装流程。
- hook 信任提示出现在哪里。
- `systemMessage` 在 app 界面里怎么显示（文档只说会作为警告显示在界面或事件流里）。
- hook 在 app 的沙盒里能不能访问 Relingo（访问不了时会回退本地词库）。

**为什么不选其他机制**
- `notify`：已经被 ChatGPT 的 Computer Use 助手占用，而且显示不了内容。
- 只做 MCP 或 skill：只有模型主动调用才会运行，做不到「每次回复后自动出现」。
- `.app.json`：引用的是 ChatGPT 服务端的 connector，本地做不了。

**注意**：app 安装的是插件的副本（缓存目录），修改插件后要重新安装或重启 app 才会生效。tw 本身不在插件里，修改 tw 立即生效。

## 6. 范围与里程碑

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0 | 调研、设计文档、`tw` CLI 原型（本地词库） | 完成 |
| M1 | Relingo 服务端接入：钥匙串 token、缓存、限频、退避、本地兜底；配置文件；Ghostty 快捷键 | 完成（带 token 的路径待用户登录后验证） |
| M2 | Claude Code mod（pane + 提示栏上方一行）、Codex app 插件（Stop hook + skill）、共享历史分屏 | 完成（Codex 插件待在 app 里安装） |
| M3 | 回写 Relingo：在 pane 或终端里标的已掌握 / 生词本同步回 Relingo（`submitVocabulary`，需要先拿到 masterID / currentBookID） | 待决定 |
| M4 | `tw` 打包安装；mod 发布为本地 marketplace；按实际使用调整白名单和 `local_jargon` 规则 | 待做 |

## 7. 待决策

1. 执行 `tw auth login` 登录，然后用 `tw auth status` 验证。登录后我再确认带 token 的返回字段（`mastered` 等），以及是否需要 cookie。
2. 是否把 pane 里的 ✓（已掌握）回写到 Relingo（M3）？目前只写本地词库。
3. 在 ChatGPT app 里安装 Codex 插件，并信任它的 hook（步骤见 README）。
4. 缓存有效期默认 12 小时是否合适（越短越及时，请求也越多）。
