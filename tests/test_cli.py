import argparse
import os
import unittest
from unittest import mock

from termwise import cli


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


if __name__ == "__main__":
    unittest.main()
