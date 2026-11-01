"""词库层：Lexicon 接口 + 基于 ECDICT 的本地实现。

Relingo 服务端实现见 relingo.py；上层的分词 / 渲染只认 Lexicon 接口。
"""

import csv
import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Iterable, Optional, Protocol

# 去掉后词根仍成词、意思基本可推的前缀
_PREFIXES = ("non", "un", "re", "dis", "mis", "in", "im")

# ECDICT exchange 字段里表示「本词的变形」的类型码：
# p 过去式, d 过去分词, i 现在分词, 3 第三人称单数, s 复数, r 比较级, t 最高级
_FORM_CODES = set("pdi3srt")


@dataclass
class Entry:
    word: str           # 词典里的词头（原形）
    phonetic: str
    translation: str    # 已挑选、截短后的一行释义
    rank: int           # 有效词频排名，0 表示未收录（通常是生僻词 / 术语）；
                        # 前缀派生词取词根排名（unused 按 used 算）
    tags: str
    # 由词库直接给出的判定：None 表示按 rank 和难度阈值判断（本地词库）；
    # Relingo 返回的词为 True（服务端已按级别和个人已掌握词过滤）
    hard: Optional[bool] = None
    starred: bool = False
    source: str = "local"


class Lexicon(Protocol):
    def lookup(self, words: Iterable[str]) -> dict[str, Entry]:
        """输入小写词形，返回 词形 -> Entry（查不到的词不返回）。"""
        ...


def _short(sense: str, n: int = 3) -> str:
    """一行释义只保留前 n 个义项。"""
    parts = re.split(r"[,，;；]\s*", sense)
    return "，".join(p for p in parts[:n] if p)


def _pick_translation(raw: str, code_mode: bool = True, width: int = 40) -> str:
    """ECDICT 的释义按词性分行（字面量 \\n）。code mode 下把 [计] 义项附在通用义后面。"""
    lines = [l.strip() for l in raw.replace("\\n", "\n").splitlines() if l.strip()]
    lines = [l for l in lines if not l.startswith("[网络]")] or lines
    general = [l for l in lines if not l.startswith("[")]
    # 多词性时各取前两个义项（如 invalid：n. 病人；a. 有病的，无效的）
    chosen = "；".join(_short(l, 2 if len(general) > 1 else 3) for l in general[:2]) if general \
        else (lines[0] if lines else "")
    if code_mode:
        tech = next((l for l in lines if l.startswith("[计]")), None)
        if tech and tech not in chosen:
            chosen = f"{chosen}｜{_short(tech, 2)}" if general else tech
    chosen = re.sub(r"\s+", " ", chosen)
    return chosen if len(chosen) <= width else chosen[: width - 1] + "…"


def _rank(bnc: str, frq: str) -> int:
    vals = [int(v) for v in (bnc, frq) if v and v.isdigit() and int(v) > 0]
    return min(vals) if vals else 0


def build_db(csv_path: str, db_path: str) -> int:
    """把 ECDICT CSV 构建成只含单词（不含词组）的 sqlite 库，外加词形 -> 原形表。"""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    tmp = db_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.executescript(
        """
        CREATE TABLE words (word TEXT PRIMARY KEY, phonetic TEXT, translation TEXT,
                            rank INTEGER, tags TEXT);
        CREATE TABLE lemmas (form TEXT PRIMARY KEY, lemma TEXT);
        """
    )
    n = 0
    word_re = re.compile(r"^[a-z][a-z'-]*$")
    with open(csv_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            w = row["word"].strip().lower()
            if not word_re.match(w) or not row["translation"].strip():
                continue
            db.execute(
                "INSERT OR IGNORE INTO words VALUES (?,?,?,?,?)",
                (w, row["phonetic"], row["translation"], _rank(row["bnc"], row["frq"]), row["tag"]),
            )
            n += 1
            for part in row["exchange"].split("/"):
                code, _, val = part.partition(":")
                val = val.strip().lower()
                if not val or not word_re.match(val):
                    continue
                if code == "0" and val != w:          # 本词是 val 的变形
                    db.execute("INSERT OR IGNORE INTO lemmas VALUES (?,?)", (w, val))
                elif code in _FORM_CODES and val != w:  # val 是本词的变形
                    db.execute("INSERT OR IGNORE INTO lemmas VALUES (?,?)", (val, w))
    db.commit()
    db.close()
    os.replace(tmp, db_path)
    return n


class EcdictLexicon:
    def __init__(self, db_path: str, code_mode: bool = True):
        if not os.path.exists(db_path):
            raise FileNotFoundError(
                f"找不到词库 {db_path}，先运行 scripts/fetch-ecdict.sh 构建"
            )
        self.db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.code_mode = code_mode
        self._cache: dict[str, Optional[Entry]] = {}

    def lemma(self, form: str) -> str:
        row = self.db.execute("SELECT lemma FROM lemmas WHERE form=?", (form,)).fetchone()
        return row[0] if row else form

    def _get(self, w: str):
        return self.db.execute(
            "SELECT word, phonetic, translation, rank, tags FROM words WHERE word=?", (w,)
        ).fetchone()

    def _derived_rank(self, word: str, rank: int) -> int:
        for p in _PREFIXES:
            root = word[len(p):]
            if word.startswith(p) and len(root) >= 4:
                row = self._get(self.lemma(root)) or self._get(root)
                if row and row[3] and (not rank or row[3] < rank):
                    return row[3]
        return rank

    def _lookup_one(self, form: str) -> Optional[Entry]:
        if form in self._cache:
            return self._cache[form]
        base = self.lemma(form)
        row = self._get(base) or self._get(form)
        entry = None
        if row:
            word, phonetic, trans, rank, tags = row
            if not rank and base != form:
                # 原形没词频时退回词形本身的词频
                r2 = self._get(form)
                rank = r2[3] if r2 else 0
            rank = self._derived_rank(word, rank)
            entry = Entry(word, phonetic or "", _pick_translation(trans, self.code_mode), rank, tags or "")
        self._cache[form] = entry
        return entry

    def lookup(self, words: Iterable[str]) -> dict[str, Entry]:
        out = {}
        for w in words:
            e = self._lookup_one(w)
            if e:
                out[w] = e
        return out
