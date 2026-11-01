"""拼音 / 双拼搜索。"""

from __future__ import annotations

import pytest
from textual.widgets import Input

from tclash.search import flypy_syllable, matches, search_keys
from tclash.settings import Settings
from tclash.tui.app import TclashApp

from test_tui import make_api, names


@pytest.mark.parametrize(
    ("syllable", "keys"),
    [
        ("ri", "ri"),
        ("ben", "bf"),
        ("mei", "mw"),
        ("guo", "go"),
        ("zhong", "vs"),
        ("shang", "uh"),
        ("xiang", "xl"),
        ("guang", "gl"),
        ("xin", "xb"),
        ("yue", "yt"),
        ("ping", "pk"),
        ("kuai", "kk"),
        ("miao", "mn"),
        ("shen", "uf"),
        ("shan", "uj"),
        ("rui", "rv"),
        ("ang", "ah"),
        ("a", "aa"),
    ],
)
def test_flypy_syllable(syllable: str, keys: str) -> None:
    assert flypy_syllable(syllable) == keys


@pytest.mark.parametrize(
    ("name", "queries"),
    [
        ("日本01", ["日本", "riben", "riben01", "ribf", "rb", "rib", "01"]),
        ("美国 01", ["美国", "meiguo", "mwgo", "mg"]),
        ("香港 HK-01", ["香港", "xianggang", "xlgh", "hk"]),
        ("新加坡", ["xinjiapo", "xbjxpo", "xjp"]),
        ("重庆", ["chongqing", "isqk"]),
        ("深圳", ["shenzhen", "ufvf"]),
        ("IEPL 专线", ["iepl", "zx"]),
    ],
)
def test_matches(name: str, queries: list[str]) -> None:
    for query in queries:
        assert matches(query, name), f"{query!r} 应该命中 {name!r}"


def test_misses() -> None:
    assert not matches("ribf", "香港01")
    assert not matches("mwgo", "日本01")


def test_keys_keep_latin_untouched() -> None:
    """英文/数字不该被双拼规则改写（airport 里的 ai 不能变成 ad）。"""
    assert search_keys("airport 01")[2] == "airport 01"


@pytest.mark.asyncio
async def test_filter_accepts_pinyin_and_flypy() -> None:
    async with TclashApp(Settings(), api=make_api([])).run_test() as pilot:
        await pilot.pause()
        field = pilot.app.query_one("#filter", Input)
        for query in ("riben", "ribf", "rb", "日本"):
            field.value = query
            await pilot.pause()
            assert names(pilot.app) == ["日本01"], query
        field.value = "xlgh"
        await pilot.pause()
        assert names(pilot.app) == ["香港01"]
