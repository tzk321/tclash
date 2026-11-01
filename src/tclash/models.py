"""数据模型：把 mihomo ``/proxies`` 的原始 JSON 转成友好对象。"""

from __future__ import annotations

from dataclasses import dataclass

# mihomo 的策略组类型
GROUP_TYPES = {"selector", "urltest", "fallback", "loadbalance", "relay"}

# 内置出站/占位节点，默认在 TUI 里隐藏
BUILTIN_NAMES = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL"}

# 机场塞进订阅里的“剩余流量/到期时间”伪节点，默认隐藏
INFO_KEYWORDS = ("剩余流量", "套餐到期", "到期时间", "流量重置", "过期时间", "官网")


@dataclass(slots=True)
class Proxy:
    """一个代理节点或策略组。"""

    name: str
    type: str
    now: str | None = None
    members: tuple[str, ...] = ()
    delay: int | None = None
    alive: bool = True
    udp: bool = False
    provider: str = ""

    @property
    def is_group(self) -> bool:
        return bool(self.members) or self.type.lower() in GROUP_TYPES

    @property
    def is_builtin(self) -> bool:
        return self.name.upper() in BUILTIN_NAMES

    @property
    def is_info(self) -> bool:
        """机场的流量/到期提示节点，没有实际连通性。"""
        return any(k in self.name for k in INFO_KEYWORDS)


def _last_delay(raw: dict) -> int | None:
    history = raw.get("history") or []
    for entry in reversed(history):
        delay = entry.get("delay")
        if isinstance(delay, int) and delay > 0:
            return delay
    return None


def parse_proxies(payload: dict) -> list[Proxy]:
    """把 ``GET /proxies`` 的 ``{"proxies": {...}}`` 解析成 Proxy 列表。"""
    result: list[Proxy] = []
    for name, raw in (payload.get("proxies") or {}).items():
        result.append(
            Proxy(
                name=name,
                type=str(raw.get("type", "")),
                now=raw.get("now") or None,
                members=tuple(raw.get("all") or ()),
                delay=_last_delay(raw),
                alive=bool(raw.get("alive", True)),
                udp=bool(raw.get("udp", False)),
                provider=str(raw.get("provider-name") or ""),
            )
        )
    return result


def delay_style(delay: int | None) -> str:
    """延迟分档配色。"""
    if delay is None:
        return "grey50"
    if delay <= 200:
        return "green"
    if delay <= 500:
        return "yellow"
    if delay <= 900:
        return "orange3"
    return "red"


def format_delay(delay: int | None) -> str:
    return "—" if delay is None else f"{delay}ms"
