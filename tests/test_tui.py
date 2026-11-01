from __future__ import annotations

import json

import httpx
import pytest
from textual.widgets import DataTable, Input, ListView

from tclash.api import MihomoAPI
from tclash.settings import Settings
from tclash.tui.app import TclashApp

def column(table: DataTable, key: str) -> list[str]:
    return [str(cell) for cell in table.get_column(key)]


PROXIES = {
    "proxies": {
        "DIRECT": {"type": "Direct", "alive": True},
        "剩余流量：999 GB": {"type": "Hysteria2", "alive": True},
        "香港01": {"type": "Hysteria2", "alive": True, "history": [{"delay": 88}]},
        "日本01": {"type": "Hysteria2", "alive": True, "history": [{"delay": 300}]},
        "主组": {"type": "Selector", "now": "香港01", "all": ["香港01", "日本01", "DIRECT"], "history": []},
        "自动": {"type": "URLTest", "now": "日本01", "all": ["香港01", "日本01"], "history": []},
    }
}


def make_api(calls: list[httpx.Request]) -> MihomoAPI:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/version":
            return httpx.Response(200, json={"version": "v1.19.33"})
        if path == "/configs":
            return httpx.Response(200, json={"mode": "rule"})
        if path == "/proxies":
            return httpx.Response(200, json=PROXIES)
        if path.endswith("/delay"):
            return httpx.Response(200, json={"delay": 111})
        if request.method == "PUT":
            return httpx.Response(204)
        return httpx.Response(404)

    client = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handler))
    return MihomoAPI("http://test", client=client)


@pytest.mark.asyncio
async def test_table_lists_group_members_and_hides_noise() -> None:
    calls: list[httpx.Request] = []
    async with TclashApp(Settings(), api=make_api(calls)).run_test() as pilot:
        await pilot.pause()
        table = pilot.app.query_one("#proxies", DataTable)
        names = column(table, "name")
        assert list(names) == ["香港01", "日本01"]  # DIRECT 与“剩余流量”被隐藏
        marks = list(column(table, "mark"))
        assert marks == ["▶", " "]

        # h 显示隐藏节点
        await pilot.press("h")
        await pilot.pause()
        assert table.row_count == 3


@pytest.mark.asyncio
async def test_select_current_row_sends_put() -> None:
    calls: list[httpx.Request] = []
    async with TclashApp(Settings(), api=make_api(calls)).run_test() as pilot:
        await pilot.pause()
        table = pilot.app.query_one("#proxies", DataTable)
        table.move_cursor(row=1)  # 日本01
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        puts = [r for r in calls if r.method == "PUT"]
        assert puts, "select 没有发出 PUT 请求"
        assert puts[-1].url.path == "/proxies/主组"
        assert json.loads(puts[-1].content) == {"name": "日本01"}


@pytest.mark.asyncio
async def test_filter_narrows_rows() -> None:
    calls: list[httpx.Request] = []
    async with TclashApp(Settings(), api=make_api(calls)).run_test() as pilot:
        await pilot.pause()
        table = pilot.app.query_one("#proxies", DataTable)
        pilot.app.query_one("#filter", Input).value = "日本"
        await pilot.pause()
        assert list(column(table, "name")) == ["日本01"]


@pytest.mark.asyncio
async def test_group_switch_changes_table() -> None:
    calls: list[httpx.Request] = []
    async with TclashApp(Settings(), api=make_api(calls)).run_test() as pilot:
        await pilot.pause()
        lv = pilot.app.query_one("#groups", ListView)
        assert lv.index == 0
        lv.index = 1  # 自动（URLTest）组
        await pilot.pause()
        table = pilot.app.query_one("#proxies", DataTable)
        assert list(column(table, "name")) == ["香港01", "日本01"]
        assert list(column(table, "mark")) == [" ", "▶"]


@pytest.mark.asyncio
async def test_test_group_updates_delays() -> None:
    calls: list[httpx.Request] = []
    async with TclashApp(Settings(), api=make_api(calls)).run_test() as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        await pilot.pause()
        table = pilot.app.query_one("#proxies", DataTable)
        assert set(column(table, "delay")) == {"111ms"}
        assert pilot.app.delays["香港01"] == 111
