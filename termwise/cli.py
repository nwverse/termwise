"""tw —— Relingo code mode：在终端 / Agent 回复里标出生词并给释义。

  cmd | tw                  逐行透传，有生词的行下方加一行释义
  tw --list < file          只输出去重后的生词表
  tw screen [--scrollback]  读当前 Ghostty 终端的屏幕 / scrollback 并列出生词
  tw clip                   对剪贴板内容列出生词
  tw lookup WORD...         查词
  tw agent [--name N]       给 Agent 回复用：去掉代码块后列生词，并记入历史
  tw history [-f]           查看 / 跟随 Agent 回复的生词历史（适合放在分屏里）
  tw codex-hook             Codex Stop hook 入口
  tw auth login [--apple|--email A]  浏览器授权登录 Relingo（token 存 macOS 钥匙串）
  tw config init|show|path  配置文件 ~/.config/termwise/config.toml
  tw cache clear            清空 Relingo 结果缓存
  tw known|star|forget W..  个人词库（本地，登录后同步到 Relingo 账号）
  tw import-relingo F.csv   导入 Relingo 导出的 CSV
  tw build-db ecdict.csv    构建本地词库
"""

import argparse
import json
import os
import re
import select
import subprocess
import sys
import time

from . import annotate as A
from . import config as C
from .lexicon import EcdictLexicon, build_db
from .store import UserStore, data_dir, db_path
from .textproc import strip_ansi

COMMANDS = {"annotate", "screen", "clip", "lookup", "agent", "history", "codex-hook", "auth",
            "config", "cache", "known", "star", "forget", "import-relingo", "build-db"}

FENCE_RE = re.compile(r"^\s*(```|~~~).*?^\s*\1[^\n]*$", re.S | re.M)


def _add_view_opts(p: argparse.ArgumentParser):
    p.add_argument("-l", "--level", default=None,
                   help="难度 basic|intermediate|advanced|expert（默认取配置，缺省 intermediate）；"
                        "本地模式下也可写词频排名阈值")
    p.add_argument("-s", "--source", choices=["relingo", "local"], default=None,
                   help="词库来源（默认取配置，缺省 relingo）")
    p.add_argument("-a", "--all", action="store_true", help="同一个词每次出现都注释（默认只注释首次）")
    p.add_argument("-p", "--phonetic", action="store_true", help="显示音标")
    p.add_argument("--color", choices=["auto", "always", "never"], default="auto")


def _color(args) -> bool:
    if args.color == "auto":
        return sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    return args.color == "always"


def _local_lexicon():
    return EcdictLexicon(db_path()) if os.path.exists(db_path()) else None


def _lexicon(cfg: dict, source: str, level: str):
    # -s 有 choices 校验，TW_SOURCE / config.toml 也要同样拒绝非法值，
    # 否则拼写错误会被静默当成 local，绕过 Relingo 判定
    if source not in ("relingo", "local"):
        raise ValueError(f"无效的来源 {source!r}，可选 relingo|local")
    local = _local_lexicon()
    if source == "relingo":
        from .relingo import RelingoLexicon, get_token
        if level not in C.RELINGO_LEVELS:
            raise ValueError(f"Relingo 模式下难度只能是 {'|'.join(C.RELINGO_LEVELS)}")
        return RelingoLexicon(cfg["relingo"], C.RELINGO_LEVELS[level], data_dir(), local, get_token())
    if not local:
        raise FileNotFoundError(f"找不到词库 {db_path()}，先运行 scripts/fetch-ecdict.sh 构建")
    return local


def _annotator(args) -> A.Annotator:
    cfg = C.load()
    source = getattr(args, "source", None) or os.environ.get("TW_SOURCE") or cfg["source"]
    level = getattr(args, "level", None) or os.environ.get("TW_LEVEL") or cfg["level"]
    lex = _lexicon(cfg, source, level)
    return A.Annotator(lex, UserStore(devwords=cfg["devwords"]), A.parse_level(level),
                       once=not getattr(args, "all", False))


