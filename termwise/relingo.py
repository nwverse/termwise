"""Relingo 服务端接入：parseContent3 判生词 / 给释义，带缓存、限频、退避，失败回退本地词库。

隐私约束：
- 只发送去重后的小写词表，从不发送行文本、文件路径或 referrer；
- 默认只发送本地词典里存在的词（only_dictionary_words）。

token 存在 macOS 钥匙串（service=termwise-relingo），不写文件、不打印。
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Iterable, Optional

from .lexicon import EcdictLexicon, Entry

KEYCHAIN_SERVICE = "termwise-relingo"
KEYCHAIN_ACCOUNT = "token"
EXTENSION_ID = "dpphkcfmnbkdpmgneljgdhfnccnhmfig"
AUTH_ERRORS = {"invalid signature", "illegal user token", "missing user token"}
AUTH_CODES = {2001, 2002}
POS = {"ADJ": "adj.", "NOUN": "n.", "VERB": "v.", "ADV": "adv.", "PRON": "pron.",
       "ADP": "prep.", "CONJ": "conj.", "CCONJ": "conj.", "NUM": "num.", "INTJ": "int.",
       "DET": "det.", "AUX": "aux.", "PROPN": "n."}


class RelingoError(Exception):
    def __init__(self, message: str, code=None, auth: bool = False, transient: bool = False):
        super().__init__(message)
        self.code, self.auth, self.transient = code, auth, transient


# ---------- token：macOS 钥匙串（不可用时可选 600 权限的文件） ----------

def credentials_path() -> str:
    from .config import config_path
    return os.path.join(os.path.dirname(config_path()), "credentials")


def _keychain_get() -> Optional[str]:
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
                            "-a", KEYCHAIN_ACCOUNT, "-w"], capture_output=True, text=True)
    except OSError:  # 非 macOS：没有 security 命令
        return None
    return r.stdout.strip() or None


def get_token() -> Optional[str]:
    tok = _keychain_get()
    if tok:
        return tok
    try:
        with open(credentials_path(), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def token_storage() -> str:
    if _keychain_get():
        return "钥匙串"
    return f"文件 {credentials_path()}" if os.path.exists(credentials_path()) else "无"


def save_token(token: str, insecure_storage: bool = False) -> str:
    """存 token，返回存放位置的描述。默认只存钥匙串；钥匙串不可用时需显式 insecure_storage。"""
    if not token or any(c in token for c in "\"\\\n\r "):
        raise ValueError("token 为空或含有意外字符")
    # 走 `security -i` 的 stdin，token 不出现在进程参数里（ps 看不到）
    cmd = f'add-generic-password -U -s {KEYCHAIN_SERVICE} -a {KEYCHAIN_ACCOUNT} -l "termwise Relingo token" -w "{token}"\n'
    try:
        r = subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True)
        ok, err = r.returncode == 0 and not r.stderr.strip(), r.stderr.strip()
    except OSError:  # 非 macOS：没有 security 命令
        ok, err = False, "找不到 security 命令（非 macOS 无钥匙串）"
    if ok and _keychain_get() == token:
        _remove_credentials_file()
        return "钥匙串（service=termwise-relingo）"
    if not insecure_storage:
        raise RuntimeError("写入钥匙串失败（当前环境可能无法访问钥匙串）："
                           + err.replace(token, "***")
                           + "\n可加 --insecure-storage 改存到权限 600 的文件里")
    path = credentials_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    os.chmod(path, 0o600)
    return f"文件 {path}（权限 600）"


def _remove_credentials_file():
    try:
        os.remove(credentials_path())
    except OSError:
        pass


def delete_token() -> bool:
    try:
        r = subprocess.run(["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE,
                            "-a", KEYCHAIN_ACCOUNT], capture_output=True, text=True)
        deleted = r.returncode == 0
    except OSError:  # 非 macOS：没有 security 命令
        deleted = False
    had_file = os.path.exists(credentials_path())
    _remove_credentials_file()
    return deleted or had_file


# ---------- 登录：与扩展相同的 OAuth 设备流 / 邮箱验证码 ----------

def oauth_login(cfg: dict, provider: str, state_db: sqlite3.Connection, open_browser: bool = True,
                poll_seconds: float = 2.0, max_polls: int = 120) -> dict:
    """oauth{Google,Apple}Url 拿到授权链接和 id；浏览器里完成授权后，轮询 oauth*Profile(id)
    直到返回带 email 的账号（扩展的登录页就是这么做的，最多等 4 分钟）。"""
    name = {"google": "Google", "apple": "Apple"}[provider]
    client = Client(dict(cfg, min_interval_seconds=0), state_db, None)
    data = client.call(f"oauth{name}Url", {})
    url, sid = data.get("url"), data.get("id")
    if not url or not sid:
        raise RuntimeError("Relingo 没有返回登录链接")
    print(f"在浏览器里用 {name} 账号登录 Relingo：\n  {url}\n等待授权完成（最多 {int(poll_seconds * max_polls)} 秒，Ctrl-C 取消）…",
          file=sys.stderr)
    if open_browser:
        for opener in ("open", "xdg-open"):
            try:
                subprocess.run([opener, url], capture_output=True)
                break
            except OSError:
                continue
    for _ in range(max_polls):
        time.sleep(poll_seconds)
        account = client.call(f"oauth{name}Profile", {"id": sid}) or {}
        if account.get("email"):
            return account
    raise RuntimeError("等待授权超时")


def passcode_login(cfg: dict, email: str, state_db: sqlite3.Connection) -> dict:
    client = Client(dict(cfg, min_interval_seconds=0), state_db, None)
    client.call("sendPasscode", {"email": email, "type": "LOGIN"})
    code = input(f"验证码已发到 {email}，请输入：").strip()
    return client.call("login", {"type": "PASSCODE", "email": email, "code": code}) or {}


def installed_extension_version() -> str:
    base = os.path.expanduser(
        f"~/Library/Application Support/Google/Chrome/Default/Extensions/{EXTENSION_ID}")
    try:
        versions = sorted(os.listdir(base), key=lambda v: [int(x) if x.isdigit() else 0
                                                           for x in v.replace("_", ".").split(".")])
        with open(os.path.join(base, versions[-1], "manifest.json"), encoding="utf-8") as f:
            return json.load(f)["version"]
    except (OSError, IndexError, KeyError, ValueError):
        return "3.31.1"


# ---------- HTTP 客户端 + 跨进程限频 / 退避状态 ----------

class Client:
    def __init__(self, cfg: dict, state_db: sqlite3.Connection, token: Optional[str]):
        self.cfg = cfg
        self.db = state_db
        self.token = token
        self.version = cfg.get("client_version") or installed_extension_version()

    def _state(self, key: str, default=0.0) -> float:
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return float(row[0]) if row else default

    def _set_state(self, **kv):
        self.db.executemany("INSERT OR REPLACE INTO state VALUES (?,?)", list(kv.items()))
        self.db.commit()

    def _reserve_request(self) -> float:
        """原子预留下一次请求的开始时间，提交后再在锁外等待。"""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            now = time.time()
            interval = float(self.cfg["min_interval_seconds"])
            last_request_at = self._state("last_request_at")
            max_queue_delay = max(60.0, interval * 64)
            if last_request_at > now + max_queue_delay:
                # 至多排队 64 个槽位；更远的时间戳视为时钟回拨或状态损坏。
                next_request_at = now
            else:
                next_request_at = max(now, last_request_at + interval)
            self.db.execute("INSERT OR REPLACE INTO state VALUES (?,?)",
                            ("last_request_at", next_request_at))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return next_request_at

    def available(self) -> tuple[bool, str]:
        down = self._state("down_until")
        if time.time() < down:
            return False, f"Relingo 暂不可用，退避中（{int(down - time.time())}s 后重试）"
        return True, ""

    def call(self, fn: str, body: dict) -> dict:
        # 限频：先在短事务中跨进程预留槽位，再在锁外等待，等待而不是丢弃
        next_request_at = self._reserve_request()
        wait = next_request_at - time.time()
        if wait > 0:
            time.sleep(wait)
        headers = {
            "Content-Type": "application/json",
            "x-relingo-version": self.version,
            "x-relingo-platform": "extension",
            "x-relingo-lang": self.cfg["native"],
            "x-relingo-dest-lang": "en",
            # Cloudflare 会拦 Python 默认 UA（error 1010），这里如实标明客户端身份
            "User-Agent": "termwise/0.2 (terminal client)",
        }
        if self.token:
            headers["x-relingo-token"] = self.token
        req = urllib.request.Request(f"{self.cfg['endpoint'].rstrip('/')}/api/{fn}",
                                     data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=float(self.cfg["timeout_seconds"])) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as e:
            raise self._fail(RelingoError(f"HTTP {e.code}", code=e.code,
                                          transient=e.code == 429 or e.code >= 500,
                                          auth=e.code == 401))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise self._fail(RelingoError(f"网络错误：{e}", transient=True))
        if payload.get("code") != 0:
            msg = str(payload.get("message") or "")
            raise self._fail(RelingoError(msg or f"code {payload.get('code')}", code=payload.get("code"),
                                          auth=msg.lower() in AUTH_ERRORS or payload.get("code") in AUTH_CODES))
        self._set_state(backoff=0, down_until=0)
        return payload.get("data") or {}

    def _fail(self, err: RelingoError) -> RelingoError:
        if err.transient:
            # 指数退避：30s 起翻倍，最长 30 分钟
            backoff = min(max(30.0, self._state("backoff") * 2), 1800.0)
            self._set_state(backoff=backoff, down_until=time.time() + backoff)
        return err


# ---------- Lexicon 实现 ----------

def _translation(item: dict, width: int = 40) -> str:
    groups: dict[str, list[str]] = {}
    for t in item.get("translations") or []:
        target = (t.get("target") or "").strip()
        if target:
            groups.setdefault(POS.get(t.get("pos") or "", ""), []).append(target)
    parts = [f"{pos} {'，'.join(ts[:3])}".strip() for pos, ts in list(groups.items())[:2]]
    s = "；".join(parts)
    return s if len(s) <= width else s[: width - 1] + "…"


def _phonetic(item: dict) -> str:
    ph = item.get("phonetic") or {}
    vals = ph.get("us") or ph.get("uk") or []
    return vals[0].strip("/") if vals else ""


def is_highlightable(item: dict) -> bool:
    """与扩展里的判定一致（content.js X5）：未掌握，且不是「标过陌生但取消了星标」。"""
    return not (item.get("mastered") or (item.get("strange") and not item.get("stared")))


class RelingoLexicon:
    def __init__(self, cfg: dict, level: str, data_dir: str, fallback: Optional[EcdictLexicon],
                 token: Optional[str] = None):
        self.cfg = cfg
        self.level = level
        self.fallback = fallback
        os.makedirs(data_dir, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(data_dir, "relingo-cache.db"), timeout=5)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS words (level TEXT, word TEXT, item TEXT, fetched_at REAL,
                                              PRIMARY KEY (level, word));
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value REAL);
            CREATE TABLE IF NOT EXISTS account_tokens (token_hash TEXT PRIMARY KEY,
                                                       account_key TEXT NOT NULL);
        """)
        # 旧的 |user 行无法判断属于哪个账号，只能失效，不能迁移给当前登录者。
        self.db.execute("DELETE FROM words WHERE level LIKE '%|user'")
        self.db.commit()
        self.client = Client(cfg, self.db, token)
        self.authenticated = bool(token)
        self._token_fingerprint = None
        if token:
            self._token_fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()
        self._account_key: Optional[str] = None
        if self._token_fingerprint:
            row = self.db.execute("SELECT account_key FROM account_tokens WHERE token_hash=?",
                                  (self._token_fingerprint,)).fetchone()
            self._account_key = row[0] if row else None
        self._account_key_ready = bool(self._account_key)
        self.mem: dict[str, Optional[Entry]] = {}
        self.warned: set[str] = set()
        self.stats = {"requests": 0, "cached": 0, "fallback": 0}

    def _warn(self, key: str, msg: str):
        if key not in self.warned:
            self.warned.add(key)
            print(f"tw: {msg}", file=sys.stderr)

    def _entry(self, word: str, item: Optional[dict]) -> Optional[Entry]:
        if item is None:
            return None  # 服务端没返回 = 不是生词
        lemma = (item.get("lemma") or item.get("word") or word).lower()
        if lemma != word and self.fallback and not self.fallback.lookup([lemma]):
            lemma = word  # 服务端偶尔给出不存在的原形（unrelated→unrelate），此时显示原词
        return Entry(word=lemma,
                     phonetic=_phonetic(item), translation=_translation(item), rank=0, tags="",
                     hard=is_highlightable(item), starred=bool(item.get("stared")), source="relingo")

    def _sendable(self, words: list[str]) -> list[str]:
        if not self.cfg.get("only_dictionary_words", True) or not self.fallback:
            return words
        known = self.fallback.lookup(words)
        return [w for w in words if w in known]

    def lookup(self, words: Iterable[str]) -> dict[str, Entry]:
        words = sorted({w for w in words if w})
        out: dict[str, Entry] = {}
        todo = [w for w in words if w not in self.mem]
        # 1) 磁盘缓存
        if todo:
            ttl = float(self.cfg["cache_ttl_hours"]) * 3600
            fresh_after = time.time() - ttl
            rows = {}
            cache_level = self._cache_level()
            if cache_level:
                for i in range(0, len(todo), 500):
                    chunk = todo[i:i + 500]
                    q = f"SELECT word, item, fetched_at FROM words WHERE level=? AND word IN ({','.join('?' * len(chunk))})"
                    for w, item, at in self.db.execute(q, [cache_level, *chunk]):
                        if at >= fresh_after:
                            rows[w] = item
            for w, item in rows.items():
                self.mem[w] = self._entry(w, json.loads(item) if item else None)
                self.stats["cached"] += 1
            todo = [w for w in todo if w not in self.mem]
        # 2) 只把本地词典认识的词发给服务端；其余词不发，视为非生词
        send = self._sendable(todo)
        for w in set(todo) - set(send):
            self.mem[w] = None
        # 3) 请求服务端
        if send:
            self._fetch(send)
        jargon = self._local_jargon([w for w in words if self.mem.get(w) is None])
        for w in words:
            e = self.mem.get(w) or jargon.get(w)
            if e:
                out[w] = e
        return out

    def _local_jargon(self, words: list[str]) -> dict[str, Entry]:
        """Relingo 不收录的计算机术语（如 idempotent）：本地无词频、无考试标签、只有 [计] 义项。
        这类词 Relingo 词库里不存在，用本地释义补上不会和个人已掌握词冲突。"""
        if not words or not self.fallback or not self.cfg.get("local_jargon", True):
            return {}
        out = {}
        for w, e in self.fallback.lookup(words).items():
            if e.rank == 0 and not e.tags and e.translation.startswith("[计]"):
                out[w] = Entry(e.word, e.phonetic, e.translation, 0, "", hard=True, source="local")
        return out

    def _cache_level(self) -> Optional[str]:
        # 登录结果含个人已掌握/生词本，按稳定账号隔离；token 轮换不能改变缓存命名空间。
        if not self.authenticated:
            return f"{self.level}|anon"
        if not self._account_key_ready:
            # 退避期间不能为了确定缓存命名空间而绕过 available() 发请求。
            ok, _ = self.client.available()
            if not ok:
                return None
            self._account_key_ready = True
            try:
                info = self.user_info()
            except RelingoError as e:
                if e.auth:
                    self._warn("auth", "Relingo token 无效或已过期，改为未登录模式（运行 tw auth login 重新登录）")
                    self.client.token, self.authenticated = None, False
                    return f"{self.level}|anon"
                self._warn("account", f"无法确认 Relingo 账号身份（{e}），本次不读写登录缓存")
                return None
            identity = ""
            for key in ("_id", "id", "userId", "user_id"):
                value = str(info.get(key) or "").strip()
                if value:
                    identity = f"id:{value}"
                    break
            if not identity:
                email = str(info.get("email") or "").strip().lower()
                if email:
                    identity = f"email:{email}"
            if not identity:
                self._warn("account", "Relingo 账号信息缺少稳定用户 ID，本次不读写登录缓存")
                return None
            self._account_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            # 只保存 token 指纹和账号摘要；下次打开同一 token 无需联网即可命中个人缓存。
            self.db.execute("INSERT OR REPLACE INTO account_tokens VALUES (?,?)",
                            (self._token_fingerprint, self._account_key))
            self.db.commit()
        if not self._account_key:
            return None
        return f"{self.level}|account:{self._account_key}"

    def _fetch(self, words: list[str]):
        ok, why = self.client.available()
        if not ok:
            self._use_fallback(words, why)
            return
        cache_level = self._cache_level()
        size = int(self.cfg["max_words_per_request"])
        for i in range(0, len(words), size):
            chunk = words[i:i + size]
            try:
                data = self.client.call("parseContent3", {"words": chunk, "to": self.cfg["native"],
                                                          "level": self.level})
            except RelingoError as e:
                if e.auth and self.client.token:
                    self._warn("auth", "Relingo token 无效或已过期，改为未登录模式（运行 tw auth login 重新登录）")
                    self.client.token, self.authenticated = None, False
                    return self._fetch(words[i:])
                self._use_fallback(words[i:], f"Relingo 请求失败（{e}），本次使用本地词库")
                return
            self.stats["requests"] += 1
            items = {(it.get("word") or "").lower(): it for it in data.get("words") or []}
            now = time.time()
            rows = []
            for w in chunk:
                it = items.get(w)
                self.mem[w] = self._entry(w, it)
                if cache_level:
                    rows.append((cache_level, w, json.dumps(it, ensure_ascii=False) if it else None, now))
            if rows:
                self.db.executemany("INSERT OR REPLACE INTO words VALUES (?,?,?,?)", rows)
                self.db.commit()

    def _use_fallback(self, words: list[str], why: str):
        self._warn("fallback", why)
        self.stats["fallback"] += len(words)
        local = self.fallback.lookup(words) if self.fallback else {}
        for w in words:
            self.mem[w] = local.get(w)  # 只放内存，不写缓存

    def user_info(self) -> dict:
        return self.client.call("getUserInfo", {})

    def sync_mark(self, words: list[str], kind: str):
        """把 tw known / star / forget 同步到 Relingo 账号，调用方式与扩展相同（content.js）：
        已掌握 = submitVocabulary(mastered 词书)，生词本 = submitVocabulary(当前生词本, strange)，
        取消用 removeVocabularyWords / removeStarWord。star 时同时移出已掌握，与本地 mark 一致。"""
        books = self.client.call("getVocabularyList", {}) or []
        en = [b for b in books if b.get("lang") == "en"]
        mastered = next((b.get("_id") for b in en if b.get("id") == "mastered"), None) or "mastered"
        current = next((b for b in en if b.get("active") and b.get("isCurrent")), None) \
            or next((b for b in en if b.get("id") == "default"), None)
        strange = (current or {}).get("_id") or "default"
        if kind == "known":
            self.client.call("submitVocabulary", {"id": mastered, "type": "mastered", "words": words})
        elif kind == "star":
            self.client.call("removeVocabularyWords", {"id": mastered, "words": words})
            self.client.call("submitVocabulary", {"id": strange, "type": "strange", "words": words})
        elif kind == "forget":
            self.client.call("removeVocabularyWords", {"id": mastered, "words": words})
            for w in words:
                self.client.call("removeStarWord", {"word": w})
        # 缓存里这些词的 mastered / stared 已过期
        self.db.executemany("DELETE FROM words WHERE word=?", [(w,) for w in words])
        self.db.commit()

    def clear_cache(self) -> int:
        n = self.db.execute("DELETE FROM words").rowcount
        self.db.execute("DELETE FROM state")
        self.db.commit()
        return n
