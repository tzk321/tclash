from __future__ import annotations

import pytest

from tclash.models import Proxy, delay_style, format_delay, parse_proxies

SAMPLE = {
    "proxies": {
        "DIRECT": {"type": "Direct", "alive": True, "history": []},
        "剩余流量：784.16 GB": {"type": "Hysteria2", "alive": True, "history": []},
        "日本线": {
            "type": "Hysteria2",
            "alive": True,
            "udp": True,
            "history": [{"time": "t", "delay": 0}, {"time": "t2", "delay": 233}],
        },
        "一分机场": {
            "type": "Selector",
            "alive": True,
            "now": "日本线",
            "all": ["日本线", "DIRECT"],
            "history": [],
        },
    }
}


def test_parse_basic_fields() -> None:
    by_name = {p.name: p for p in parse_proxies(SAMPLE)}
    assert by_name["日本线"].delay == 233  # 取最近一次非零延迟
    assert by_name["日本线"].udp is True
    assert by_name["一分机场"].now == "日本线"
    assert by_name["一分机场"].is_group is True
    assert by_name["日本线"].is_group is False


def test_hidden_nodes() -> None:
    by_name = {p.name: p for p in parse_proxies(SAMPLE)}
    assert by_name["DIRECT"].is_builtin is True
    assert by_name["剩余流量：784.16 GB"].is_info is True


def test_group_detected_without_all() -> None:
    assert Proxy(name="x", type="URLTest").is_group is True
    assert Proxy(name="x", type="Direct").is_group is False


@pytest.mark.parametrize(
    ("delay", "style"),
    [(None, "grey50"), (50, "green"), (300, "yellow"), (700, "orange3"), (2000, "red")],
)
def test_delay_style(delay: int | None, style: str) -> None:
    assert delay_style(delay) == style


def test_format_delay() -> None:
    assert format_delay(None) == "—"
    assert format_delay(123) == "123ms"