def _print_list(hits: list[A.Hit], color: bool, phonetic: bool):
    if not hits:
        print("（没有生词）")
        return
    width = max(A.display_width(h.entry.word) for h in hits)
    for h in hits:
        e = h.entry
        star = "★" if h.starred else " "
        head = e.word.ljust(width)
        ph = f"/{e.phonetic}/ " if phonetic and e.phonetic else ""
        if color:
            print(f"{star}{A.CYAN}{head}{A.RESET}  {A.DIM}{ph}{A.RESET}{e.translation}")
        else:
            print(f"{star}{head}  {ph}{e.translation}")


def _collect(ann: A.Annotator, text: str) -> list[A.Hit]:
    ann.once = True
    lines = [strip_ansi(l) for l in text.splitlines()]
    ann.prefetch(lines)
    return [h for l in lines for h in ann.hits(l)]


def _list_text(text: str, args):
    _print_list(_collect(_annotator(args), text), _color(args), args.phonetic)


def _batches(stream, idle: float = 0.15, max_wait: float = 1.0, max_lines: int = 200):
    """把输入切成小批：攒到停顿 idle 秒、或最多 max_wait 秒 / max_lines 行就交出一批。
    这样 Relingo 模式下一批只发一次请求，同时 tail -f 之类的流仍然接近实时。"""
    if stream is not sys.stdin:
        batch = []
        for raw in iter(stream.readline, ""):
            batch.append(raw)
            if len(batch) >= max_lines:
                yield batch
                batch = []
        if batch:
            yield batch
        return
    fd, buf, batch, first = stream.fileno(), b"", [], None
    while True:
        timeout = None if not batch else max(0.0, min(idle, first + max_wait - time.time()))
        ready, _, _ = select.select([fd], [], [], timeout)
        if ready:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for l in lines:
                batch.append(l.decode("utf-8", "replace") + "\n")
            if batch and first is None:
                first = time.time()
            if len(batch) < max_lines and (first is None or time.time() - first < max_wait):
                continue
        if batch:
            yield batch
            batch, first = [], None
    if buf:
        batch.append(buf.decode("utf-8", "replace"))
    if batch:
        yield batch


def _read_all(files) -> str:
    """--list 把多个输入拼成一段文本。文件末尾没有换行时补一个，
    否则前一个文件的末词会和下一个文件的首词合并成一个不存在的词。"""
    parts = []
    for f in files:
        t = sys.stdin.read() if f == "-" else open(f, encoding="utf-8", errors="replace").read()
        if t and not t.endswith("\n"):
            t += "\n"
        parts.append(t)
    return "".join(parts)


def cmd_annotate(args):
    ann = _annotator(args)
    color = _color(args)
    files = args.files or ["-"]
    if args.list:
        return _print_list(_collect(ann, _read_all(files)), color, args.phonetic)
    sys.stdout.reconfigure(line_buffering=True)
    dangling = False  # 上一次输出是否停在行中（原行末尾无换行且没有注脚）
    for f in files:
        if dangling:  # 前一个文件末行无换行时补一个，免得和下一个文件首行粘连
            sys.stdout.write("\n")
            dangling = False
        stream = sys.stdin if f == "-" else open(f, encoding="utf-8", errors="replace")
        for batch in _batches(stream):
            bodies = [raw.rstrip("\n") for raw in batch]
            ann.prefetch([strip_ansi(b) for b in bodies])
            for raw, body in zip(batch, bodies):
                hits = ann.hits(strip_ansi(body))
                if args.json:
                    for h in hits:
                        print(A.hit_json(h))
                    continue
                # 有注脚时注脚行总以换行结尾；只有原样透传的行才保留"原行无换行就不补"
                end = "\n" if hits or raw.endswith("\n") else ""
                sys.stdout.write(A.render_line(body, hits, color, args.phonetic) + end)
                dangling = not end and bool(body)


