import argparse
import csv
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from termwise import cli
from termwise.lexicon import build_db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TW = os.path.join(ROOT, "bin", "tw")


class SourceValidationTest(unittest.TestCase):
    def test_lexicon_rejects_unknown_source(self):
        for source in ("relinog", "locale", "", "LOCAL"):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, "无效的来源"):
                    cli._lexicon({"relingo": {}}, source, "intermediate")

    def test_annotator_rejects_bad_env_source(self):
        # TW_SOURCE 拼写错误不能静默降级成 local，必须与 -s 一样报错
        cfg = {"source": "relingo", "level": "intermediate", "devwords": False, "relingo": {}}
        args = argparse.Namespace(source=None, level=None, all=False)
        with mock.patch.object(cli.C, "load", return_value=cfg), \
                mock.patch.dict(os.environ, {"TW_SOURCE": "relinog"}):
            with self.assertRaisesRegex(ValueError, "无效的来源"):
                cli._annotator(args)

    def test_annotator_rejects_bad_config_source(self):
        cfg = {"source": "relinog", "level": "intermediate", "devwords": False, "relingo": {}}
        args = argparse.Namespace(source=None, level=None, all=False)
        with mock.patch.object(cli.C, "load", return_value=cfg), \
                mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "无效的来源"):
                cli._annotator(args)


class StreamTrailingNewlineTest(unittest.TestCase):
    """#25：末行无换行时，注脚行仍须独占一行，多文件之间不能粘连。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        csv_path = os.path.join(d, "ecdict.csv")
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["word", "phonetic", "translation", "bnc", "frq", "tag", "exchange"])
            w.writerow(["ephemeral", "", "adj. 短暂", "99999", "99999", "", ""])
            w.writerow(["serendipity", "", "n. 机缘巧合", "99999", "99999", "", ""])
        build_db(csv_path, os.path.join(d, "ecdict.db"))
        cls.env = dict(os.environ, TW_DATA=d, TW_CONFIG=os.path.join(d, "none.toml"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _write(self, name, text):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def _tw(self, *files, stdin=None):
        r = subprocess.run([TW, "-s", "local", "--color", "never", *files],
                           input=stdin, capture_output=True, text=True, env=self.env, check=True)
        return r.stdout

    def test_multi_files_without_trailing_newline(self):
        a = self._write("a.md", "ephemeral")
        b = self._write("b.md", "serendipity")
        self.assertEqual(self._tw(a, b),
                         "ephemeral\n  ↳ ephemeral adj. 短暂\n"
                         "serendipity\n  ↳ serendipity n. 机缘巧合\n")

    def test_stdin_without_trailing_newline(self):
        self.assertEqual(self._tw(stdin="ephemeral"), "ephemeral\n  ↳ ephemeral adj. 短暂\n")

    def test_plain_last_line_kept_as_is(self):
        self.assertEqual(self._tw(stdin="plain a"), "plain a")

    def test_plain_file_followed_by_another_file(self):
        a = self._write("p.md", "plain a")
        b = self._write("s.md", "serendipity")
        self.assertEqual(self._tw(a, b), "plain a\nserendipity\n  ↳ serendipity n. 机缘巧合\n")


if __name__ == "__main__":
    unittest.main()
