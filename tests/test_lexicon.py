import contextlib
import csv
import os
import sqlite3
import tempfile
import unittest

from termwise.annotate import LEVELS, Annotator
from termwise.lexicon import EcdictLexicon, build_db
from termwise.store import UserStore
from termwise.textproc import Word

FIELDS = ["word", "phonetic", "translation", "bnc", "frq", "tag", "exchange"]

# (word, bnc, frq, tag, exchange)
ROWS = [
    ("toward", "384", "384", "zk gk cet4", ""),
    ("miss", "833", "833", "zk gk", ""),
    ("prove", "671", "671", "gk cet4", ""),
    ("abuse", "1540", "1540", "cet4", ""),
    ("use", "87", "87", "zk gk", "d:used/p:used"),
    ("valid", "2100", "2100", "cet4", ""),
    ("untoward", "18475", "18475", "gre", ""),
    ("remiss", "24156", "24156", "gre", ""),
    ("reprove", "21465", "21465", "gre", ""),
    ("disabuse", "28116", "28116", "gre", ""),
    ("unused", "8412", "8412", "cet6", ""),
    ("invalid", "3211", "3211", "cet6", ""),
    # #13：前缀不是构词前缀的假派生词，自身词频必须保留
    ("collect", "1222", "1222", "cet4", ""),
    ("recollect", "14189", "14189", "cet4 cet6", ""),
    ("very", "80", "80", "zk gk", ""),
    ("revery", "43745", "43745", "", ""),
]


class EcdictLexiconTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        csv_path = os.path.join(cls.tmp.name, "ecdict.csv")
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(FIELDS)
            for word, bnc, frq, tag, exchange in ROWS:
                w.writerow([word, "", f"a. {word} 的释义", bnc, frq, tag, exchange])
        db_path = os.path.join(cls.tmp.name, "db", "ecdict.db")
        build_db(csv_path, db_path)
        cls.lex = EcdictLexicon(db_path)
        store = UserStore(root=cls.tmp.name, devwords=False)
        cls.ann = Annotator(cls.lex, store, LEVELS["intermediate"])

    @classmethod
    def tearDownClass(cls):
        cls.lex.db.close()
        cls.tmp.cleanup()

    def is_hard(self, word):
        e = self.lex.lookup([word])[word]
        return self.ann.is_hard(Word(word, word, 0, len(word)), e)[0]

    def test_gre_words_keep_own_rank(self):
        # issue #2：自身带 gre 标签的前缀词不借词根词频
        for word in ("untoward", "remiss", "reprove", "disabuse"):
            with self.subTest(word=word):
                self.assertTrue(self.is_hard(word))

    def test_prefixed_common_words_use_root_rank(self):
        self.assertEqual(self.lex.lookup(["unused"])["unused"].rank, 87)
        for word in ("unused", "invalid"):
            with self.subTest(word=word):
                self.assertFalse(self.is_hard(word))

    def test_non_compositional_prefix_words_keep_own_rank(self):
        # #13：recollect / revery 去掉前缀后虽是词根，但语义不能由前缀加词根推出，不能借词根词频。
        self.assertEqual(self.lex.lookup(["recollect"])["recollect"].rank, 14189)
        self.assertEqual(self.lex.lookup(["revery"])["revery"].rank, 43745)
        store = UserStore(root=self.tmp.name, devwords=False)
        for word, level in (("recollect", "intermediate"), ("recollect", "advanced"), ("revery", "expert")):
            with self.subTest(word=word, level=level):
                entry = self.lex.lookup([word])[word]
                ann = Annotator(self.lex, store, LEVELS[level])
                self.assertTrue(ann.is_hard(Word(word, word, 0, len(word)), entry)[0])

    def test_all_annotates_every_occurrence_on_one_line(self):
        # --all：同一行重复出现的生词也要逐个标注，不再按 lemma 逐行去重
        line = "untoward untoward untoward"
        every = Annotator(self.lex, self.ann.store, LEVELS["intermediate"], once=False)
        hits = every.hits(line)
        self.assertEqual([h.word.text for h in hits], ["untoward"] * 3)
        self.assertEqual([h.word.start for h in hits], [0, 9, 18])
        # 默认 once=True 仍只标首次
        first = Annotator(self.lex, self.ann.store, LEVELS["intermediate"])
        self.assertEqual(len(first.hits(line)), 1)
        self.assertEqual(first.hits(line), [])


# issue #30: directory name -> where SQLite would land if the path were parsed as a URI
PATH_CASES = {
    "data": None,
    "data#one": "data",                       # '#' starts a URI fragment
    "data?one": "data",                       # '?' starts the URI query
    "data%2Fone": "data/one/ecdict.db",       # '%2F' decodes to '/'
}


class EcdictLexiconPathTest(unittest.TestCase):
    """issue #30: open exactly the given file, read-only, whatever characters the path has."""

    def make_db(self, root, rel, word):
        csv_path = os.path.join(root, f"{word}.csv")
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(FIELDS)
            w.writerow([word, "", f"a. {word} 的释义", "99999", "99999", "", ""])
        build_db(csv_path, os.path.join(root, rel))
        os.remove(csv_path)

    def files(self, root):
        return {os.path.relpath(os.path.join(d, n), root)
                for d, _, names in os.walk(root) for n in names}

    def each_case(self, check, decoy):
        """Run check(root, path) for every case, with an absolute and a relative path.

        Each run gets a fresh directory holding the real database and, if decoy
        is set, another database at the location a URI parser would open.
        """
        for name, misread in PATH_CASES.items():
            for relative in (False, True):
                with self.subTest(name=name, relative=relative), \
                        tempfile.TemporaryDirectory() as root, contextlib.chdir(root):
                    rel = os.path.join(name, "ecdict.db")
                    self.make_db(root, rel, "ephemeral")
                    if decoy and misread:
                        self.make_db(root, misread, "decoy")
                    check(root, rel if relative else os.path.join(root, rel))

    def test_reads_the_database_at_the_given_path(self):
        def check(root, path):
            lex = EcdictLexicon(path)
            with contextlib.closing(lex.db):
                opened = lex.db.execute("PRAGMA database_list").fetchone()[2]
                self.assertEqual(os.path.realpath(opened), os.path.realpath(path))
                self.assertIn("ephemeral", lex.lookup(["ephemeral"]))
                self.assertEqual(lex.lookup(["decoy"]), {})
        self.each_case(check, decoy=True)

    def test_connection_is_read_only(self):
        def check(root, path):
            before = self.files(root)
            lex = EcdictLexicon(path)
            with contextlib.closing(lex.db):
                with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                    lex.db.execute("INSERT INTO words VALUES ('extra', '', '', 0, '')")
            self.assertEqual(self.files(root), before)
        self.each_case(check, decoy=True)

    def test_opening_creates_no_files(self):
        # Without decoys a misparsed path creates an empty database at the
        # truncated location, or fails to open at all
        def check(root, path):
            before = self.files(root)
            lex = EcdictLexicon(path)
            with contextlib.closing(lex.db):
                self.assertEqual(self.files(root), before)
                self.assertIn("ephemeral", lex.lookup(["ephemeral"]))
            self.assertEqual(self.files(root), before)
        self.each_case(check, decoy=False)

    def test_missing_database_creates_no_files(self):
        def check(root, path):
            os.remove(path)
            before = self.files(root)
            with self.assertRaises(FileNotFoundError):
                EcdictLexicon(path)
            self.assertEqual(self.files(root), before)
        self.each_case(check, decoy=False)


if __name__ == "__main__":
    unittest.main()
