"""配置：~/.config/termwise/config.toml（可用 TW_CONFIG 指定），缺省值见 DEFAULTS。"""

import copy
import os
import tomllib

DEFAULTS = {
    # relingo：Relingo 服务端判生词、给释义（失败时回退 local）；local：只用本地 ECDICT
    "source": "relingo",
    # 难度：basic | intermediate | advanced | expert
    "level": "intermediate",
    # 是否把内置开发者常用词（termwise/devwords.txt）视为已认识
    "devwords": True,
    "relingo": {
        "endpoint": "https://api.relingo.net",
        "native": "zh",
        # 扩展版本号会随请求头发送；留空则读取本机已安装扩展的 manifest
        "client_version": "",
        # 缓存有效期：已掌握状态在浏览器里改了之后，最多这么久同步到终端（tw cache clear 立即生效）
        "cache_ttl_hours": 12,
        # 两次请求之间的最短间隔（跨进程生效）
        "min_interval_seconds": 2.0,
        "timeout_seconds": 8.0,
        "max_words_per_request": 500,
        # 只发送本地词典里存在的词，避免把项目代号、内部标识符之类发出去
        "only_dictionary_words": True,
        # Relingo 不收录的计算机术语（如 idempotent）用本地 [计] 释义补上
        "local_jargon": True,
    },
}

TEMPLATE = """\
# tw 配置。改动即时生效（每次运行 tw 时读取）。
source = "relingo"        # relingo | local
level = "intermediate"    # basic | intermediate | advanced | expert
devwords = true           # 内置开发者常用词视为已认识

[relingo]
cache_ttl_hours = 12
min_interval_seconds = 2.0
only_dictionary_words = true
"""

# 难度档位 -> Relingo 级别 id（来自 vocabularyTest 接口返回的 levels）
RELINGO_LEVELS = {
    "basic": "relingo-Beginner",
    "intermediate": "relingo-Intermediate",
    "advanced": "relingo-Advanced",
    "expert": "relingo-Proficient",
}


def config_path() -> str:
    return os.environ.get("TW_CONFIG") or os.path.expanduser("~/.config/termwise/config.toml")


def _merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    path = config_path()
    if os.path.exists(path):
        with open(path, "rb") as f:
            _merge(cfg, tomllib.load(f))
    return cfg


def init() -> str:
    path = config_path()
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(TEMPLATE)
    return path
