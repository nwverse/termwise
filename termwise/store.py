"""用户词库：已掌握 / 生词本，纯文本存储（每行一个小写词）。

数据可以来自：
- tw known / tw star 手动维护；
- tw import-relingo 导入 Relingo 选项页「导出 CSV」得到的文件（不调用 Relingo 接口）。
"""

import csv
import os

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
DEV_WORDS = os.path.join(PKG_DIR, "devwords.txt")


def data_dir() -> str:
    return os.environ.get("TW_DATA") or os.path.join(os.path.dirname(PKG_DIR), "data")


def db_path() -> str:
    return os.path.join(data_dir(), "ecdict.db")


def _read_list(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        return {l.strip().lower() for l in f if l.strip() and not l.startswith("#")}


class UserStore:
    def __init__(self, root: str | None = None, devwords: bool = True):
        self.root = os.path.join(root or data_dir(), "user")
        self.known_path = os.path.join(self.root, "known.txt")
        self.star_path = os.path.join(self.root, "star.txt")
        self.known = _read_list(self.known_path)
        self.star = _read_list(self.star_path)
        self.dev = _read_list(DEV_WORDS) if devwords else set()

    def is_known(self, *forms: str) -> bool:
        return any(f in self.known or f in self.dev for f in forms if f) and not self.is_star(*forms)

    def is_star(self, *forms: str) -> bool:
        return any(f in self.star for f in forms if f)

    def save(self):
        os.makedirs(self.root, exist_ok=True)
        for path, words in ((self.known_path, self.known), (self.star_path, self.star)):
            with open(path, "w", encoding="utf-8") as f:
                f.write("".join(w + "\n" for w in sorted(words)))

    def mark(self, words: list[str], kind: str):
        words = [w.strip().lower() for w in words if w.strip()]
        if kind == "known":
            self.known.update(words)
            self.star.difference_update(words)
        elif kind == "star":
            self.star.update(words)
            self.known.difference_update(words)
        elif kind == "forget":
            self.known.difference_update(words)
            self.star.difference_update(words)
        self.save()

    def import_relingo_csv(self, path: str, default: str = "star") -> tuple[int, int]:
        """Relingo 导出的 CSV 列为 word,translation,phonetic,mastered,sentences，
        mastered 列为 ✓ / ✗。✓ 记为已掌握，其余按 default：star 进生词本，known 记为已掌握，ignore 不导入。"""
        known, star = [], []
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                w = (row.get("word") or "").strip().lower()
                if not w:
                    continue
                if (row.get("mastered") or "").strip() in ("✓", "true", "1", "yes"):
                    known.append(w)
                elif default == "star":
                    star.append(w)
                elif default == "known":
                    known.append(w)
        self.known.update(known)
        self.star.difference_update(known)
        self.star.update(star)
        self.known.difference_update(star)
        self.save()
        return len(known), len(star)
