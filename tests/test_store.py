import csv
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from termwise.store import UserStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 子进程先取快照并报告就绪，等父进程放行后再保存：所有进程手里都是同一份旧快照。
WORKER = """
import sys
sys.path.insert(0, sys.argv[1])
from termwise.store import UserStore
store = UserStore(root=sys.argv[2], devwords=False)
print("ready", flush=True)
sys.stdin.readline()
store.mark([sys.argv[3]], "known")
"""


def store(root):
    return UserStore(root=root, devwords=False)


class UserStoreTest(unittest.TestCase):
    def test_stale_snapshot_does_not_undo_another_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = store(tmp), store(tmp)  # 两边都先取到空快照
            first.mark(["ephemeral"], "known")
            second.mark(["idempotent"], "known")
            self.assertEqual(store(tmp).known, {"ephemeral", "idempotent"})
            self.assertEqual(second.known, {"ephemeral", "idempotent"})

    def test_each_mark_only_changes_its_own_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            store(tmp).mark(["ephemeral", "idempotent", "latency"], "known")
            a, b, c = store(tmp), store(tmp), store(tmp)
            a.mark(["ephemeral"], "star")
            b.mark(["idempotent"], "forget")
            c.mark(["mitigate"], "star")
            final = store(tmp)
            self.assertEqual(final.known, {"latency"})
            self.assertEqual(final.star, {"ephemeral", "mitigate"})

    def test_import_keeps_words_saved_meanwhile(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = os.path.join(tmp, "relingo.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["word", "translation", "phonetic", "mastered", "sentences"])
                w.writerow(["ephemeral", "", "", "✓", ""])
                w.writerow(["idempotent", "", "", "✗", ""])
            importer = store(tmp)
            store(tmp).mark(["latency"], "known")
            self.assertEqual(importer.import_relingo_csv(csv_path), (1, 1))
            final = store(tmp)
            self.assertEqual(final.known, {"ephemeral", "latency"})
            self.assertEqual(final.star, {"idempotent"})

    def test_symlinked_list_stays_a_link_and_keeps_its_mode(self):
        # 词表放在 dotfiles / 同步盘、再链接进 data/user 时，保存要写到链接指向的文件。
        with tempfile.TemporaryDirectory() as tmp:
            sync, user = os.path.join(tmp, "sync"), os.path.join(tmp, "user")
            os.makedirs(sync)
            os.makedirs(user)
            target, link = os.path.join(sync, "known.txt"), os.path.join(user, "known.txt")
            with open(target, "w", encoding="utf-8") as f:
                f.write("ephemeral\n")
            os.chmod(target, 0o640)
            os.symlink(os.path.join("..", "sync", "known.txt"), link)

            store(tmp).mark(["idempotent"], "known")
            self.assertTrue(os.path.islink(link))
            with open(target, encoding="utf-8") as f:
                self.assertEqual(f.read(), "ephemeral\nidempotent\n")
            self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o640)
            self.assertEqual(os.listdir(sync), ["known.txt"])

    def test_failed_save_keeps_the_old_list_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store(tmp).mark(["ephemeral"], "known")
            with mock.patch("termwise.store.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    store(tmp).mark(["idempotent"], "known")
            self.assertEqual(store(tmp).known, {"ephemeral"})
            self.assertEqual(sorted(os.listdir(os.path.join(tmp, "user"))),
                             [".lock", "known.txt", "star.txt"])

    def test_concurrent_processes_keep_every_word(self):
        words = [f"word{chr(ord('a') + i)}" for i in range(8)]
        with tempfile.TemporaryDirectory() as tmp:
            procs = [subprocess.Popen([sys.executable, "-c", WORKER, ROOT, tmp, word],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
                     for word in words]
            try:
                for p in procs:
                    self.assertEqual(p.stdout.readline().strip(), "ready")
                for p in procs:  # 全部取完快照后一起放行
                    p.stdin.write("\n")
                    p.stdin.flush()
                for p in procs:
                    self.assertEqual(p.wait(timeout=30), 0)
            finally:
                for p in procs:
                    if p.poll() is None:
                        p.kill()
                        p.wait()
                    p.stdin.close()
                    p.stdout.close()
            self.assertEqual(store(tmp).known, set(words))
            # 原子替换不留下临时文件
            self.assertEqual(sorted(os.listdir(os.path.join(tmp, "user"))),
                             [".lock", "known.txt", "star.txt"])


if __name__ == "__main__":
    unittest.main()
