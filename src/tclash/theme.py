"""终端配色探测与主题选择。

优先顺序：环境变量 → ``COLORFGBG`` → OSC 11 查询 → 默认深色。
``ansi-dark`` / ``ansi-light`` 直接使用终端自己的 16 色，所以无论用什么终端配色，
界面都会跟着走。
"""

from __future__ import annotations

import os
import re
import select
import sys
import termios
import time
import tty

FALLBACK_BACKGROUND = "dark"

# ctrl+t 循环的主题
THEME_CYCLE = [
    "ansi-dark",
    "ansi-light",
    "textual-dark",
    "textual-light",
    "catppuccin-mocha",
    "tokyo-night",
    "gruvbox",
    "nord",
]

OSC11 = b"\x1b]11;?\x07"
_RGB = re.compile(r"rgb:([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})")


def detect_background() -> str:
    """返回 ``"dark"`` 或 ``"light"``。"""
    forced = os.environ.get("TCLASH_BACKGROUND", "").strip().lower()
    if forced in ("dark", "light"):
        return forced

    fgbg = os.environ.get("COLORFGBG", "")
    if fgbg:
        try:
            index = int(fgbg.split(";")[-1])
        except ValueError:
            index = -1
        if 0 <= index <= 6 or index == 8:
            return "dark"
        if index == 7 or index >= 9:
            return "light"

    rgb = _query_osc11()
    if rgb is not None:
        return "light" if _luminance(rgb) > 0.5 else "dark"

    return FALLBACK_BACKGROUND


def resolve_theme(preference: str) -> str:
    """把配置里的主题偏好（可能是 ``auto``）解析成实际主题名。"""
    pref = (preference or "auto").strip()
    if pref in ("", "auto"):
        return f"ansi-{detect_background()}"
    return pref


def next_theme(current: str) -> str:
    try:
        return THEME_CYCLE[(THEME_CYCLE.index(current) + 1) % len(THEME_CYCLE)]
    except ValueError:
        return THEME_CYCLE[0]


def _luminance(rgb: tuple[float, float, float]) -> float:
    red, green, blue = rgb
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _query_osc11(timeout: float = 0.15) -> tuple[float, float, float] | None:
    """问终端要背景色。不是真终端、终端不支持或超时就返回 None。"""
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return None
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return None

    try:
        try:
            saved = termios.tcgetattr(fd)
        except termios.error:
            return None
        try:
            tty.setraw(fd)
            os.write(fd, OSC11)
            reply = b""
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                ready, _, _ = select.select([fd], [], [], remaining)
                if not ready:
                    break
                chunk = os.read(fd, 64)
                if not chunk:
                    break
                reply += chunk
                if reply.endswith(b"\x07") or reply.endswith(b"\x1b\\"):
                    break
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    finally:
        os.close(fd)

    match = _RGB.search(reply.decode("ascii", "ignore"))
    if not match:
        return None
    scale = 65535.0
    return tuple(int(part, 16) / scale for part in match.groups())  # type: ignore[return-value]
