"""code mode 文本处理：去 ANSI 转义、跳过代码噪音、拆标识符。"""

import re
from dataclasses import dataclass

# CSI（颜色、光标）、OSC（标题、超链接、shell integration 标记）、其他两字节 ESC 序列
ANSI_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
)

# 整段跳过、不参与查词的片段。
# URL、邮箱、相对路径、文件名的开头字符类能吞下很长一段，所以用负向后顾把起点限制在 token 左边界：
# 否则长 token（base64、压缩成一行的输出）里每个字符都会重新扫到末尾再回溯，耗时随长度平方增长。
SKIP_RE = re.compile(
    r"""
      (?<![a-zA-Z0-9+.-])[0-9+.-]*           # URL：scheme 前紧贴的符号和数字一并跳过（diff 里的 +https://）
          [a-zA-Z][a-zA-Z0-9+.-]*://\S+
    | (?<![\w.+-])[\w.+-]+@[\w-]+\.[\w.-]+   # 邮箱
    | (?:~|\.{1,2})?/[\w.@~/-]*              # 路径 /a/b ./x ~/y
    | (?<![\w.-])[\w.-]+(?:/[\w.@-]+)+       # 相对路径 src/foo.ts
    | (?<![\w-])(?!--?[a-zA-Z])-*            # 文件名 / 域名 foo.py；开头像 flag 的（-o.txt）留给下一条
          \b[\w-]+\.(?:[a-z]{1,4})\b
    | (?<![\w-])--?[a-zA-Z][\w-]*            # 命令行 flag
    | \b0x[0-9a-fA-F]+\b                     # 十六进制
    | \b[0-9a-f]{7,}\b                       # commit hash 等
    | \bv?\d+(?:\.\d+)+\S*                   # 版本号
    | \$\{?\w+\}?                            # 变量 $HOME ${FOO}
          (?:(?<=\w)(?: [\w.+-]+@[\w-]+\.[\w.-]+    # 变量名后面紧跟的邮箱、路径、文件名不在左边界上，
                      | [\w.-]+(?:/[\w.@-]+)+       # 在这里接着跳过：$dir-old/x  $name-v2.txt
                      | [\w-]+\.(?:[a-z]{1,4})\b ))?
    | `[^`]*`                                # 行内代码
    | (?<!\w)'[^'\s]{1,40}'                 # 单引号包住的数据 'abc'
    | \b\w+'(?:t|s|ll|re|ve|d|m)\b           # 缩写 isn't I'll
    """,
    re.VERBOSE,
)

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_'-]*")
# 拆 camelCase / PascalCase / HTTPServer
CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+")


@dataclass
class Word:
    text: str      # 原文中的片段（用于定位高亮）
    norm: str      # 小写，用于查词
    start: int     # 在去 ANSI 后文本中的偏移
    end: int


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def _mask(text: str) -> str:
    return SKIP_RE.sub(lambda m: " " * len(m.group()), text)


def extract_words(text: str, min_len: int = 3) -> list[Word]:
    """从一行（已去 ANSI）文本里取出待查的英文词，含拆分后的标识符子词。"""
    masked = _mask(text)
    out: list[Word] = []
    for m in TOKEN_RE.finditer(masked):
        tok = m.group().strip("'-")
        if not tok:
            continue
        base = m.start() + m.group().index(tok)
        is_ident = "_" in tok or "-" in tok or bool(re.search(r"[a-z][A-Z]|[A-Z]{2}[a-z]", tok)) or any(c.isdigit() for c in tok)
        if not is_ident:
            # 全大写短词基本是缩写（HTTP、EOF、TODO），跳过
            if tok.isupper() and len(tok) <= 5:
                continue
            if len(tok) >= min_len:
                out.append(Word(tok, tok.lower(), base, base + len(tok)))
            continue
        for pm in re.finditer(r"[A-Za-z]+", tok):
            for sub in CAMEL_RE.finditer(pm.group()):
                s = sub.group()
                if len(s) < min_len or (s.isupper() and len(s) <= 5):
                    continue
                st = base + pm.start() + sub.start()
                out.append(Word(s, s.lower(), st, st + len(s)))
    return out
