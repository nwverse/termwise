"""用户词库：已掌握 / 生词本，纯文本存储（每行一个小写词）。

数据可以来自：
- tw known / tw star 手动维护；
- tw import-relingo 导入 Relingo 选项页「导出 CSV」得到的文件（不调用 Relingo 接口）。
"""

import contextlib
import csv
import os
import shutil

try:
    import fcntl
except ImportError:  # Windows 没有 fcntl：仍然重读、原子替换，只是不加跨进程锁
    fcntl = None

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


def _write_list(path: str, words: set[str]):
    """先写临时文件再原子替换，读的一方不会看到写了一半的词表。"""
    # 词表可能是指向 dotfiles / 同步盘的符号链接：替换它指向的文件，链接本身留着。
    real = os.path.realpath(path)
    tmp = f"{real}.{os.getpid()}.tmp"  # 和真实文件同目录，os.replace 才是原子的
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("".join(w + "\n" for w in sorted(words)))
        if os.path.exists(real):
            shutil.copymode(real, tmp)  # 替换已有文件时保留它的权限
        os.replace(tmp, real)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


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

    @contextlib.contextmanager
    def _locked(self):
        """known / star 这对文件的跨进程写锁，关闭锁文件即释放。"""
        os.makedirs(self.root, exist_ok=True)
        with open(os.path.join(self.root, ".lock"), "a", encoding="utf-8") as f:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_EX)
            yield

    def _update(self, change):
        """在锁内重读最新词表，再应用本次变更。
        启动时读到的快照可能已被别的进程改过，把它整份写回去会撤销对方已保存的词。"""
        with self._locked():
            self.known = _read_list(self.known_path)
            self.star = _read_list(self.star_path)
            change(self.known, self.star)
            _write_list(self.known_path, self.known)
            _write_list(self.star_path, self.star)

    def mark(self, words: list[str], kind: str):
        words = [w.strip().lower() for w in words if w.strip()]

        def change(known: set[str], star: set[str]):
            if kind == "known":
                known.update(words)
                star.difference_update(words)
            elif kind == "star":
                star.update(words)
                known.difference_update(words)
            elif kind == "forget":
                known.difference_update(words)
                star.difference_update(words)

        self._update(change)

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
        def change(saved_known: set[str], saved_star: set[str]):
            saved_known.update(known)
            saved_star.difference_update(known)
            saved_star.update(star)
            saved_known.difference_update(star)

        self._update(change)
        return len(known), len(star)
