import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from termwise import relingo as R
from termwise.cli import main


def security_result(returncode, stderr):
    return mock.Mock(returncode=returncode, stdout="", stderr=stderr)


NOT_FOUND = "The specified item could not be found in the keychain."


class DeleteTokenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cred = os.path.join(self.tmp.name, "credentials")
        patcher = mock.patch.object(R, "credentials_path", return_value=self.cred)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_keychain_failure_raises_instead_of_reporting_no_token(self):
        with mock.patch.object(R.subprocess, "run",
                               return_value=security_result(1, "User interaction is not allowed.")):
            with self.assertRaisesRegex(RuntimeError, "删除钥匙串"):
                R.delete_token()

    def test_item_not_found_without_file_returns_false(self):
        with mock.patch.object(R.subprocess, "run", return_value=security_result(1, NOT_FOUND)):
            self.assertFalse(R.delete_token())

    def test_item_not_found_with_file_removes_file(self):
        with open(self.cred, "w", encoding="utf-8") as f:
            f.write("tok\n")
        with mock.patch.object(R.subprocess, "run", return_value=security_result(1, NOT_FOUND)):
            self.assertTrue(R.delete_token())
        self.assertFalse(os.path.exists(self.cred))

    def test_file_removal_failure_raises(self):
        with open(self.cred, "w", encoding="utf-8") as f:
            f.write("tok\n")
        with mock.patch.object(R.subprocess, "run", return_value=security_result(1, NOT_FOUND)), \
                mock.patch.object(R.os, "remove", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(RuntimeError, "删除凭据文件"):
                R.delete_token()


class LogoutCommandTest(unittest.TestCase):
    def logout(self, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(R, "delete_token", **kwargs), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["auth", "logout"])
        return rc, out.getvalue(), err.getvalue()

    def test_logout_failure_exits_nonzero(self):
        rc, _, err = self.logout(side_effect=RuntimeError("删除钥匙串里的 token 失败：denied"))
        self.assertEqual(rc, 1)
        self.assertIn("删除钥匙串", err)

    def test_logout_without_token(self):
        rc, out, _ = self.logout(return_value=False)
        self.assertEqual(rc, 0)
        self.assertIn("没有保存过 token", out)

    def test_logout_success(self):
        rc, out, _ = self.logout(return_value=True)
        self.assertEqual(rc, 0)
        self.assertIn("已删除 Relingo token", out)


if __name__ == "__main__":
    unittest.main()
