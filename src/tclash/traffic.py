"""流量采样：把 `/connections` 的累计字节差分成实时速度，并记录一段历史画迷你曲线。

mihomo 的 `/traffic` 是 websocket，textual 这边不想为了一个数字再拉一套 ws 依赖，
所以直接 1 秒轮询 `/connections`：它本来就带 `downloadTotal` / `uploadTotal`，
顺带还能拿到连接数，比 /traffic 的信息更多。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

SPARK = "▁▂▃▄▅▆▇█"
"""迷你曲线用的方块字符，从矮到高 8 档。"""

SMOOTHING = 0.55
"""指数平滑系数：越小越稳，1 就是原始值。轮询抖动时不至于跳成一片乱码。"""


def format_speed(bytes_per_second: float) -> str:
    """人类可读的速度，固定 3 段（`1.2 MB/s`），方便对齐。"""
    value = max(0.0, float(bytes_per_second))
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if value < 1024 or unit == "GB/s":
            if unit == "B/s":
                return f"{value:.0f} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB/s"


def sparkline(values: list[float], width: int) -> str:
    """把最近若干个采样压成一行方块。空数据返回等宽空白，保持布局不跳。"""
    if width <= 0:
        return ""
    if not values:
        return " " * width
    recent = values[-width:]
    peak = max(recent) or 1.0
    chars = []
    for value in recent:
        level = min(len(SPARK) - 1, int(value / peak * (len(SPARK) - 1)))
        chars.append(SPARK[max(0, level)])
    return "".join(chars).rjust(width)


@dataclass
class Connection:
    """一条活动连接（只留界面要用的字段）。"""

    id: str
    host: str
    destination: str
    rule: str
    chains: list[str]
    upload: int
    download: int
    started: str

    @property
    def total(self) -> int:
        return self.upload + self.download


def parse_connections(payload: dict) -> list[Connection]:
    items: list[Connection] = []
    for raw in payload.get("connections") or []:
        if not isinstance(raw, dict):
            continue
        meta = raw.get("metadata") or {}
        host = str(meta.get("host") or meta.get("destinationIP") or "?")
        port = meta.get("destinationPort")
        destination = f"{host}:{port}" if port else host
        items.append(
            Connection(
                id=str(raw.get("id") or ""),
                host=host,
                destination=destination,
                rule=f"{raw.get('rule', '')},{raw.get('rulePayload', '')}".strip(","),
                chains=[str(c) for c in (raw.get("chains") or [])],
                upload=int(raw.get("upload") or 0),
                download=int(raw.get("download") or 0),
                started=str(raw.get("start") or ""),
            )
        )
    items.sort(key=lambda c: c.total, reverse=True)
    return items


@dataclass
class TrafficMeter:
    """累计值 → 实时速度。`sample()` 幂等，同一秒重复调用不会算出假速度。"""

    down_speed: float = 0.0
    up_speed: float = 0.0
    down_total: int = 0
    up_total: int = 0
    connections: int = 0
    history: list[float] = field(default_factory=list)
    _last: tuple[int, int, float] | None = None
    _history_limit: int = 60

    def sample(self, payload: dict, *, now: float | None = None) -> None:
        stamp = time.monotonic() if now is None else now
        down = int(payload.get("downloadTotal") or 0)
        up = int(payload.get("uploadTotal") or 0)
        self.connections = len(payload.get("connections") or [])
        if self._last is None:
            # 第一次采样没有参照物，只记基线，速度留 0
            self._last = (down, up, stamp)
            self.down_total, self.up_total = down, up
            return

        last_down, last_up, last_stamp = self._last
        elapsed = stamp - last_stamp
        if elapsed < 0.2:
            return  # 手速按得太快 / 定时器抖动，忽略这次
        raw_down = max(0.0, (down - last_down) / elapsed)
        raw_up = max(0.0, (up - last_up) / elapsed)
        self.down_speed = SMOOTHING * raw_down + (1 - SMOOTHING) * self.down_speed
        self.up_speed = SMOOTHING * raw_up + (1 - SMOOTHING) * self.up_speed
        self.down_total, self.up_total = down, up
        self._last = (down, up, stamp)
        self.history.append(self.down_speed)
        del self.history[: max(0, len(self.history) - self._history_limit)]

    def reset(self) -> None:
        """重连内核后累计值归零，必须清掉基线，否则会出现负速度。"""
        self._last = None
        self.down_speed = self.up_speed = 0.0
        self.history.clear()
