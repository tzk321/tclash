from __future__ import annotations

import json
import time

import httpx
import pytest
from textual.widgets import Input

from tclash import subscription as sub
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


# ------------------------------------------------------------------ 订阅
@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    """所有 TUI 测试都跑在 tmp 配置目录里，绝不读真实订阅表（防止误写 /etc）。"""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path


def write_profiles(**entries: dict) -> None:
    """写一份订阅表。默认 updated_at=现在，免得每次启动都触发自动更新。"""
    from tclash.subscription import Profile, Profiles

    profiles = Profiles(active=next(iter(entries), ""))
    for name, extra in entries.items():
        extra = {"updated_at": int(time.time()), **extra}
        profiles.items[name] = Profile(name=name, url=f"http://sub/{name}", **extra)
    profiles.save()


def fake_update(seen: list[tuple[str, bool]], *, fail: bool = False):
    from tclash.subscription import Result

    async def run(profiles, profile, settings, *, reload=True, dry_run=False, api=None):
        seen.append((profile.name, dry_run))
        if fail:
            raise sub.SubError("拉取失败：连接超时")
        return Result(profile=profile, steps=[f"已写入 {settings.config_path}"])

    return run


@pytest.mark.asyncio
async def test_profiles_key_without_subscriptions(monkeypatch) -> None:
    monkeypatch.setattr(sub, "update", fake_update([]))
    async with TclashApp(Settings(), api=make_api([])).run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        assert pilot.app.screen is pilot.app.screen_stack[0]  # 没弹窗
        assert "还没有订阅" in pilot.app.status
        assert pilot.app.status_error


@pytest.mark.asyncio
async def test_profile_modal_switches_subscription(monkeypatch) -> None:
    from tclash.tui.profiles import ProfileScreen

    write_profiles(**{"一分机场": {"node_count": 42, "total": 100, "download": 40}, "备用": {}})
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(sub, "update", fake_update(seen))

    async with TclashApp(Settings(), api=make_api([])).run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, ProfileScreen)
        rows = screen.query_one("#profile-list", SpringList)
        assert [item.key for item in rows.items] == ["一分机场", "备用"]
        assert rows.cursor_key == "一分机场"

        rows.move_to(1)  # 备用
        await pilot.pause()
        await pilot.press("enter")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()

        assert isinstance(pilot.app.screen, ProfileScreen) is False
        assert seen == [("备用", False)]
        assert "订阅已更新" in pilot.app.status
        assert not pilot.app.status_error


@pytest.mark.asyncio
async def test_update_key_pulls_active_profile(monkeypatch) -> None:
    write_profiles(**{"一分机场": {}, "备用": {}})
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(sub, "update", fake_update(seen))

    async with TclashApp(Settings(), api=make_api([])).run_test() as pilot:
        await pilot.pause()
        await pilot.press("u")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        assert seen == [("一分机场", False)]


@pytest.mark.asyncio
async def test_subscription_failure_sets_error_status(monkeypatch) -> None:
    write_profiles(**{"一分机场": {}})
    monkeypatch.setattr(sub, "update", fake_update([], fail=True))

    async with TclashApp(Settings(), api=make_api([])).run_test() as pilot:
        await pilot.pause()
        await pilot.press("u")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        assert "订阅失败" in pilot.app.status
        assert "连接超时" in pilot.app.status
        assert pilot.app.status_error


@pytest.mark.asyncio
async def test_auto_update_when_due(monkeypatch) -> None:
    # updated_at 为空 = 从未更新 → due，启动就该自动拉一次
    write_profiles(**{"一分机场": {"interval": 24, "updated_at": None}})
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(sub, "update", fake_update(seen))

    async with TclashApp(Settings(), api=make_api([])).run_test() as pilot:
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        assert seen == [("一分机场", False)]


@pytest.mark.asyncio
async def test_no_auto_update_when_fresh(monkeypatch) -> None:
    write_profiles(**{"一分机场": {"interval": 24, "updated_at": int(time.time())}})
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(sub, "update", fake_update(seen))

    async with TclashApp(Settings(), api=make_api([])).run_test() as pilot:
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        assert seen == []
