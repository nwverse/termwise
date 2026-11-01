"""rlc —— Relingo code mode 原型：在终端文本里标出生词并给释义。

  cmd | rlc                  逐行透传，有生词的行下方加一行释义
  rlc --list < file          只输出去重后的生词表
  rlc screen [--scrollback]  读当前 Ghostty 终端的屏幕 / scrollback 并列出生词
  rlc clip                   对剪贴板内容列出生词
  rlc lookup WORD...         查词
  rlc known|star|forget W..  维护个人词库
  rlc import-relingo F.csv   导入 Relingo 导出的生词本 CSV
  rlc build-db ecdict.csv    构建本地词库
"""

import argparse
import os
import subprocess
import sys

from . import annotate as A
from .lexicon import EcdictLexicon, build_db
from .store import UserStore, db_path
from .textproc import strip_ansi

COMMANDS = {"annotate", "screen", "clip", "lookup", "known", "star", "forget",
            "import-relingo", "build-db"}


def _add_view_opts(p: argparse.ArgumentParser):
    p.add_argument("-l", "--level", default=os.environ.get("RLC_LEVEL", "intermediate"),
                   help="难度：basic|intermediate|advanced|expert 或词频排名阈值（默认 intermediate=6000）")
    p.add_argument("-a", "--all", action="store_true", help="同一个词每次出现都注释（默认只注释首次）")
    p.add_argument("-p", "--phonetic", action="store_true", help="显示音标")
    p.add_argument("--color", choices=["auto", "always", "never"], default="auto")


def _color(args) -> bool:
    if args.color == "auto":
        return sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    return args.color == "always"


def _annotator(args) -> A.Annotator:
    lex = EcdictLexicon(db_path())
    return A.Annotator(lex, UserStore(), A.parse_level(args.level), once=not args.all)


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


def _list_text(text: str, args):
    ann = _annotator(args)
    ann.once = True
    hits = []
    for line in text.splitlines():
        hits.extend(ann.hits(strip_ansi(line)))
    _print_list(hits, _color(args), args.phonetic)


def cmd_annotate(args):
    ann = _annotator(args)
    color = _color(args)
    files = args.files or ["-"]
    if args.list:
        text = "".join(sys.stdin.read() if f == "-" else open(f, encoding="utf-8", errors="replace").read()
                       for f in files)
        return _list_text(text, args)
    sys.stdout.reconfigure(line_buffering=True)
    for f in files:
        stream = sys.stdin if f == "-" else open(f, encoding="utf-8", errors="replace")
        # 逐行读，tail -f / 长时间构建日志也能实时出结果
        for raw in iter(stream.readline, ""):
            body = raw.rstrip("\n")
            hits = ann.hits(strip_ansi(body))
            if args.json:
                for h in hits:
                    print(A.hit_json(h))
                continue
            sys.stdout.write(A.render_line(body, hits, color, args.phonetic) + ("\n" if raw.endswith("\n") else ""))


def cmd_screen(args):
    from .ghostty import capture
    target = "scrollback" if args.scrollback else "selection" if args.selection else "screen"
    _list_text(capture(target), args)


def cmd_clip(args):
    text = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
    _list_text(text, args)


def cmd_lookup(args):
    lex = EcdictLexicon(db_path())
    store = UserStore()
    for w in args.words:
        e = lex.lookup([w.lower()]).get(w.lower())
        if not e:
            print(f"{w}: 未收录")
            continue
        state = "★生词本" if store.is_star(w.lower(), e.word) else "已掌握" if store.is_known(w.lower(), e.word) else ""
        rank = e.rank or "未收录"
        print(f"{e.word} /{e.phonetic}/  {e.translation}  [词频 {rank}] {e.tags} {state}".rstrip())


def cmd_mark(args):
    UserStore().mark(args.words, args.cmd)
    print(f"{args.cmd}: {' '.join(args.words)}")


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

    ap = argparse.ArgumentParser(prog="rlc", description=__doc__,
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
    p.set_defaults(fn=cmd_lookup)

    for name, h in (("known", "标记为已掌握"), ("star", "加入生词本（总是高亮）"), ("forget", "从个人词库移除")):
        p = sub.add_parser(name, help=h)
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
        print(f"rlc: {e}", file=sys.stderr)
        return 1
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
    return 0
