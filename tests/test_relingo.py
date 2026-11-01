import os
import tempfile
import unittest

from rlc.relingo import RelingoError, RelingoLexicon, _translation, is_highlightable

CFG = {"endpoint": "https://example.invalid", "native": "zh", "client_version": "3.31.1",
       "cache_ttl_hours": 12, "min_interval_seconds": 0, "timeout_seconds": 1,
       "max_words_per_request": 500, "only_dictionary_words": False, "local_jargon": False}


class StubClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.bodies = []
        self.token = None

    def available(self):
        return True, ""

    def call(self, fn, body):
        self.bodies.append(body)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def lexicon(tmp, responses):
    lex = RelingoLexicon(dict(CFG), "relingo-Intermediate", tmp, None, None)
    lex.client = StubClient(responses)
    return lex


class RelingoTest(unittest.TestCase):
    def test_highlight_rule_matches_extension(self):
        self.assertTrue(is_highlightable({"word": "x"}))
        self.assertFalse(is_highlightable({"mastered": True}))
        self.assertFalse(is_highlightable({"strange": True, "stared": False}))
        self.assertTrue(is_highlightable({"strange": True, "stared": True}))

    def test_translation_groups_pos(self):
        item = {"translations": [{"pos": "NOUN", "target": "延迟"}, {"pos": "NOUN", "target": "潜伏期"},
                                 {"pos": "VERB", "target": "等待"}]}
        self.assertEqual(_translation(item), "n. 延迟，潜伏期；v. 等待")

    def test_sends_only_word_list_and_caches_negatives(self):
        with tempfile.TemporaryDirectory() as tmp:
            lex = lexicon(tmp, [{"words": [{"word": "ephemeral", "translations": [{"pos": "ADJ", "target": "短暂"}]}]}])
            out = lex.lookup(["ephemeral", "the"])
            self.assertEqual(set(out), {"ephemeral"})
            self.assertEqual(lex.client.bodies[0], {"words": ["ephemeral", "the"], "to": "zh",
                                                    "level": "relingo-Intermediate"})
            # 新进程：两个词都从磁盘缓存读，不再请求
            lex2 = lexicon(tmp, [])
            self.assertEqual(set(lex2.lookup(["ephemeral", "the"])), {"ephemeral"})
            self.assertEqual(lex2.client.bodies, [])

    def test_failure_falls_back_without_caching(self):
        with tempfile.TemporaryDirectory() as tmp:
            lex = lexicon(tmp, [RelingoError("down", transient=True)])
            self.assertEqual(lex.lookup(["ephemeral"]), {})
            self.assertFalse(os.path.getsize(os.path.join(tmp, "relingo-cache.db")) == 0)
            n = lex.db.execute("SELECT count(*) FROM words").fetchone()[0]
            self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
