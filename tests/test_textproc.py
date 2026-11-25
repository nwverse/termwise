import time
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

    def test_skips_start_at_the_token_edge(self):
        # 左边界只限制起点：紧贴符号、数字或引号的 URL、邮箱、路径、文件名仍整段跳过。
        for s in ("+https://example.com/ephemeral", "(https://example.com/ephemeral)",
                  "git+https://example.com/ephemeral.git", "...https://example.com/ephemeral",
                  "<first.last+tag@example.com>", "'src/latency.rs'", "a/docker-compose.override.yml",
                  "\"latency-report.md\"", "$HOME/latency.d/ephemeral.conf",
                  "\"$report-latency.txt\"", "$report-latency/ephemeral", "\"-20261106-latency.pdf\""):
            self.assertEqual(norms(f"see {s} done"), ["see", "done"], s)

    def test_long_tokens_stay_linear(self):
        # 每种片段只该从左边界试一次；在 token 内逐位置重试时 32 KB 要 1～8 CPU 秒（#11）。
        for unit in ("a.", "a+", "a-", "a_", "Aa", "ab.c-", "a.b@", "a--"):
            line = unit * (32000 // len(unit))
            start = time.process_time()
            extract_words(line)
            self.assertLess(time.process_time() - start, 0.5, unit)


if __name__ == "__main__":
    unittest.main()
