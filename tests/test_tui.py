from __future__ import annotations

import json

import httpx
import pytest
from textual.widgets import Input

from tclash.api import MihomoAPI
from tclash.settings import Settings
from tclash.tui.app import TclashApp
from tclash.tui.list import SpringList

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


def proxy_list(app: TclashApp) -> SpringList:
    return app.query_one("#proxies", SpringList)


def names(app: TclashApp) -> list[str]:
    return [item.key for item in proxy_list(app).items]


def marks(app: TclashApp) -> list[str]:
    group = app.current_group
    now = group.now if group else None
    return ["▶" if item.key == now else " " for item in proxy_list(app).items]


def group_list(app: TclashApp) -> SpringList:
    return app.query_one("#groups", SpringList)


@pytest.mark.asyncio
async def test_list_shows_members_and_hides_noise() -> None:
    async with TclashApp(Settings(), api=make_api([])).run_test() as pilot:
        await pilot.pause()
        assert names(pilot.app) == ["香港01", "日本01"]  # DIRECT 与“剩余流量”被隐藏
        assert marks(pilot.app) == ["▶", " "]

        # z 显示隐藏节点
        await pilot.press("z")
        await pilot.pause()
        assert len(names(pilot.app)) == 3


@pytest.mark.asyncio
async def test_select_current_row_sends_put() -> None:
    calls: list[httpx.Request] = []
    async with TclashApp(Settings(), api=make_api(calls)).run_test() as pilot:
        await pilot.pause()
        proxy_list(pilot.app).move_to(1)  # 日本01
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        puts = [r for r in calls if r.method == "PUT"]
        assert puts, "select 没有发出 PUT 请求"
        assert puts[-1].url.path == "/proxies/主组"
        assert json.loads(puts[-1].content) == {"name": "日本01"}


@pytest.mark.asyncio
async def test_filter_narrows_rows() -> None:
    async with TclashApp(Settings(), api=make_api([])).run_test() as pilot:
        await pilot.pause()
        pilot.app.query_one("#filter", Input).value = "日本"
        await pilot.pause()
        assert names(pilot.app) == ["日本01"]


@pytest.mark.asyncio
async def test_group_switch_changes_list() -> None:
    async with TclashApp(Settings(), api=make_api([])).run_test() as pilot:
        await pilot.pause()
        assert group_list(pilot.app).cursor == 0
        group_list(pilot.app).move_to(1)  # 自动（URLTest）组
        await pilot.pause()
        assert pilot.app.group_name == "自动"
        assert names(pilot.app) == ["香港01", "日本01"]
        assert marks(pilot.app) == [" ", "▶"]


@pytest.mark.asyncio
async def test_test_group_updates_delays() -> None:
    async with TclashApp(Settings(), api=make_api([])).run_test() as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        await pilot.pause()
        assert pilot.app.delays["香港01"] == 111
        row = pilot.app.render_row(pilot.app.by_name["香港01"], 60, 0.0)
        assert "111ms" in row.plain


@pytest.mark.asyncio
async def test_vim_keys_navigate_rows_and_groups() -> None:
    async with TclashApp(Settings(), api=make_api([])).run_test() as pilot:
        await pilot.pause()
        assert proxy_list(pilot.app).cursor == 0
        await pilot.press("j")
        assert proxy_list(pilot.app).cursor == 1
        await pilot.press("j")  # 到底后回到第一行
        assert proxy_list(pilot.app).cursor == 0
        await pilot.press("G")
        assert proxy_list(pilot.app).cursor == 1
        await pilot.press("g")
        assert proxy_list(pilot.app).cursor == 0

        await pilot.press("l")
        await pilot.pause()
        assert group_list(pilot.app).cursor == 1
        assert pilot.app.group_name == "自动"
        await pilot.press("h")
        await pilot.pause()
        assert pilot.app.group_name == "主组"


@pytest.mark.asyncio
async def test_test_only_probes_filtered_rows() -> None:
    calls: list[httpx.Request] = []
    async with TclashApp(Settings(), api=make_api(calls)).run_test() as pilot:
        await pilot.pause()
        pilot.app.query_one("#filter", Input).value = "香港"
        await pilot.pause()
        calls.clear()
        await pilot.press("t")
        await pilot.pause()
        await pilot.pause()
        probed = sorted(r.url.path for r in calls if r.url.path.endswith("/delay"))
        assert probed == ["/proxies/香港01/delay"]


@pytest.mark.asyncio
async def test_theme_cycles_and_is_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Settings, "save", lambda self, path=None: None)
    async with TclashApp(Settings(theme="ansi-dark"), api=make_api([])).run_test() as pilot:
        await pilot.pause()
        assert pilot.app.theme == "ansi-dark"
        await pilot.press("ctrl+t")
        assert pilot.app.theme == "ansi-light"
        assert pilot.app.settings.theme == "ansi-light"


@pytest.mark.asyncio
async def test_help_screen_opens() -> None:
    from tclash.tui.help import HelpScreen

    async with TclashApp(Settings(), api=make_api([])).run_test() as pilot:
        await pilot.pause()
        await pilot.press("?")
        await pilot.pause()
        assert isinstance(pilot.app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, HelpScreen)
