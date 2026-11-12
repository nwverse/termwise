"""判定生词 + 渲染输出。原行原样输出，释义作为注脚另起一行，不打乱对齐。"""

import json
import unicodedata
from dataclasses import dataclass

from .lexicon import Entry, Lexicon
from .store import UserStore
from .textproc import Word, extract_words

# 难度档位 -> 词频排名阈值（排名大于阈值算生词）。大致对应 Relingo 的 CEFR level 概念。
LEVELS = {"basic": 3000, "intermediate": 6000, "advanced": 10000, "expert": 20000}

BASIC_TAGS = {"zk", "gk", "cet4"}

DIM, UL, RESET, CYAN = "\x1b[2m", "\x1b[4m", "\x1b[0m", "\x1b[36m"


def parse_level(s: str) -> int:
    if s in LEVELS:
        return LEVELS[s]
    if s.isdigit():
        return int(s)
    raise ValueError(f"无效的难度 {s!r}，可选 {'|'.join(LEVELS)} 或数字")


@dataclass
class Hit:
    word: Word
    entry: Entry
    starred: bool


class Annotator:
    def __init__(self, lexicon: Lexicon, store: UserStore, threshold: int, once: bool = True):
        self.lex = lexicon
        self.store = store
        self.threshold = threshold
        self.once = once
        self.seen: set[str] = set()

    def is_hard(self, w: Word, e: Entry) -> tuple[bool, bool]:
        forms = (w.norm, e.word)
        if self.store.is_star(*forms):
            return True, True
        if self.store.is_known(*forms):
            return False, False
        if e.hard is not None:
            return e.hard, e.starred
        if e.rank == 0:
            # 未收录词频：带中考 / 高考 / 四级标签的是基础词，其余多为生僻词或术语
            return not any(t in BASIC_TAGS for t in e.tags.split()), False
        return e.rank > self.threshold, False

    def prefetch(self, plains: list[str]):
        """一批行的词一次查完（Relingo 模式下即一次请求），之后逐行 hits() 只读缓存。"""
        self.lex.lookup({w.norm for p in plains for w in extract_words(p)})

    def hits(self, plain: str) -> list[Hit]:
        words = extract_words(plain)
        entries = self.lex.lookup({w.norm for w in words})
        out, local = [], set()
        for w in words:
            e = entries.get(w.norm)
            # 逐行去重同样受 once 控制：--all 时同一行的每次出现都要标注
            if not e or (self.once and e.word in local):
                continue
            hard, starred = self.is_hard(w, e)
            if not hard:
                continue
            local.add(e.word)
            if self.once and e.word in self.seen:
                continue
            self.seen.add(e.word)
            out.append(Hit(w, e, starred))
        return out


def _underline(plain: str, hits: list[Hit]) -> str:
    out, pos = [], 0
    for h in sorted(hits, key=lambda h: h.word.start):
        out.append(plain[pos:h.word.start])
        out.append(f"{UL}{plain[h.word.start:h.word.end]}{RESET}")
        pos = h.word.end
    out.append(plain[pos:])
    return "".join(out)


def note(h: Hit, color: bool, phonetic: bool) -> str:
    e = h.entry
    head = e.word if e.word == h.word.norm else f"{h.word.norm}→{e.word}"
    ph = f" /{e.phonetic}/" if phonetic and e.phonetic else ""
    star = "★" if h.starred else ""
    if color:
        return f"{CYAN}{star}{head}{RESET}{DIM}{ph} {e.translation}{RESET}"
    return f"{star}{head}{ph} {e.translation}"


def render_line(raw: str, hits: list[Hit], color: bool, phonetic: bool) -> str:
    """raw 不含行尾换行。原行没有 ANSI 时才在原文上加下划线，否则保持原样。"""
    if not hits:
        return raw
    line = raw
    if color and "\x1b" not in raw:
        line = _underline(raw, hits)
    sep = f"{DIM} · {RESET}" if color else " · "
    notes = sep.join(note(h, color, phonetic) for h in hits)
    arrow = f"{DIM}  ↳ {RESET}" if color else "  ↳ "
    return f"{line}\n{arrow}{notes}"


def display_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def hit_json(h: Hit) -> str:
    e = h.entry
    return json.dumps(
        {"word": h.word.text, "lemma": e.word, "phonetic": e.phonetic,
         "translation": e.translation, "rank": e.rank, "starred": h.starred},
        ensure_ascii=False,
    )
