import csv
import os
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


if __name__ == "__main__":
    unittest.main()
