"""Textual TUI 主界面。"""

from __future__ import annotations

from rich.cells import cell_len, set_cell_size
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from ..api import MihomoAPI, MihomoError
from ..models import Proxy, format_delay, group_badge, group_hint, type_label
from ..search import matches as search_matches
from ..settings import Settings
from ..theme import next_theme, resolve_theme
from .help import HelpScreen
from .list import Item, SpringList

DELAY_COLUMN = 7
TYPE_COLUMN = 9


def build_api(settings: Settings) -> MihomoAPI:
    return MihomoAPI(settings.controller, settings.secret, timeout=settings.timeout)


def _fit(text: str, width: int, *, align: str = "left") -> str:
    """把字符串裁剪/补齐到指定显示宽度（中文按两格算）。"""
    text = set_cell_size(text, width)
    if align == "right":
        pad = width - cell_len(text.strip())
        if pad > 0:
            text = " " * pad + text.strip()
    return text


class TclashApp(App[None]):
    """策略组 + 节点列表，vim 键位，过滤后测速。"""

    CSS_PATH = "style.tcss"
    TITLE = "tclash"
    SUB_TITLE = "mihomo 管理器"

    BINDINGS = [
        ("q", "quit", "退出"),
        ("j", "cursor_down", "下移"),
        ("k", "cursor_up", "上移"),
        ("h", "prev_group", "上一组"),
        ("l", "next_group", "下一组"),
        ("g", "first_row", "首个"),
        ("G", "last_row", "末个"),
        ("t", "test_visible", "测速可见"),
        ("T", "test_group", "测速整组"),
        ("b", "best", "选最快"),
        ("s", "toggle_sort", "排序"),
        ("z", "toggle_hidden", "内置节点"),
        ("/", "focus_filter", "过滤"),
        ("tab", "cycle_focus", "换面板"),
        ("ctrl+t", "cycle_theme", "主题"),
        ("?", "help", "帮助"),
        ("r", "refresh", "刷新"),
        ("escape", "blur", "返回"),
    ]

    def __init__(self, settings: Settings, api: MihomoAPI | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.api = api or build_api(settings)
        self._booted = False  # on_mount 完成前不碰 DOM
        self.proxies: list[Proxy] = []
        self.by_name: dict[str, Proxy] = {}
        self.groups: list[Proxy] = []
        self.delays: dict[str, int | None] = {}
        self.group_name: str | None = None
        self.filter_text = ""
        self.show_hidden = False
        self.sort_by_delay = False
        self.testing = False
        self.version = "?"
        self.mode = "?"
        self.status = "启动中…"
        self.status_error = False
        # 延迟分档颜色，随主题刷新：好 / 一般 / 偏慢 / 超时
        self.delay_colors: tuple[str, ...] = ("color(2)", "color(4)", "color(3)", "color(1)")

    # ------------------------------------------------------------- lifecycle
    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield SpringList(self.render_group_row, id="groups", band_alpha=0.36)
            with Vertical(id="main"):
                yield SpringList(self.render_row, id="proxies", band_alpha=0.26)
        yield Input(placeholder="过滤：名字 / 拼音 / 双拼（riben、ribf、rb）… Esc 返回列表", id="filter")
        yield Static(id="hintbar")

    async def on_mount(self) -> None:
        self.theme = resolve_theme(self.settings.theme)
        self._refresh_theme_colors()
        self.query_one("#main", Vertical).border_title = "节点"
        self.query_one("#sidebar", Vertical).border_title = "策略组"
        self.query_one("#topbar", Static).border_title = "tclash"
        self.render_topbar()
        self.render_hintbar()
        await self.reload()
        self.query_one("#proxies", SpringList).focus()
        self._booted = True

    async def on_unmount(self) -> None:
        await self.api.aclose()

    # ----------------------------------------------------------------- theme
    def _refresh_theme_colors(self) -> None:
        """延迟分档颜色：ansi 主题用调色板序号，其余用主题里的十六进制色。"""
        theme = self.current_theme
        if getattr(theme, "ansi", False):
            # color(N) 让终端自己的配色决定具体颜色
            self.delay_colors = ("color(2)", "color(4)", "color(3)", "color(1)")
            self._apply_band()
            return
        self.delay_colors = (
            getattr(theme, "success", "green"),
            getattr(theme, "primary", "cyan"),
            getattr(theme, "warning", "yellow"),
            getattr(theme, "error", "red"),
        )
        self._apply_band()

    def _apply_band(self) -> None:
        """把当前主题的主色/背景色交给列表控件，选中条按强度在两者间渐变。"""
        if not self._booted:
            return
        theme = self.current_theme
        accent = self.delay_colors[1]
        background = getattr(theme, "background", "") or ""
        if background in ("", "ansi_default", "transparent"):
            background = "black" if getattr(theme, "dark", True) else "white"
        for widget in self.query(SpringList):
            widget.set_band(accent, background)

    def watch_theme(self, theme: str) -> None:  # noqa: ARG002 - Textual watcher 签名
        if not self._booted:  # __init__ 里设置主题时还没有 screen
            return
        self._refresh_theme_colors()

    def action_cycle_theme(self) -> None:
        name = next_theme(self.theme)
        self.theme = name
        self.settings.theme = name
        try:
            self.settings.save()
        except OSError:
            pass
        self.notify(f"主题：{name}", timeout=2)

    # ----------------------------------------------------------------- state
    @property
    def current_group(self) -> Proxy | None:
        return self.by_name.get(self.group_name) if self.group_name else None

    def cursor_name(self) -> str | None:
        return self.query_one("#proxies", SpringList).cursor_key

    @property
    def rows(self) -> list[str]:
        """当前列表里的节点名（按显示顺序）。"""
        return [item.key for item in self.query_one("#proxies", SpringList).items]

    def set_status(self, message: str, *, error: bool = False) -> None:
        self.status = message
        self.status_error = error
        self.render_topbar()

    def render_topbar(self) -> None:
        bar = self.query_one("#topbar", Static)
        good, accent, warn, bad = self.delay_colors

        line = Text()
        line.append("● ", style=bad if self.status_error else good)
        line.append(self.status, style=bad if self.status_error else "bold")
        line.append("    ")
        line.append(f"{len(self.proxies)} ", style="bold")
        line.append("节点 · ", style="dim")
        line.append(f"{len(self.groups)} ", style="bold")
        line.append("策略组 · ", style="dim")
        line.append(
            f"并发 {self.settings.concurrency} · 超时 {self.settings.test_timeout_ms / 1000:.1f}s",
            style="dim",
        )
        if self.testing:
            line.append(" · 测速中…", style=warn)
        line.append("\n")
        line.append(self.api.base, style="dim")
        line.append("    模式 ", style="dim")
        line.append(self.mode, style="bold")
        focused = self.focused_proxy()
        if focused is not None and self.focused is self.query_one("#proxies"):
            delay = self.delay_of(focused)
            line.append("    焦点 ", style="dim")
            line.append(focused.name, style="bold " + accent)
            line.append(f" {type_label(focused)}", style="dim")
            line.append(f" {format_delay(delay)}", style=self.delay_style(delay))

        bar.update(line)
        bar.border_subtitle = f"mihomo {self.version.lstrip('v')}"
        self.render_main_title()

    def render_hintbar(self) -> None:
        accent = self.delay_colors[1]
        hints = (
            ("j/k", "移动"),
            ("h/l", "换组"),
            ("Tab", "换面板"),
            ("Enter", "切换"),
            ("t", "测速"),
            ("T", "整组"),
            ("b", "最快"),
            ("/", "过滤"),
            ("s", "排序"),
            ("z", "内置"),
            ("^t", "主题"),
            ("?", "帮助"),
            ("q", "退出"),
        )
        bar = Text()
        width = self.query_one("#hintbar", Static).content_size.width or self.size.width
        for keys, label in hints:
            if cell_len(bar.plain) + cell_len(f" {keys} {label} ") > width:
                break  # 放不下就到此为止，别把最后一项截成“^t”
            bar.append(f" {keys} ", style="bold " + accent)
            bar.append(f"{label} ", style="dim")
        self.query_one("#hintbar", Static).update(bar)

    def render_main_title(self) -> None:
        main = self.query_one("#main", Vertical)
        group = self.current_group
        if group is None:
            main.border_title = "节点"
            main.border_subtitle = ""
            return
        main.border_title = f"节点 · {group.name}"
        total = len([m for m in group.members if not self.is_hidden(self.by_name.get(m), m)])
        bits = [f"{len(self.rows)}/{total} 个"]
        if self.filter_text.strip():
            bits.append(f"过滤“{self.filter_text.strip()}”")
        if self.sort_by_delay:
            bits.append("按延迟")
        hint = group_hint(group)
        if hint:
            bits.append(hint)
        main.border_subtitle = " · ".join(bits)

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
        self.groups = sorted(groups, key=lambda g: 0 if g.type.lower() == "selector" else 1)

        if self.group_name not in self.by_name:
            self.group_name = self.groups[0].name if self.groups else None

        self.set_status("已连接")
        self.fill_groups()
        self.fill_table()

    # ---------------------------------------------------------------- 侧栏
    def render_group_row(self, group: Proxy, width: int, strength: float) -> Text:
        accent = self.delay_colors[1]
        selected = group.name == self.group_name
        line = Text()
        line.append("▍" if selected else " ", style="bold " + accent)
        badge = group_badge(group)
        name_width = max(6, width - 2 - (cell_len(badge) + 1 if badge else 0))
        emphasized = selected or strength > 0.5
        line.append(
            _fit(group.name, name_width), style="bold " + accent if selected else "bold" if emphasized else ""
        )
        if badge:
            line.append(" ")
            line.append(badge, style=accent if selected else "dim")
        return line

    def fill_groups(self, *, slide: int = 0) -> None:
        widget = self.query_one("#groups", SpringList)
        items = [Item(key=g.name, payload=g) for g in self.groups]
        widget.set_items(items, cursor_key=self.group_name, slide=slide)
        self.query_one("#sidebar", Vertical).border_subtitle = f"{len(self.groups)} 组"

    # ---------------------------------------------------------------- 主列表
    def visible_entries(self) -> list[Proxy]:
        group = self.current_group
        if group is None:
            return []
        needle = self.filter_text.strip().casefold()
        entries = [self.by_name.get(m) or Proxy(name=m, type="?", members=()) for m in group.members]
        if not self.show_hidden:
            entries = [p for p in entries if not p.is_info and not p.is_builtin]
        if needle:
            entries = [
                p
                for p in entries
                if needle in p.type.casefold() or search_matches(needle, p.name)
            ]
        if self.sort_by_delay:
            entries.sort(key=lambda p: (self.delay_of(p) is None, self.delay_of(p) or 0))
        return entries

    def render_row(self, proxy: Proxy, width: int, strength: float) -> Text:
        """一行：`▶ 名称  类型  延迟`。strength 用来在选中条上加强文字。"""
        group = self.current_group
        accent = self.delay_colors[1]
        delay = self.delay_of(proxy)
        current = group is not None and group.now == proxy.name

        name_width = max(8, width - 2 - TYPE_COLUMN - DELAY_COLUMN)
        line = Text()
        line.append("▶ " if current else "  ", style="bold " + accent)
        style = "bold" if strength > 0.5 else ""
        line.append(_fit(proxy.name, name_width), style=style)
        line.append(_fit(type_label(proxy), TYPE_COLUMN), style="dim")
        line.append(
            _fit(format_delay(delay), DELAY_COLUMN, align="right"),
            style=self.delay_style(delay) + (" bold" if strength > 0.5 else ""),
        )
        return line

    def fill_table(self, *, slide: int = 0) -> None:
        widget = self.query_one("#proxies", SpringList)
        previous = widget.cursor_key
        items = [Item(key=p.name, payload=p) for p in self.visible_entries()]
        widget.set_items(items, cursor_key=previous, slide=slide)
        self.render_topbar()

    def delay_of(self, proxy: Proxy) -> int | None:
        return self.delays.get(proxy.name, proxy.delay)

    def delay_style(self, delay: int | None) -> str:
        good, accent, warn, bad = self.delay_colors
        if delay is None:
            return "dim"
        if delay <= 200:
            return good
        if delay <= 500:
            return accent
        if delay <= 900:
            return warn
        return bad

    def update_row(self, name: str) -> None:
        self.query_one("#proxies", SpringList).refresh_row(name)

    def focused_proxy(self) -> Proxy | None:
        return self.query_one("#proxies", SpringList).cursor_payload

    # ------------------------------------------------------------- navigation
    def _list(self, which: str) -> SpringList:
        return self.query_one(f"#{which}", SpringList)

    def _active_list(self) -> SpringList:
        focused = self.focused
        if isinstance(focused, SpringList):
            return focused
        return self._list("proxies")

    def _move_row(self, step: int) -> None:
        self._list("proxies").move_cursor(step)

    def _move_group(self, step: int) -> None:
        if not self.groups:
            return
        names = [g.name for g in self.groups]
        index = names.index(self.group_name) if self.group_name in names else 0
        index = (index + step) % len(names)
        self.query_one("#groups", SpringList).move_to(index)

    def action_cursor_down(self) -> None:
        self._active_list().move_cursor(1)

    def action_cursor_up(self) -> None:
        self._active_list().move_cursor(-1)

    def action_prev_group(self) -> None:
        self._move_group(-1)

    def action_next_group(self) -> None:
        self._move_group(1)

    def action_first_row(self) -> None:
        self._list("proxies").move_to(0, wrap=False)

    def action_last_row(self) -> None:
        self._list("proxies").move_to(len(self.rows) - 1, wrap=False)

    # --------------------------------------------------------------- actions
    async def action_refresh(self) -> None:
        await self.reload()

    def action_toggle_sort(self) -> None:
        self.sort_by_delay = not self.sort_by_delay
        self.fill_table()
        self.set_status("按延迟排序" if self.sort_by_delay else "恢复配置顺序")

    def action_toggle_hidden(self) -> None:
        self.show_hidden = not self.show_hidden
        self.fill_table()
        self.set_status("显示内置/信息节点" if self.show_hidden else "已隐藏内置/信息节点")

    def action_focus_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_cycle_focus(self) -> None:
        """Tab 在侧栏 / 节点列表 / 过滤框之间轮转（选中条会弹簧淡入淡出）。"""
        order = [self._list("groups"), self._list("proxies"), self.query_one("#filter", Input)]
        current = self.focused
        index = order.index(current) if current in order else 0
        order[(index + 1) % len(order)].focus()

    def action_blur(self) -> None:
        self._list("proxies").focus()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_test_visible(self) -> None:
        names = list(self.rows)
        if not names:
            self.set_status("当前没有可测速的行（检查过滤条件）", error=True)
            return
        self.test_names(names)

    def action_test_group(self) -> None:
        group = self.current_group
        if group is None:
            return
        names = [n for n in group.members if not self.is_hidden(self.by_name.get(n), n)]
        if not names:
            self.set_status("这个组没有可测速的节点", error=True)
            return
        self.test_names(names)

    def action_best(self) -> None:
        self.auto_best()

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
        reachable = sum(1 for n in names if self.delays.get(n) is not None)
        self.set_status(f"测速完成：{reachable}/{total} 可用")
        if self.sort_by_delay:
            self.fill_table()

    @work(exclusive=True, group="auto-best")
    async def auto_best(self) -> None:
        group = self.current_group
        if group is None:
            return
        names = list(self.rows) or [
            n for n in group.members if not self.is_hidden(self.by_name.get(n), n)
        ]
        if not names:
            self.set_status("这个组没有可测速的节点", error=True)
            return

        total = len(names)
        done = 0
        self.set_status(f"测速选最快 0/{total} …")

        def on_result(name: str, delay: int | None) -> None:
            nonlocal done
            done += 1
            self.delays[name] = delay
            self.update_row(name)
            self.set_status(f"测速选最快 {done}/{total} …")

        try:
            results = await self.api.test_many(
                names,
                url=self.settings.test_url,
                timeout_ms=self.settings.test_timeout_ms,
                concurrency=self.settings.concurrency,
                on_result=on_result,
            )
        except MihomoError as exc:
            self.set_status(str(exc), error=True)
            return

        reachable = {n: d for n, d in results.items() if d is not None}
        if not reachable:
            self.set_status("全部节点测速失败", error=True)
            self.fill_table()
            return
        best = min(reachable, key=lambda n: reachable[n] or 10**9)
        await self._select(best, f"最快：{best} {reachable[best]}ms")

    # -------------------------------------------------------------- select
    @on(SpringList.Activated, "#proxies")
    async def on_row_activated(self, event: SpringList.Activated) -> None:
        await self._select(event.key, f"已切换到 {event.key}")

    @on(SpringList.Activated, "#groups")
    def on_group_activated(self, event: SpringList.Activated) -> None:
        self._list("proxies").focus()

    @on(SpringList.CursorMoved, "#groups")
    def on_group_moved(self, event: SpringList.CursorMoved) -> None:
        if not event.key or event.key == self.group_name:
            return
        previous = self.group_index()
        self.group_name = event.key
        direction = 1 if self.group_index() >= previous else -1
        self.fill_table(slide=direction)

    @on(SpringList.CursorMoved, "#proxies")
    def on_row_moved(self, event: SpringList.CursorMoved) -> None:  # noqa: ARG002
        if self._booted:
            self.render_topbar()

    def group_index(self) -> int:
        names = [g.name for g in self.groups]
        return names.index(self.group_name) if self.group_name in names else 0

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
        self.fill_groups()
        self.fill_table()

    @on(Input.Changed, "#filter")
    def on_filter_changed(self, event: Input.Changed) -> None:
        self.filter_text = event.value
        self.fill_table()

    @on(Input.Submitted, "#filter")
    def on_filter_submitted(self) -> None:
        self._list("proxies").focus()
