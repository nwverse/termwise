import argparse
import os
import tempfile
import unittest
from unittest import mock

from termwise import cli


class ListFileBoundaryTest(unittest.TestCase):
    def test_list_command_does_not_merge_words_across_files(self):
        # 直接走 cmd_annotate 的 --list 路径：main 上两个文件的边界词会被拼接合并
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a.md")
            b = os.path.join(tmp, "b.md")
            with open(a, "w", encoding="utf-8") as f:
                f.write("ephemeral")
            with open(b, "w", encoding="utf-8") as f:
                f.write("serendipity")
            seen = []
            args = argparse.Namespace(files=[a, b], list=True, color="never", phonetic=False, json=False)
            with mock.patch.object(cli, "_annotator", return_value=mock.Mock()), \
                    mock.patch.object(cli, "_color", return_value=False), \
                    mock.patch.object(cli, "_collect", side_effect=lambda ann, text: seen.append(text) or []), \
                    mock.patch.object(cli, "_print_list", return_value=None):
                cli.cmd_annotate(args)
        self.assertEqual(len(seen), 1)
        words = seen[0].split()
        self.assertIn("ephemeral", words)
        self.assertIn("serendipity", words)
        self.assertNotIn("ephemeralserendipity", words)

    def test_read_all_appends_newline_only_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a.md")
            b = os.path.join(tmp, "b.md")
            with open(a, "w", encoding="utf-8") as f:
                f.write("ephemeral")
            with open(b, "w", encoding="utf-8") as f:
                f.write("serendipity")
            self.assertEqual(cli._read_all([a, b]), "ephemeral\nserendipity\n")

    def test_existing_newlines_and_empty_files_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "a.md")
            b = os.path.join(tmp, "b.md")
            with open(a, "w", encoding="utf-8") as f:
                f.write("ephemeral\n")
            with open(b, "w", encoding="utf-8") as f:
                f.write("")
            self.assertEqual(cli._read_all([a, b]), "ephemeral\n")


if __name__ == "__main__":
    unittest.main()
