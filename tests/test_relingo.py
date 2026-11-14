import csv
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

from termwise import relingo
from termwise.lexicon import EcdictLexicon, build_db

from termwise.relingo import RelingoError, RelingoLexicon, _translation, is_highlightable

CFG = {"endpoint": "https://example.invalid", "native": "zh", "client_version": "3.31.1",
       "cache_ttl_hours": 12, "min_interval_seconds": 0, "timeout_seconds": 1,
       "max_words_per_request": 500, "only_dictionary_words": False, "local_jargon": False}


class StubClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.bodies = []
        self.calls = []
        self.token = None

    def available(self):
        return True, ""

    def call(self, fn, body):
        self.bodies.append(body)
        self.calls = getattr(self, "calls", []) + [(fn, body)]
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def lexicon(tmp, responses, token=None):
    lex = RelingoLexicon(dict(CFG), "relingo-Intermediate", tmp, None, token)
    lex.client = StubClient(responses)
    lex.client.token = token
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

    def test_authenticated_cache_is_partitioned_by_stable_account(self):
        item = {"word": "ephemeral", "stared": True,
                "translations": [{"pos": "ADJ", "target": "短暂"}]}
        with tempfile.TemporaryDirectory() as tmp:
            first_a = lexicon(tmp, [{"_id": "account-a"}, {"words": [item]}], "token-a-1")
            self.assertTrue(first_a.lookup(["ephemeral"])["ephemeral"].starred)

            # 同一 token 第二次打开直接使用本地账号映射，连 getUserInfo 也不请求。
            known_a = lexicon(tmp, [], "token-a-1")
            self.assertTrue(known_a.lookup(["ephemeral"])["ephemeral"].starred)
            self.assertEqual(known_a.client.calls, [])

            b = lexicon(tmp, [{"_id": "account-b"}, {"words": []}], "token-b")
            self.assertEqual(b.lookup(["ephemeral"]), {})
            self.assertEqual([fn for fn, _ in b.client.calls], ["getUserInfo", "parseContent3"])

            # 同一账号换新 token 后只重新识别一次账号，仍命中原缓存。
            reopened_a = lexicon(tmp, [{"_id": "account-a"}], "token-a-2")
            entry = reopened_a.lookup(["ephemeral"])["ephemeral"]
            self.assertTrue(entry.starred)
            self.assertEqual(reopened_a.stats["cached"], 1)
            self.assertEqual([fn for fn, _ in reopened_a.client.calls], ["getUserInfo"])

    def test_known_token_cache_hit_works_offline(self):
        item = {"word": "ephemeral", "stared": True,
                "translations": [{"pos": "ADJ", "target": "短暂"}]}
        with tempfile.TemporaryDirectory() as tmp:
            first = lexicon(tmp, [{"_id": "account-a"}, {"words": [item]}], "token-a")
            first.lookup(["ephemeral"])

            offline = lexicon(tmp, [], "token-a")
            with mock.patch.object(offline.client, "available", return_value=(False, "offline")) as available:
                entry = offline.lookup(["ephemeral"])["ephemeral"]
            self.assertTrue(entry.starred)
            self.assertEqual(offline.client.calls, [])
            available.assert_not_called()

    def test_unknown_token_resolution_respects_backoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            lex = lexicon(tmp, [], "token-a")
            with mock.patch.object(lex.client, "available", return_value=(False, "backoff")):
                self.assertEqual(lex.lookup(["ephemeral"]), {})
            self.assertEqual(lex.client.calls, [])

    def test_unresolved_account_does_not_persist_authenticated_entries(self):
        item = {"word": "ephemeral", "stared": True,
                "translations": [{"pos": "ADJ", "target": "短暂"}]}
        with tempfile.TemporaryDirectory() as tmp:
            lex = lexicon(tmp, [{}, {"words": [item]}], "token-a")
            self.assertIn("ephemeral", lex.lookup(["ephemeral"]))
            self.assertEqual(lex.db.execute("SELECT count(*) FROM words").fetchone()[0], 0)
            self.assertEqual(lex.db.execute("SELECT count(*) FROM account_tokens").fetchone()[0], 0)

    def test_legacy_shared_user_cache_is_invalidated(self):
        item = {"word": "ephemeral", "stared": True,
                "translations": [{"pos": "ADJ", "target": "短暂"}]}
        with tempfile.TemporaryDirectory() as tmp:
            seed = lexicon(tmp, [])
            seed.db.execute("INSERT INTO words VALUES (?,?,?,?)",
                            ("relingo-Intermediate|user", "ephemeral", json.dumps(item), time.time()))
            seed.db.commit()

            b = lexicon(tmp, [{"_id": "account-b"}, {"words": []}], "token-b")
            self.assertEqual(b.lookup(["ephemeral"]), {})
            self.assertEqual([fn for fn, _ in b.client.calls], ["getUserInfo", "parseContent3"])
            legacy = b.db.execute("SELECT count(*) FROM words WHERE level LIKE '%|user'").fetchone()[0]
            self.assertEqual(legacy, 0)

    def test_failure_falls_back_without_caching(self):
        with tempfile.TemporaryDirectory() as tmp:
            lex = lexicon(tmp, [RelingoError("down", transient=True)])
            self.assertEqual(lex.lookup(["ephemeral"]), {})
            self.assertFalse(os.path.getsize(os.path.join(tmp, "relingo-cache.db")) == 0)
            n = lex.db.execute("SELECT count(*) FROM words").fetchone()[0]
            self.assertEqual(n, 0)


    def test_rate_limit_reserves_slots_across_connections(self):
        interval = 0.2
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "relingo-cache.db")
        db1 = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self.addCleanup(db1.close)
        db2 = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self.addCleanup(db2.close)
        db1.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value REAL)")
        db1.execute("INSERT INTO state VALUES (?,?)", ("last_request_at", -interval))
        db1.commit()
        clients = [relingo.Client(dict(CFG, min_interval_seconds=interval), db, None)
                   for db in (db1, db2)]

        start_barrier = threading.Barrier(2)
        first_transport = threading.Event()
        second_transport = threading.Event()
        transport_starts = []
        transport_lock = threading.Lock()
        clock_lock = threading.Lock()
        clock_now = [0.0]
        errors = []

        def clock_time():
            with clock_lock:
                return clock_now[0]

        def clock_sleep(delay):
            # A queued caller can wake only after the earlier caller reaches HTTP.
            if not first_transport.wait(timeout=5):
                raise AssertionError("request slept before the first mocked transport started")
            with clock_lock:
                clock_now[0] += delay

        fake_clock = mock.Mock()
        fake_clock.time.side_effect = clock_time
        fake_clock.sleep.side_effect = clock_sleep

        def gated_urlopen(req, timeout):
            with transport_lock:
                transport_starts.append(clock_time())
                transport_number = len(transport_starts)
            if transport_number == 1:
                first_transport.set()
                if not second_transport.wait(timeout=5):
                    raise AssertionError("second request did not reach the mocked transport")
            else:
                second_transport.set()
            return io.BytesIO(b'{"code": 0, "data": {}}')

        def call(client):
            try:
                start_barrier.wait(timeout=5)
                client.call("parseContent3", {"words": []})
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=call, args=(client,)) for client in clients]
        with mock.patch.object(relingo, "time", fake_clock), \
                mock.patch.object(relingo.urllib.request, "urlopen", side_effect=gated_urlopen):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(transport_starts), 2)
        transport_starts.sort()
        self.assertGreaterEqual(transport_starts[1] - transport_starts[0], interval)

    def test_far_future_request_state_does_not_cause_a_long_sleep(self):
        now = 1000.0
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = sqlite3.connect(os.path.join(tmp.name, "relingo-cache.db"))
        self.addCleanup(db.close)
        db.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value REAL)")
        db.execute("INSERT INTO state VALUES (?,?)", ("last_request_at", now + 3600))
        db.commit()
        client = relingo.Client(dict(CFG, min_interval_seconds=0.2), db, None)
        fake_clock = mock.Mock()
        fake_clock.time.return_value = now

        with mock.patch.object(relingo, "time", fake_clock), \
                mock.patch.object(relingo.urllib.request, "urlopen",
                                  return_value=io.BytesIO(b'{"code": 0, "data": {}}')) as urlopen:
            self.assertEqual(client.call("parseContent3", {"words": []}), {})

        fake_clock.sleep.assert_not_called()
        urlopen.assert_called_once()
        self.assertEqual(db.execute("SELECT value FROM state WHERE key='last_request_at'").fetchone()[0], now)

    def test_sync_mark_uses_the_extension_calls(self):
        books = [{"_id": "s1", "id": "default", "lang": "en", "type": "strange"},
                 {"id": "relingo-GRE", "lang": "en", "type": "builtin"},
                 {"_id": "m1", "id": "mastered", "lang": "en", "type": "mastered"}]
        with tempfile.TemporaryDirectory() as tmp:
            lex = lexicon(tmp, [books, {}, books, {}, {}, books, {}, {}])
            lex.sync_mark(["ephemeral"], "known")
            lex.sync_mark(["ephemeral"], "star")
            lex.sync_mark(["ephemeral"], "forget")
            self.assertEqual([c for c in lex.client.calls if c[0] != "getVocabularyList"], [
                ("submitVocabulary", {"id": "m1", "type": "mastered", "words": ["ephemeral"]}),
                ("removeVocabularyWords", {"id": "m1", "words": ["ephemeral"]}),
                ("submitVocabulary", {"id": "s1", "type": "strange", "words": ["ephemeral"]}),
                ("removeVocabularyWords", {"id": "m1", "words": ["ephemeral"]}),
                ("removeStarWord", {"word": "ephemeral"}),
            ])
    def test_get_token_without_security_binary(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(relingo.subprocess, "run", side_effect=FileNotFoundError("security")), \
                mock.patch.object(relingo, "credentials_path", return_value=os.path.join(tmp, "credentials")):
            self.assertIsNone(relingo._keychain_get())
            self.assertIsNone(relingo.get_token())
            self.assertEqual(relingo.token_storage(), "无")
            self.assertFalse(relingo.delete_token())
            with self.assertRaises(RuntimeError):
                relingo.save_token("abc")


class OnlyDictionaryWordsTest(unittest.TestCase):
    """only_dictionary_words 开启时，没有本地词典也不能把未知词发给 Relingo（#27）。"""

    def _lexicon(self, tmp, fallback, only=True):
        lex = RelingoLexicon(dict(CFG, only_dictionary_words=only), "relingo-Intermediate",
                             tmp, fallback, None)
        lex.client = StubClient([{"words": []}] * 3)
        self.addCleanup(lex.db.close)
        return lex

    def _ecdict(self, tmp, words):
        csv_path = os.path.join(tmp, "ecdict.csv")
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["word", "phonetic", "translation", "bnc", "frq", "tag", "exchange"])
            for word in words:
                w.writerow([word, "", f"a. {word} 的释义", "5000", "5000", "", ""])
        db_path = os.path.join(tmp, "dict", "ecdict.db")
        build_db(csv_path, db_path)
        local = EcdictLexicon(db_path)
        self.addCleanup(local.db.close)
        return local

    def test_missing_dictionary_sends_nothing_and_warns_once(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            lex = self._lexicon(tmp, None)
            self.assertEqual(lex.lookup(["syntheticprivateidentifier", "ephemeral"]), {})
            self.assertEqual(lex.lookup(["anotherprivateidentifier"]), {})
            self.assertEqual(lex.client.calls, [])
            self.assertEqual(err.getvalue().count("tw: "), 1)
            self.assertIn("only_dictionary_words", err.getvalue())

    def test_empty_dictionary_sends_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            lex = self._lexicon(tmp, self._ecdict(tmp, []))
            self.assertEqual(lex.lookup(["syntheticprivateidentifier"]), {})
            self.assertEqual(lex.client.calls, [])

    def test_only_dictionary_words_are_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            lex = self._lexicon(tmp, self._ecdict(tmp, ["ephemeral"]))
            lex.lookup(["ephemeral", "syntheticprivateidentifier"])
            self.assertEqual([b["words"] for b in lex.client.bodies], [["ephemeral"]])

    def test_filter_disabled_without_dictionary_sends_all(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            lex = self._lexicon(tmp, None, only=False)
            lex.lookup(["ephemeral", "syntheticprivateidentifier"])
            self.assertEqual([b["words"] for b in lex.client.bodies],
                             [["ephemeral", "syntheticprivateidentifier"]])
            self.assertEqual(err.getvalue(), "")


def _item(mastered=False):
    return {"word": "ephemeral", "mastered": mastered,
            "translations": [{"pos": "ADJ", "target": "短暂"}]}


class MemoryCacheExpiryTest(unittest.TestCase):
    """#29：同一实例（tail -f … | tw -a）里，内存结果也要按 TTL / 退避到期后重新查询。
    用真实 Client，只替换 urlopen 和时钟。"""

    TTL = 3600.0

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.now = 1_000_000.0
        clock = mock.patch.object(relingo.time, "time", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        net = mock.patch.object(relingo.urllib.request, "urlopen", side_effect=self._respond)
        net.start()
        self.addCleanup(net.stop)
        self.responses, self.sent = [], []
        self.lex = self._open()

    def _open(self, ttl=None):
        ttl = self.TTL if ttl is None else ttl
        lex = RelingoLexicon(dict(CFG, cache_ttl_hours=ttl / 3600), "relingo-Intermediate",
                             self.tmp, None)
        self.addCleanup(lex.db.close)
        return lex

    def _respond(self, req, timeout):
        self.sent.append(json.loads(req.data)["words"])
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return io.BytesIO(json.dumps({"code": 0, "data": r}).encode())

    def test_positive_entry_expires_after_ttl(self):
        self.responses = [{"words": [_item(mastered=False)]}, {"words": [_item(mastered=True)]}]
        self.assertTrue(self.lex.lookup(["ephemeral"])["ephemeral"].hard)
        self.now += self.TTL - 1
        self.assertTrue(self.lex.lookup(["ephemeral"])["ephemeral"].hard)
        self.assertEqual(len(self.sent), 1)  # TTL 内不重复请求
        self.now += 2
        self.assertFalse(self.lex.lookup(["ephemeral"])["ephemeral"].hard)
        self.assertEqual(self.sent, [["ephemeral"], ["ephemeral"]])

    def test_negative_entry_expires_after_ttl(self):
        self.responses = [{"words": []}, {"words": [_item()]}]
        self.assertEqual(self.lex.lookup(["ephemeral"]), {})
        self.now += self.TTL + 1
        self.assertIn("ephemeral", self.lex.lookup(["ephemeral"]))
        self.assertEqual(len(self.sent), 2)

    def test_expired_memory_prefers_fresher_disk_row(self):
        self.responses = [{"words": [_item(mastered=False)]}, {"words": [_item(mastered=True)]}]
        self.lex.lookup(["ephemeral"])
        self.now += self.TTL + 1
        other = self._open()  # 另一个进程已经刷新过磁盘缓存
        self.assertFalse(other.lookup(["ephemeral"])["ephemeral"].hard)
        self.now += 1
        self.assertFalse(self.lex.lookup(["ephemeral"])["ephemeral"].hard)
        self.assertEqual(len(self.sent), 2)

    def test_zero_ttl_keeps_memory_for_min_lifetime(self):
        lex = self._open(ttl=0)
        self.responses = [{"words": [_item()]}, {"words": [_item(mastered=True)]}]
        self.assertTrue(lex.lookup(["ephemeral"])["ephemeral"].hard)  # prefetch()
        self.now += 0.5
        self.assertTrue(lex.lookup(["ephemeral"])["ephemeral"].hard)  # 同一批逐行 hits()
        self.now += 30
        lex.lookup(["ephemeral"])  # 相邻几行
        self.assertEqual(len(self.sent), 1)
        self.now += relingo.MEM_MIN_LIFETIME
        self.assertFalse(lex.lookup(["ephemeral"])["ephemeral"].hard)
        self.assertEqual(len(self.sent), 2)

    def test_transient_failure_is_retried_after_backoff(self):
        self.responses = [relingo.urllib.error.URLError("offline"), {"words": [_item()]}]
        self.assertEqual(self.lex.lookup(["ephemeral"]), {})
        self.now += 10  # 仍在 30s 退避期内：不重试
        self.assertEqual(self.lex.lookup(["ephemeral"]), {})
        self.assertEqual(len(self.sent), 1)
        self.now += 25
        self.assertTrue(self.lex.lookup(["ephemeral"])["ephemeral"].hard)
        self.assertEqual(len(self.sent), 2)
        self.now += 1  # 恢复后的结果按 TTL 正常缓存
        self.lex.lookup(["ephemeral"])
        self.assertEqual(len(self.sent), 2)

    def test_fallback_during_backoff_is_retried_when_backoff_ends(self):
        self.lex.client._set_state(down_until=self.now + 100)
        self.responses = [{"words": [_item()]}]
        self.assertEqual(self.lex.lookup(["ephemeral"]), {})
        self.assertEqual(self.sent, [])
        self.now += 101
        self.assertIn("ephemeral", self.lex.lookup(["ephemeral"]))
        self.assertEqual(len(self.sent), 1)


if __name__ == "__main__":
    unittest.main()
