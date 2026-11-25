import csv
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from termwise.lexicon import build_db

ROOT = Path(__file__).resolve().parent.parent
CODE = ("import sys; sys.path.insert(0, sys.argv.pop(1)); "
        "from termwise.cli import main; sys.exit(main())")


class PipeBytesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        with (root / "ecdict.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["word", "phonetic", "translation", "bnc", "frq", "tag", "exchange"])
            w.writerow(["ephemeral", "", "adj. 短暂", "99999", "99999", "", ""])
        build_db(str(root / "ecdict.csv"), str(root / "ecdict.db"))
        self.env = dict(os.environ, TW_DATA=str(root), TW_CONFIG=str(root / "missing.toml"))

    def pipe(self, data: bytes) -> bytes:
        r = subprocess.run([sys.executable, "-c", CODE, str(ROOT), "-s", "local", "--color", "never"],
                           input=data, capture_output=True, env=self.env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def run_file(self, data: bytes, *extra_args: str) -> bytes:
        p = Path(self.tmp.name) / "latin1.log"
        p.write_bytes(data)
        r = subprocess.run([sys.executable, "-c", CODE, str(ROOT), "-s", "local", "--color", "never",
                            *extra_args, str(p)],
                           capture_output=True, env=self.env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_line_without_new_words_passes_bytes_through(self):
        data = b"lat\xe9n caf\xe9 log line\n"
        self.assertEqual(self.pipe(data), data)

    def test_eof_residual_bytes_pass_through(self):
        data = b"tail caf\xe9"
        self.assertEqual(self.pipe(data), data)

    def test_annotated_line_keeps_original_bytes(self):
        out = self.pipe(b"ephemeral caf\xe9\n")
        self.assertTrue(out.startswith(b"ephemeral caf\xe9\n"), out)
        self.assertNotIn(b"\xef\xbf\xbd", out)
        self.assertIn("短暂".encode("utf-8"), out)

    def test_file_arg_keeps_original_bytes_and_crlf(self):
        data = b"lat\xe9n caf\xe9 log line\r\nephemeral caf\xe9\n"
        out = self.run_file(data)
        self.assertTrue(out.startswith(b"lat\xe9n caf\xe9 log line\r\nephemeral caf\xe9\n"), out)
        self.assertNotIn(b"\xef\xbf\xbd", out)
        self.assertIn("短暂".encode("utf-8"), out)

    def test_file_arg_eof_residual_bytes_pass_through(self):
        data = b"tail caf\xe9"
        self.assertEqual(self.run_file(data), data)

    def test_list_mode_handles_non_utf8_file_and_stdin(self):
        data = b"ephemeral caf\xe9\n"
        out_file = self.run_file(data, "--list")
        self.assertIn(b"ephemeral", out_file)
        r = subprocess.run([sys.executable, "-c", CODE, str(ROOT), "-s", "local", "--color", "never", "--list"],
                           input=data, capture_output=True, env=self.env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"ephemeral", r.stdout)

    def test_valid_utf8_unchanged(self):
        data = "café 中文输出\n".encode("utf-8")
        self.assertEqual(self.pipe(data), data)
        self.assertEqual(self.run_file(data), data)


if __name__ == "__main__":
    unittest.main()
