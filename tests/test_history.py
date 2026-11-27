import contextlib
import io
import json
import os
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from termwise import cli

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TW = os.path.join(ROOT, "bin", "tw")

WORDS = ["alpha", "bravo", "gamma", "delta", "omega", "sigma", "theta", "kappa"]


def record(word: str) -> str:
    """一条历史记录。词长相同、时间戳位数相同，所以每条记录的字节数相同、内容各不相同。"""
    return json.dumps({"ts": 1764200000.5 + WORDS.index(word), "agent": "codex",
                       "words": [{"lemma": word, "translation": "t", "starred": False}]}) + "\n"


def lemmas(lines) -> list[str]:
    return [json.loads(l)["words"][0]["lemma"] for l in lines]


class HistoryFile(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.path = os.path.join(self.dir, "agent-words.jsonl")

    def write(self, text: str, mode: str = "a"):
        with open(self.path, mode, encoding="utf-8") as f:
            f.write(text)

    def add(self, *words: str):
        self.write("".join(record(w) for w in words))


class HistoryTailTest(HistoryFile):
    def setUp(self):
        super().setUp()
        self.tail = cli._HistoryTail(self.path)

    def poll(self) -> list[str]:
        return lemmas(self.tail.poll())

    def test_reads_each_record_once(self):
        self.assertEqual(self.poll(), [])  # 文件还不存在
        self.add("alpha", "bravo")
        self.assertEqual(self.poll(), ["alpha", "bravo"])
        self.assertEqual(self.poll(), [])
        self.add("gamma")
        self.assertEqual(self.poll(), ["gamma"])

    def test_blank_lines_are_skipped(self):
        self.write(record("alpha") + "\n  \n" + record("bravo"))
        self.assertEqual(self.poll(), ["alpha", "bravo"])

    def test_half_written_record_waits_for_the_rest(self):
        self.add("alpha")
        self.assertEqual(self.poll(), ["alpha"])
        half = len(record("bravo")) // 2
        self.write(record("bravo")[:half])
        self.assertEqual(self.poll(), [])
        self.write(record("bravo")[half:])
        self.assertEqual(self.poll(), ["bravo"])

    def test_last_record_without_trailing_newline_is_read(self):
        # 手工编辑过的文件末行可能没有换行；之后 tw agent 追加的记录会直接接在后面。
        self.write(record("alpha") + record("bravo").rstrip("\n"))
        self.assertEqual(self.poll(), ["alpha", "bravo"])
        self.add("gamma")
        self.assertEqual(self.poll(), ["gamma"])

    def test_truncated_file_is_read_from_the_start(self):
        self.add("alpha", "bravo", "gamma")
        self.assertEqual(self.poll(), ["alpha", "bravo", "gamma"])
        self.write("", mode="w")  # : > agent-words.jsonl
        for word in ("delta", "omega", "sigma", "theta"):  # 每条都和旧记录一样长
            self.add(word)
            self.assertEqual(self.poll(), [word])

    def test_recreated_file_is_read_from_the_start(self):
        self.add("alpha", "bravo", "gamma")
        self.assertEqual(self.poll(), ["alpha", "bravo", "gamma"])
        os.remove(self.path)
        self.add("delta", "omega", "sigma", "theta")  # 比旧文件还长
        self.assertEqual(self.poll(), ["delta", "omega", "sigma", "theta"])

    def test_file_removed_between_polls(self):
        self.add("alpha", "bravo")
        self.assertEqual(self.poll(), ["alpha", "bravo"])
        os.remove(self.path)
        self.assertEqual(self.poll(), [])
        self.add("gamma", "delta", "omega")
        self.assertEqual(self.poll(), ["gamma", "delta", "omega"])

    def test_rewrite_in_place_back_to_the_old_size(self):
        # 原地清空后在一次轮询间隔内又写回同样多的字节：inode 和大小都没变，只能靠内容发现。
        self.add("alpha", "bravo", "gamma")
        self.assertEqual(self.poll(), ["alpha", "bravo", "gamma"])
        before = os.stat(self.path)
        self.write(record("delta") + record("omega") + record("sigma"), mode="w")
        after = os.stat(self.path)
        self.assertEqual((before.st_ino, before.st_size), (after.st_ino, after.st_size))
        self.assertEqual(self.poll(), ["delta", "omega", "sigma"])

    def test_rewrite_in_place_past_the_old_size(self):
        self.add("alpha")
        self.assertEqual(self.poll(), ["alpha"])
        self.write(record("bravo") + record("gamma") + record("delta"), mode="w")
        self.assertEqual(self.poll(), ["bravo", "gamma", "delta"])


class HistoryCommandTest(HistoryFile):
    def run_history(self, *args: str):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"TW_DATA": self.dir}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["history", "--color", "never", *args])
        shown = [l.split()[0] for l in out.getvalue().split("\n") if l and not l.startswith("──")]
        return code, shown, err.getvalue().count("tw:")  # 退出码、显示的词、警告条数

    def test_n_limits_to_the_latest_records(self):
        self.add("alpha", "bravo", "gamma")
        self.assertEqual(self.run_history("-n", "2"), (0, ["bravo", "gamma"], 0))
        self.assertEqual(self.run_history(), (0, ["alpha", "bravo", "gamma"], 0))

    def test_n_zero_shows_no_old_records(self):
        self.add("alpha", "bravo", "gamma")
        self.assertEqual(self.run_history("-n", "0"), (0, [], 0))

    def test_record_containing_a_unicode_line_separator(self):
        # tw agent 用 ensure_ascii=False 写记录，释义里的 U+2028 会原样落盘；它不是记录之间的分隔。
        self.add("alpha")
        self.write(json.dumps({"ts": 1764200009.5, "agent": "codex", "words": [
            {"lemma": "separator", "translation": "行\u2028分隔", "starred": False}]}, ensure_ascii=False) + "\n")
        self.assertEqual(self.run_history(), (0, ["alpha", "separator"], 0))

    def test_negative_n_is_an_argument_error(self):
        self.add("alpha", "bravo", "gamma")
        with self.assertRaises(SystemExit) as exit_, contextlib.redirect_stderr(io.StringIO()) as err:
            cli.main(["history", "-n", "-2"])
        self.assertEqual(exit_.exception.code, 2)
        self.assertIn("-n", err.getvalue())

    def test_missing_history_file_shows_nothing(self):
        self.assertEqual(self.run_history(), (0, [], 0))

    def test_unreadable_records_are_skipped_with_one_warning(self):
        bad = ["{not json", "[]", '"text"', '{"ts": "yesterday", "agent": "codex", "words": []}',
               '{"ts": 1764200000.5, "agent": "codex"}',
               '{"ts": 1764200000.5, "agent": "codex", "words": [{"lemma": "half"}]}']
        self.add("alpha")
        self.write("\n" + "\n".join(bad) + "\n")
        with open(self.path, "ab") as f:
            f.write(b"\xff\xfe not utf-8\n")
        self.add("bravo")
        self.assertEqual(self.run_history("-n", "50"), (0, ["alpha", "bravo"], 1))