def cmd_screen(args):
    from .ghostty import capture
    target = "scrollback" if args.scrollback else "selection" if args.selection else "screen"
    _list_text(capture(target), args)


def cmd_clip(args):
    _list_text(subprocess.run(["pbpaste"], capture_output=True, text=True).stdout, args)


def cmd_lookup(args):
    local = _local_lexicon()
    ann = _annotator(args)
    store = ann.store
    for w in args.words:
        n = w.lower()
        e = local.lookup([n]).get(n) if local else None
        is_remote = not isinstance(ann.lex, EcdictLexicon)
        remote = ann.lex.lookup([n]).get(n) if is_remote else None
        if not e and not remote:
            print(f"{w}: 未收录")
            continue
        base = remote or e
        state = "★生词本" if store.is_star(n, base.word) else "已掌握" if store.is_known(n, base.word) else ""
        if is_remote:
            src = (f"Relingo：{'★' if remote.starred else ''}生词 {remote.translation}" if remote and remote.hard
                   else "Relingo：不是生词（低于级别或已掌握）")
            print(f"{base.word} /{base.phonetic}/  {src} {state}".rstrip())
        if e:
            rank = e.rank or "未收录"
            print(f"  本地：{e.translation}  [词频 {rank}] {e.tags}".rstrip())


# ---------- Agent 集成（Claude Code mod / Codex hook 共用） ----------

def history_path() -> str:
    return os.path.join(data_dir(), "agent-words.jsonl")


def agent_words(text: str, agent: str, args) -> list[dict]:
    """去掉 fenced 代码块后取生词，并把结果追加到历史文件。"""
    prose = FENCE_RE.sub("", text)
    hits = _collect(_annotator(args), prose)
    words = [json.loads(A.hit_json(h)) for h in hits]
    if words:
        os.makedirs(data_dir(), exist_ok=True)
        with open(history_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "agent": agent, "words": words}, ensure_ascii=False) + "\n")
    return words


def _one_line(words: list[dict], limit: int = 12) -> str:
    parts = [f"{'★' if w['starred'] else ''}{w['lemma']} {w['translation']}" for w in words[:limit]]
    more = f" …另有 {len(words) - limit} 个" if len(words) > limit else ""
    return "生词：" + " · ".join(parts) + more


def cmd_agent(args):
    words = agent_words(sys.stdin.read(), args.name, args)
    if args.format == "json":
        print(json.dumps({"words": words}, ensure_ascii=False))
    elif words:
        print(_one_line(words, limit=10_000))


