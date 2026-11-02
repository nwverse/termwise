"""通过 Ghostty 1.3 的 AppleScript 字典拿到当前终端的屏幕 / scrollback 文本。

做法：对 front window 当前 tab 的 focused terminal 执行 action
`write_screen_file:copy`（或 write_scrollback_file / write_selection_file），
Ghostty 把内容写进临时文件并把路径放进剪贴板；读完再把剪贴板还原。
首次运行 macOS 会弹「允许控制 Ghostty」的自动化授权（系统设置 → 隐私与安全性 → 自动化）。
已知限制：只能保存 / 还原纯文本剪贴板，剪贴板里是图片等非文本内容时会被清掉。
"""

import os
import subprocess
import time

TARGETS = {"screen": "write_screen_file", "scrollback": "write_scrollback_file",
           "selection": "write_selection_file"}

_SCRIPT = '''
tell application "Ghostty"
  set t to focused terminal of selected tab of front window
  perform action "{action}:copy,plain" on t
end tell
'''


def _pbpaste() -> str:
    return subprocess.run(["pbpaste"], capture_output=True, text=True).stdout


def _pbcopy(s: str):
    subprocess.run(["pbcopy"], input=s, text=True)


def capture(target: str = "screen", timeout: float = 2.0) -> str:
    action = TARGETS[target]
    saved = _pbpaste()
    _pbcopy("")  # 用来判断 Ghostty 是否已写入新路径
    try:
        try:
            r = subprocess.run(["osascript", "-e", _SCRIPT.format(action=action)],
                               capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired:
            raise RuntimeError("调用 Ghostty 超时：请在弹出的「自动化」授权框里允许，"
                               "或到 系统设置 → 隐私与安全性 → 自动化 中打开") from None
        if r.returncode != 0:
            raise RuntimeError(f"osascript 失败：{r.stderr.strip()}")
        path, deadline = "", time.time() + timeout
        while time.time() < deadline:
            path = _pbpaste().strip()
            if path:
                break
            time.sleep(0.05)
        if not path or not os.path.isfile(path):
            raise RuntimeError(
                "Ghostty 没有写出文件（selection 模式下需要先选中文本）" if target == "selection"
                else f"Ghostty 没有写出文件：{path!r}"
            )
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    finally:
        _pbcopy(saved)