class HistoryFollowTest(HistoryFile):
    """用真实的 tw history -f 进程：先等它显示完旧记录，再改文件，读到期望的新记录为止。"""

    def follow(self):
        proc = subprocess.Popen([TW, "history", "-n", "1", "-f", "--color", "never"],
                                env=dict(os.environ, TW_DATA=self.dir, TW_CONFIG=os.path.join(self.dir, "none.toml")),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        watchdog = threading.Timer(20, proc.kill)  # 没等到期望的输出时让测试失败，而不是挂住
        watchdog.start()

        def stop():
            watchdog.cancel()
            proc.kill()
            proc.wait()
            proc.stdout.close()
            proc.stderr.close()

        self.addCleanup(stop)
        return proc

    def shown(self, proc, count: int) -> list[str]:
        out = []
        while len(out) < count:
            line = proc.stdout.readline()
            if not line:
                self.fail(f"tw history -f 退出了，只显示了 {out}：{proc.stderr.read()}")
            if not line.startswith("──"):
                out.append(line.split()[0])
        return out

    def test_follow_survives_delete_and_recreate(self):
        self.add("alpha", "bravo", "gamma")
        proc = self.follow()
        self.assertEqual(self.shown(proc, 1), ["gamma"])
        os.remove(self.path)
        self.add("delta", "omega", "sigma", "theta")
        self.assertEqual(self.shown(proc, 4), ["delta", "omega", "sigma", "theta"])
        self.add("kappa")
        self.assertEqual(self.shown(proc, 1), ["kappa"])

    def test_follow_survives_truncation(self):
        self.add("alpha", "bravo", "gamma")
        proc = self.follow()
        self.assertEqual(self.shown(proc, 1), ["gamma"])
        self.write(record("delta"), mode="w")
        self.assertEqual(self.shown(proc, 1), ["delta"])
        self.add("omega", "sigma")  # 文件正好长回清空前的大小
        self.assertEqual(self.shown(proc, 2), ["omega", "sigma"])

    def test_follow_skips_an_unreadable_record_and_keeps_going(self):
        self.add("alpha")
        proc = self.follow()
        self.assertEqual(self.shown(proc, 1), ["alpha"])
        self.write("{not json\n" + record("bravo"))
        self.assertEqual(self.shown(proc, 1), ["bravo"])


if __name__ == "__main__":
    unittest.main()
