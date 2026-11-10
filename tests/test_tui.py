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


# --------------------------------------------------- 流量 / 连接 / 规则 / 模式
CONNECTIONS = {
    "downloadTotal": 10_000,
    "uploadTotal": 2_000,
    "connections": [
        {
            "id": "a",
            "metadata": {"host": "example.com", "destinationPort": "443"},
            "rule": "DomainSuffix",
            "rulePayload": "example.com",
            "chains": ["主组", "香港01"],
            "upload": 100,
            "download": 4000,
            "start": "2026-11-02T00:00:00Z",
        },
        {
            "id": "b",
            "metadata": {"destinationIP": "1.1.1.1", "destinationPort": "53"},
            "rule": "MATCH",
            "rulePayload": "",
            "chains": ["DIRECT"],
            "upload": 10,
            "download": 20,
            "start": "2026-11-02T00:00:01Z",
        },
    ],
}
RULES = {
    "rules": [
        {"type": "DomainSuffix", "payload": "example.com", "proxy": "主组"},
        {"type": "MATCH", "payload": "", "proxy": "主组"},
    ]
}


def make_rich_api(calls: list[httpx.Request]) -> MihomoAPI:
    """比 make_api 多认识 /connections、/rules 和 PATCH /configs。"""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/version":
            return httpx.Response(200, json={"version": "v1.19.33"})
        if path == "/configs":
            return httpx.Response(200, json={"mode": "rule"})
        if path == "/proxies":
            return httpx.Response(200, json=PROXIES)
        if path == "/connections":
            return httpx.Response(200, json=CONNECTIONS)
        if path == "/rules":
            return httpx.Response(200, json=RULES)
        if request.method in ("PUT", "PATCH"):
            return httpx.Response(204)
        return httpx.Response(404)

    client = httpx.AsyncClient(base_url="http://test", transport=httpx.MockTransport(handler))
    return MihomoAPI("http://test", client=client)


async def test_traffic_poll_updates_topbar(monkeypatch: pytest.MonkeyPatch) -> None:
    from textual.widgets import Static

    calls: list[httpx.Request] = []
    app = TclashApp(Settings(), api=make_rich_api(calls))
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.poll_traffic()
        await pilot.pause()
        assert app.meter.connections == 2
        topbar = app.query_one("#topbar", Static)
        text = topbar.content.plain
        assert "↓" in text and "↑" in text
        assert "2 连接" in text


async def test_connections_panel_lists_rows() -> None:
    from tclash.tui.panels import ConnectionsScreen

    app = TclashApp(Settings(), api=make_rich_api([]))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ConnectionsScreen)
        await screen.workers.wait_for_complete()
        await pilot.pause()
        rows = [item.key for item in screen.query_one("#conn-list", SpringList).items]
        assert rows == ["a", "b"]  # 按流量降序
        head = screen.query_one("#conn-head").content.plain
        assert "↓" in head and "累计" in head
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ConnectionsScreen)


async def test_rules_panel_lists_rules() -> None:
    from tclash.tui.panels import RulesScreen

    app = TclashApp(Settings(), api=make_rich_api([]))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("w")
        await app.workers.wait_for_complete()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, RulesScreen)
        rows = [item.key for item in screen.query_one("#rules-list", SpringList).items]
        assert rows == ["0:example.com", "1:"]
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, RulesScreen)


async def test_mode_key_cycles_and_calls_kernel() -> None:
    calls: list[httpx.Request] = []
    app = TclashApp(Settings(), api=make_rich_api(calls))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        patches = [r for r in calls if r.method == "PATCH" and r.url.path == "/configs"]
        assert patches and json.loads(patches[-1].content)["mode"] == "global"
        assert app.mode == "global"
        assert "global" in app.status

        await pilot.press("m")
        await pilot.pause()
        assert app.mode == "direct"
        await pilot.press("m")
        await pilot.pause()
        assert app.mode == "rule"  # 转一圈回来


@pytest.mark.asyncio
async def test_tun_key_toggles_and_reports_boot(monkeypatch) -> None:
    from tclash import tun as tun_mod
    from tclash.tun import TunResult

    calls: list[tuple[bool, bool]] = []

    async def fake_set_tun(api, settings, enable, *, persist=True):  # noqa: ARG001
        calls.append((enable, persist))
        return TunResult(enabled=enable, runtime=enable, boot=True, steps=[])

    monkeypatch.setattr(tun_mod, "set_tun", fake_set_tun)
    async with TclashApp(Settings(), api=make_api([])).run_test() as pilot:
        await pilot.pause()
        assert pilot.app.tun_enable is False  # /configs 里没有 tun 字段
        await pilot.press("i")
        await pilot.pause()
        assert calls == [(True, True)]  # 默认连开机一起改
        assert pilot.app.tun_enable is True
        assert "开机" in pilot.app.status
        await pilot.press("i")
        await pilot.pause()
        assert calls[-1] == (False, True)
        assert pilot.app.tun_enable is False
