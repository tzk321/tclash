"""Textual TUI 主界面。"""

from __future__ import annotations

import asyncio

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Input, Label, ListItem, ListView, Static

from ..api import MihomoAPI, MihomoError
from ..models import Proxy, delay_style, format_delay
from ..settings import Settings


def build_api(settings: Settings) -> MihomoAPI:
    return MihomoAPI(settings.controller, settings.secret, timeout=settings.timeout)


class TclashApp(App[None]):
    """代理列表 + 测速 + 切换。"""

    CSS_PATH = "style.tcss"
    TITLE = "tclash"
    SUB_TITLE = "mihomo 管理器"

    BINDINGS = [
        ("q", "quit", "退出"),
        ("r", "refresh", "刷新"),
        ("t", "test_group", "测速本组"),
        ("T", "test_all", "测速全部"),
        ("b", "best", "选最快"),
        ("s", "toggle_sort", "按延迟排序"),
        ("h", "toggle_hidden", "隐藏/显示信息节点"),
        ("/", "focus_filter", "过滤"),
        ("escape", "blur", "返回"),
    ]

    def __init__(self, settings: Settings, api: MihomoAPI | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.api = api or build_api(settings)
        self.proxies: list[Proxy] = []
        self.by_name: dict[str, Proxy] = {}
        self.groups: list[Proxy] = []
        self.delays: dict[str, int | None] = {}
        self.rows: list[str] = []
        self.group_name: str | None = None
        self.filter_text = ""
        self.show_hidden = False
        self.sort_by_delay = False
        self.testing = False
        self.version = "?"
        self.mode = "?"
        self.status = "启动中…"
        self.status_error = False

    # ------------------------------------------------------------- lifecycle
    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static("策略组", classes="panel-title")
                yield ListView(id="groups")
            with Vertical(id="main"):
                yield Static("节点 · Enter 切换 · t 测速", id="main-title", classes="panel-title")
                yield DataTable(id="proxies")
        yield Input(placeholder="过滤节点…（/ 聚焦，Esc 返回）", id="filter")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#proxies", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        self.render_topbar()
        await self.reload()
        table.focus()

    async def on_unmount(self) -> None:
        await self.api.aclose()

    # ----------------------------------------------------------------- state
    @property
    def current_group(self) -> Proxy | None:
        return self.by_name.get(self.group_name) if self.group_name else None

    def cursor_name(self) -> str | None:
        table = self.query_one("#proxies", DataTable)
        index = table.cursor_row
        if 0 <= index < len(self.rows):
            return self.rows[index]
        return None

    def set_status(self, message: str, *, error: bool = False) -> None:
        self.status = message
        self.status_error = error
        self.render_topbar()

    def render_topbar(self) -> None:
        bar = Text()
        bar.append(" tclash ", style="bold black on green")
        bar.append(" mihomo ", style="grey50")
        bar.append(f"v{self.version.lstrip('v')}  ", style="bold")
        bar.append("| 模式 ", style="grey50")
        bar.append(f"{self.mode}  ", style="bold cyan")
        bar.append("| 控制器 ", style="grey50")
        bar.append(f"{self.api.base}  ")
        bar.append("| 节点 ", style="grey50")
        bar.append(f"{len(self.proxies)}", style="bold")
        bar.append("\n")
        bar.append(" ", style="grey50")
        bar.append(self.status, style="red" if self.status_error else "grey62")

        table = self.query_one("#proxies", DataTable)
        sorting = " · 按延迟排序" if self.sort_by_delay else ""
        self.query_one("#main-title", Static).update(
            Text.assemble(
                ("节点", "bold"),
                f" · {len(self.rows)} 个",
                (f" · {self.group_name}" if self.group_name else "", "grey50"),
                (sorting, "grey50"),
                (" · Enter 切换 · t 测速 · b 选最快", "grey50"),
            )
        )
        self.query_one("#topbar", Static).update(bar)
        if table.row_count and self.group_name:
            table.border_title = self.group_name

    # ---------------------------------------------------------------- reload
    async def reload(self) -> None:
        try:
            self.version = await self.api.version()
            configs = await self.api.configs()
            probes = await self.api.proxies()
        except MihomoError as exc:
            self.set_status(str(exc), error=True)
            return

        self.proxies = probes
        self.by_name = {p.name: p for p in probes}
        self.mode = str(configs.get("mode", "?"))
        groups = [p for p in probes if p.is_group and p.name.upper() != "GLOBAL"]
        select_first = sorted(groups, key=lambda g: 0 if g.type.lower() == "selector" else 1)
        self.groups = select_first

        if self.group_name not in self.by_name:
            self.group_name = self.groups[0].name if self.groups else None

        self.set_status("已连接")
        await self.fill_groups()
        self.fill_table()

    async def fill_groups(self) -> None:
        lv = self.query_one("#groups", ListView)
        await lv.clear()
        index = 0
        for i, group in enumerate(self.groups):
            label = Text()
            label.append(group.name, style="bold" if group.name == self.group_name else "")
            if group.now:
                label.append(f"  → {group.now}", style="grey50")
            label.truncate(46, overflow="ellipsis")
            await lv.append(ListItem(Label(label, markup=False), name=group.name))
            if group.name == self.group_name:
                index = i
        if self.groups:
            lv.index = index

    def fill_table(self) -> None:
        table = self.query_one("#proxies", DataTable)
        previous = self.cursor_name()
        table.clear(columns=True)
        table.add_columns(("", "mark"), ("节点", "name"), ("类型", "type"), ("延迟", "delay"))

        group = self.current_group
        self.rows = []
        if group is None:
            self.render_topbar()
            return

        needle = self.filter_text.strip().casefold()
        entries: list[Proxy] = []
        for member in group.members:
            proxy = self.by_name.get(member)
            if proxy is None:
                proxy = Proxy(name=member, type="?", members=())
            entries.append(proxy)

        if not self.show_hidden:
            entries = [p for p in entries if not p.is_info and not p.is_builtin]
        if needle:
            entries = [p for p in entries if needle in p.name.casefold()]
        if self.sort_by_delay:
            entries.sort(key=lambda p: (self.delay_of(p) is None, self.delay_of(p) or 0))

        for proxy in entries:
            delay = self.delay_of(proxy)
            marker = "▶" if group.now == proxy.name else " "
            table.add_row(
                Text(marker, style="bold green"),
                Text(proxy.name),
                Text(proxy.type, style="grey50"),
                Text(format_delay(delay), style=delay_style(delay)),
                key=proxy.name,
            )
            self.rows.append(proxy.name)

        if previous in self.rows:
            table.move_cursor(row=self.rows.index(previous))
        elif self.rows:
            table.move_cursor(row=0)
        self.render_topbar()

    def delay_of(self, proxy: Proxy) -> int | None:
        return self.delays.get(proxy.name, proxy.delay)

    def update_row(self, name: str) -> None:
        if name not in self.rows:
            return
        delay = self.delays.get(name)
        try:
            self.query_one("#proxies", DataTable).update_cell(
                name, "delay", Text(format_delay(delay), style=delay_style(delay))
            )
        except Exception:  # noqa: BLE001 - 行可能已被过滤掉
            pass

    # --------------------------------------------------------------- actions
    async def action_refresh(self) -> None:
        await self.reload()

    async def action_toggle_sort(self) -> None:
        self.sort_by_delay = not self.sort_by_delay
        self.delays = {k: v for k, v in self.delays.items()}
        self.fill_table()

    def action_toggle_hidden(self) -> None:
        self.show_hidden = not self.show_hidden
        self.fill_table()
        self.set_status("显示内置/信息节点" if self.show_hidden else "已隐藏内置/信息节点")

    def action_focus_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_blur(self) -> None:
        self.query_one("#proxies", DataTable).focus()

    def action_test_group(self) -> None:
        self.start_test(hidden=False)

    def action_test_all(self) -> None:
        self.start_test(hidden=True)

    def action_best(self) -> None:
        self.auto_best()

    def start_test(self, *, hidden: bool) -> None:
        group = self.current_group
        if group is None:
            return
        names = [
            n
            for n in group.members
            if (hidden or not self.is_hidden(self.by_name.get(n), n))
        ]
        if not names:
            self.set_status("这个组没有可测速的节点", error=True)
            return
        self.test_names(names)

    def is_hidden(self, proxy: Proxy | None, name: str) -> bool:
        if proxy is None:
            return False
        return (not self.show_hidden) and (proxy.is_info or proxy.is_builtin)

    @work(exclusive=True, group="delay-test")
    async def test_names(self, names: list[str]) -> None:
        self.testing = True
        total = len(names)
        done = 0
        self.set_status(f"测速中 0/{total} …")

        def on_result(name: str, delay: int | None) -> None:
            nonlocal done
            done += 1
            self.delays[name] = delay
            self.update_row(name)
            self.set_status(f"测速中 {done}/{total} …")

        try:
            await self.api.test_many(
                names,
                url=self.settings.test_url,
                timeout_ms=self.settings.test_timeout_ms,
                concurrency=self.settings.concurrency,
                on_result=on_result,
            )
        except MihomoError as exc:
            self.testing = False
            self.set_status(str(exc), error=True)
            return
        self.testing = False
        self.set_status(f"测速完成：{total} 个节点")
        if self.sort_by_delay:
            self.fill_table()

    @work(exclusive=True, group="auto-best")
    async def auto_best(self) -> None:
        group = self.current_group
        if group is None:
            return
        names = [n for n in group.members if not self.is_hidden(self.by_name.get(n), n)]
        if not names:
            self.set_status("这个组没有可测速的节点", error=True)
            return
        self.set_status(f"测速选最快：{len(names)} 个节点…")
        try:
            results = await self.api.test_many(
                names,
                url=self.settings.test_url,
                timeout_ms=self.settings.test_timeout_ms,
                concurrency=self.settings.concurrency,
            )
        except MihomoError as exc:
            self.set_status(str(exc), error=True)
            return
        self.delays.update(results)
        reachable = {n: d for n, d in results.items() if d is not None}
        if not reachable:
            self.set_status("全部节点测速失败", error=True)
            self.fill_table()
            return
        best = min(reachable, key=lambda n: reachable[n] or 10**9)
        await self._select(best, f"最快：{best} {reachable[best]}ms")

    # -------------------------------------------------------------- select
    @on(DataTable.RowSelected)
    async def on_row_selected(self, event: DataTable.RowSelected) -> None:
        name = str(event.row_key.value)
        await self._select(name, f"已切换到 {name}")

    async def _select(self, name: str, message: str) -> None:
        group = self.group_name
        if not group:
            return
        try:
            await self.api.select(group, name)
        except MihomoError as exc:
            self.set_status(str(exc), error=True)
            return
        current = self.by_name.get(group)
        if current is not None:
            current.now = name
        self.set_status(message)
        await self.fill_groups()
        self.fill_table()

    @on(ListView.Highlighted, "#groups")
    def on_group_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        name = event.item.name if isinstance(event.item.name, str) else None
        if not name or name == self.group_name:
            return
        self.group_name = name
        self.fill_table()

    @on(Input.Changed, "#filter")
    def on_filter_changed(self, event: Input.Changed) -> None:
        self.filter_text = event.value
        self.fill_table()

    @on(Input.Submitted, "#filter")
    def on_filter_submitted(self) -> None:
        self.query_one("#proxies", DataTable).focus()