def cmd_codex_hook(args):
    """Codex Stop hook：stdin 是 hook JSON，stdout 必须是 JSON 或空。任何错误都静默，不打断 Codex。"""
    try:
        payload = json.load(sys.stdin)
        text = payload.get("last_assistant_message") or ""
        words = agent_words(text, "codex", args) if text.strip() else []
        if words:
            print(json.dumps({"systemMessage": _one_line(words)}, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001
        print(f"tw codex-hook: {e}", file=sys.stderr)


def cmd_history(args):
    path = history_path()
    color = _color(args)

    def show(rec):
        t = time.strftime("%H:%M", time.localtime(rec["ts"]))
        head = f"── {t} {rec['agent']} ({len(rec['words'])})"
        print(f"{A.DIM}{head}{A.RESET}" if color else head)
        for w in rec["words"]:
            star = "★" if w["starred"] else " "
            print(f"{star}{A.CYAN}{w['lemma']}{A.RESET}  {w['translation']}" if color
                  else f"{star}{w['lemma']}  {w['translation']}")

    lines = open(path, encoding="utf-8").read().splitlines() if os.path.exists(path) else []
    for l in lines[-args.n:]:
        show(json.loads(l))
    if not args.follow:
        return
    sys.stdout.reconfigure(line_buffering=True)
    pos = os.path.getsize(path) if os.path.exists(path) else 0
    while True:
        time.sleep(0.5)
        if not os.path.exists(path) or os.path.getsize(path) <= pos:
            continue
        with open(path, encoding="utf-8") as f:
            f.seek(pos)
            new = f.read()
            pos = f.tell()
        for l in new.splitlines():
            if l.strip():
                show(json.loads(l))


# ---------- 账号 / 配置 / 缓存 ----------

def cmd_auth(args):
    from . import relingo as R
    if args.action == "logout":
        print("已删除 Relingo token" if R.delete_token() else "没有保存过 token")
        return
    cfg = C.load()
    lex = R.RelingoLexicon(cfg["relingo"], C.RELINGO_LEVELS["intermediate"], data_dir(), None, None)
    if args.action == "login":
        if args.paste:
            token = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout.strip()
            subprocess.run(["pbcopy"], input="", text=True)  # 清掉剪贴板里的 token
            if not 16 <= len(token) <= 4096:
                raise RuntimeError("剪贴板里没有像 token 的内容")
        else:
            if args.email:
                account = R.passcode_login(cfg["relingo"], args.email, lex.db)
            else:
                account = R.oauth_login(cfg["relingo"], "apple" if args.apple else "google", lex.db,
                                        open_browser=not args.no_browser)
            token = account.get("token") or ""
            if not token:
                raise RuntimeError("登录成功但返回里没有 token 字段")
        where = R.save_token(token, insecure_storage=args.insecure_storage)
        print(f"已登录，token 存在{where}")
    token = R.get_token()
    if not token:
        print("未登录：按 Relingo 级别匿名判词，不含你的已掌握 / 生词本。运行 tw auth login 登录")
        return
    lex.client.token = token
    try:
        info = lex.user_info()
    except R.RelingoError as e:
        raise RuntimeError(f"token 校验失败：{e}" + ("（token 无效或过期，重新运行 tw auth login）" if e.auth else "")) from None
    email = str(info.get("email") or "")
    masked = re.sub(r"(^.).*(@.*$)", r"\1***\2", email) if email else "（无邮箱字段）"
    print(f"Relingo 账号：{masked}（token 存在{R.token_storage()}）")


def cmd_config(args):
    if args.action == "init":
        print(f"配置文件：{C.init()}")
    elif args.action == "path":
        print(C.config_path())
    else:
        print(json.dumps(C.load(), ensure_ascii=False, indent=2))


def cmd_cache(args):
    from .relingo import RelingoLexicon
    cfg = C.load()
    lex = RelingoLexicon(cfg["relingo"], "", data_dir(), None, None)
    print(f"已清空 {lex.clear_cache()} 条缓存，退避状态已重置")


def cmd_mark(args):
    from . import relingo as R
    words = [w.strip().lower() for w in args.words if w.strip()]
    UserStore().mark(words, args.cmd)
    token = R.get_token()
    if not token:
        print(f"{args.cmd}: {' '.join(words)}（本地词库；未登录 Relingo，没有同步）")
        return
    cfg = C.load()
    lex = R.RelingoLexicon(cfg["relingo"], "", data_dir(), None, None)
    lex.client.token = token
    try:
        lex.sync_mark(words, args.cmd)
    except R.RelingoError as e:
        print(f"{args.cmd}: {' '.join(words)}（本地词库；Relingo 同步失败：{e}）")
        sys.exit(3)
    print(f"{args.cmd}: {' '.join(words)}（本地词库，已同步 Relingo）")


def cmd_import(args):
    k, s = UserStore().import_relingo_csv(args.csv, default=args.rest)
    print(f"导入完成：已掌握 {k} 个，生词本 {s} 个")


def cmd_build(args):
    n = build_db(args.csv, db_path())
    print(f"词库已构建：{db_path()}（{n} 个词条）")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help")):
        argv.insert(0, "annotate")

    ap = argparse.ArgumentParser(prog="tw", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("annotate", help="标注 stdin / 文件（默认命令）")
    p.add_argument("files", nargs="*")
    p.add_argument("--list", action="store_true", help="只输出去重后的生词表")
    p.add_argument("--json", action="store_true", help="每个生词输出一行 JSON（给其他工具集成）")
    _add_view_opts(p)
    p.set_defaults(fn=cmd_annotate)

    p = sub.add_parser("screen", help="列出当前 Ghostty 终端屏幕上的生词")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--scrollback", action="store_true")
    g.add_argument("--selection", action="store_true")
    _add_view_opts(p)
    p.set_defaults(fn=cmd_screen)

    p = sub.add_parser("clip", help="列出剪贴板文本里的生词")
    _add_view_opts(p)
    p.set_defaults(fn=cmd_clip)

    p = sub.add_parser("lookup", help="查词")
    p.add_argument("words", nargs="+")
    _add_view_opts(p)
    p.set_defaults(fn=cmd_lookup)

    p = sub.add_parser("agent", help="列出 Agent 回复（stdin）里的生词并记入历史")
    p.add_argument("--name", default="agent", help="记录在历史里的 Agent 名")
    p.add_argument("--format", choices=["json", "text"], default="text")
    _add_view_opts(p)
    p.set_defaults(fn=cmd_agent)

    p = sub.add_parser("codex-hook", help="Codex Stop hook 入口（stdin 为 hook JSON）")
    _add_view_opts(p)
    p.set_defaults(fn=cmd_codex_hook)

    p = sub.add_parser("history", help="Agent 回复生词历史")
    p.add_argument("-f", "--follow", action="store_true", help="持续显示新记录")
    p.add_argument("-n", type=int, default=5, help="先显示最近 n 条（默认 5）")
    p.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    p.set_defaults(fn=cmd_history)

    p = sub.add_parser("auth", help="登录 Relingo（默认浏览器里用 Google 授权）")
    p.add_argument("action", choices=["login", "status", "logout"])
    g = p.add_mutually_exclusive_group()
    g.add_argument("--apple", action="store_true", help="用 Apple 账号授权")
    g.add_argument("--email", metavar="ADDR", help="用邮箱验证码登录")
    g.add_argument("--paste", action="store_true", help="从剪贴板读取从扩展复制出的 token")
    p.add_argument("--no-browser", action="store_true", help="只打印授权链接，不自动打开浏览器")
    p.add_argument("--insecure-storage", action="store_true",
                   help="钥匙串不可用时把 token 存到 ~/.config/termwise/credentials（权限 600）")
    p.set_defaults(fn=cmd_auth)

    p = sub.add_parser("config", help="配置文件")
    p.add_argument("action", choices=["init", "show", "path"])
    p.set_defaults(fn=cmd_config)

    p = sub.add_parser("cache", help="Relingo 结果缓存")
    p.add_argument("action", choices=["clear"])
    p.set_defaults(fn=cmd_cache)

    for name, h in (("known", "标记为已掌握"), ("star", "加入生词本（总是高亮）"), ("forget", "从个人词库移除")):
        p = sub.add_parser(name, help=h + "（本地词库，登录后同步到 Relingo 账号）")
        p.add_argument("words", nargs="+")
        p.set_defaults(fn=cmd_mark)

    p = sub.add_parser("import-relingo", help="导入 Relingo 导出的 CSV（word,translation,phonetic,mastered,sentences）")
    p.add_argument("csv")
    p.add_argument("--rest", choices=["star", "known", "ignore"], default="star",
                   help="未标 mastered 的词放哪：star 生词本（默认，适合导出的是生词本）/ known / ignore（级别词书用这个）")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("build-db", help="由 ECDICT CSV 构建本地词库")
    p.add_argument("csv")
    p.set_defaults(fn=cmd_build)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        print(f"tw: {e}", file=sys.stderr)
        return 1
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
    return 0
