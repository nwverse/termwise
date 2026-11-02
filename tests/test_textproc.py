import unittest

from termwise.textproc import extract_words, strip_ansi


def norms(s):
    return [w.norm for w in extract_words(s)]


class TextprocTest(unittest.TestCase):
    def test_strip_ansi(self):
        self.assertEqual(strip_ansi("\x1b[31merror\x1b[0m \x1b]8;;http://x\x07link\x1b]8;;\x07"), "error link")

    def test_skip_code_noise(self):
        s = "see https://example.com/ephemeral src/latency.rs --verbose 0xdeadbeef 1a2b3c4d v1.2.3 `mitigate` 'abc' isn't"
        self.assertEqual(norms(s), ["see"])

    def test_split_identifiers(self):
        self.assertEqual(norms("ConnectionPoolManager retry_count is-idempotent HTTPServer"),
                         ["connection", "pool", "manager", "retry", "count", "idempotent", "server"])

    def test_skip_short_acronyms(self):
        self.assertEqual(norms("HTTP EOF TODO WARNING"), ["warning"])

    def test_offsets_point_at_original(self):
        s = "the latency regression"
        for w in extract_words(s):
            self.assertEqual(s[w.start:w.end].lower(), w.norm)


if __name__ == "__main__":
    unittest.main()
