"""订阅切换弹窗：`p` 打开，Enter 生效，`u` 更新当前项。"""

from __future__ import annotations

from rich.cells import cell_len, set_cell_size
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from ..subscription import Profile
from .list import Item, SpringList


def _fit(text: str, width: int) -> str:
    """裁剪/补齐到指定显示宽度（中文按两格算）。"""
    return set_cell_size(text, width)


class ProfileScreen(ModalScreen[str | None]):
    """选订阅。返回值是要生效的订阅名，取消返回 None。"""

    BINDINGS = [
        Binding("escape,q", "cancel", "取消"),
        Binding("enter", "choose", "切换", show=False),
        Binding("u", "update", "更新"),
    ]

    def __init__(self, profiles: list[Profile], active: str) -> None:
        super().__init__()
        self.profiles = profiles
        self.active = active

    def compose(self) -> ComposeResult:
        with Vertical(id="profile-box"):
            yield SpringList(self.render_row, id="profile-list", band_alpha=0.26)
            yield Static(
                "Enter 切换 · u 拉取更新 · Esc 取消", id="profile-hint"
            )

    def on_mount(self) -> None:
        box = self.query_one("#profile-box", Vertical)
        box.border_title = "订阅"
        box.border_subtitle = f"{len(self.profiles)} 个 · 当前 {self.active or '—'}"
        widget = self.query_one("#profile-list", SpringList)
        widget.set_items(
            [Item(key=p.name, payload=p) for p in self.profiles],
            cursor_key=self.active,
        )
        widget.focus()

    def render_row(self, profile: Profile, width: int, strength: float) -> Text:
        line = Text()
        mark = "▶ " if profile.name == self.active else "  "
        line.append(mark, style="bold")
        tail = f"{profile.traffic_line()} · {profile.expire_line()}"
        if profile.interval:
            tail += f" · 每 {profile.interval}h"
        name_width = max(10, width - 2 - 6 - cell_len(tail))
        style = "bold" if profile.name == self.active or strength > 0.5 else ""
        line.append(_fit(profile.name, name_width), style=style)
        line.append(f"{profile.node_count or '—':>4} ", style="dim")
        line.append(tail, style="dim")
        return line

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(SpringList.Activated, "#profile-list")
    def _activated(self, event: SpringList.Activated) -> None:
        """列表自己吃掉了 Enter，所以从它的 Activated 消息里取选中项。"""
        self.dismiss(event.key)

    def action_choose(self) -> None:
        key = self.query_one("#profile-list", SpringList).cursor_key
        if key:
            self.dismiss(key)

    def action_update(self) -> None:
        key = self.query_one("#profile-list", SpringList).cursor_key
        if key:
            self.dismiss(f"update:{key}")
