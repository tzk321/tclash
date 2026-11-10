"""两个只读弹窗：`n` 看活动连接与实时速度，`w` 看规则表。

两者都复用了主列表那套弹簧选中条，所以翻动时也是滑过去的。
"""

from __future__ import annotations

from rich.cells import cell_len, set_cell_size
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from ..subscription import human_bytes
from ..traffic import Connection, TrafficMeter, format_speed, parse_connections
from .list import Item, SpringList


def _fit(text: str, width: int) -> str:
    return set_cell_size(text, width)


class ConnectionsScreen(ModalScreen[None]):
    """活动连接。每秒自己刷一次，Esc 关闭。"""

    BINDINGS = [Binding("escape,q,n", "close", "关闭")]

    def __init__(self) -> None:
        super().__init__()
        self.meter = TrafficMeter()
        self.items: list[Connection] = []
        self.error = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="conn-box"):
            yield Static(id="conn-head")
            yield SpringList(self.render_row, id="conn-list", band_alpha=0.22)
            yield Static("Esc 关闭 · 每秒刷新", id="conn-hint")

    def on_mount(self) -> None:
        self.query_one("#conn-box", Vertical).border_title = "活动连接"
        self.query_one("#conn-list", SpringList).focus()
        self.render_head()
        self.refresh_data()
        self.set_interval(1.0, self.refresh_data)

    @property
    def app_api(self):  # noqa: ANN201 - 只是转发，不想为它建协议类
        return self.app.api  # type: ignore[attr-defined]
    def refresh_data(self) -> None:
        self.run_worker(self._poll(), exclusive=True, group="conn-poll")

    async def _poll(self) -> None:
        try:
            payload = await self.app_api.connections()
        except Exception as exc:  # noqa: BLE001 - 弹窗里出错不该炸掉整个界面
            self.error = str(exc)
            self.render_head()
            return
        self.error = ""
        self.meter.sample(payload)
        self.items = parse_connections(payload)
        self.render_head()
        cursor = self.query_one("#conn-list", SpringList).cursor_key
        self.query_one("#conn-list", SpringList).set_items(
            [Item(key=c.id or c.destination, payload=c) for c in self.items],
            cursor_key=cursor,
        )

    def render_head(self) -> None:
        head = self.query_one("#conn-head", Static)
        box = self.query_one("#conn-box", Vertical)
        box.border_subtitle = f"{len(self.items)} 条连接"
        if self.error:
            head.update(Text(f"取不到连接：{self.error}", style="bold red"))
            return
        meter = self.meter
        line = Text()
        line.append("↓ ", style="dim")
        line.append(format_speed(meter.down_speed), style="bold")
        line.append("  ↑ ", style="dim")
        line.append(format_speed(meter.up_speed), style="bold")
        line.append("  累计 ", style="dim")
        line.append(f"{human_bytes(meter.down_total)} ↓ {human_bytes(meter.up_total)} ↑", style="dim")
        head.update(line)

    def render_row(self, conn: Connection, width: int, strength: float) -> Text:
        line = Text()
        rule = conn.rule or "—"
        chain = " → ".join(conn.chains) or "—"
        tail = f"{human_bytes(conn.total):>9}  {rule}"
        host_width = max(8, width - cell_len(tail) - 2)
        style = "bold" if strength > 0.5 else ""
        line.append(_fit(conn.host, host_width), style=style)
        line.append(f"{human_bytes(conn.total):>9}", style="dim")
        line.append(f"  {rule}", style="dim")
        if cell_len(chain) < width // 2:
            line.append(f"  {chain}", style="dim")
        return line

    def action_close(self) -> None:
        self.dismiss(None)


class RulesScreen(ModalScreen[None]):
    """规则表：`w` 打开，`/` 过滤交给主界面的输入框逻辑太重，这里只做滚动。"""

    BINDINGS = [Binding("escape,q,w", "close", "关闭")]

    def __init__(self, rules: list[dict]) -> None:
        super().__init__()
        self.rules = rules

    def compose(self) -> ComposeResult:
        with Vertical(id="rules-box"):
            yield SpringList(self.render_row, id="rules-list", band_alpha=0.22)
            yield Static("Esc 关闭", id="rules-hint")

    def on_mount(self) -> None:
        box = self.query_one("#rules-box", Vertical)
        box.border_title = "规则"
        box.border_subtitle = f"{len(self.rules)} 条"
        widget = self.query_one("#rules-list", SpringList)
        widget.set_items(
            [Item(key=f"{i}:{r.get('payload', '')}", payload=r) for i, r in enumerate(self.rules)]
        )
        widget.focus()

    def render_row(self, rule: dict, width: int, strength: float) -> Text:
        line = Text()
        kind = str(rule.get("type") or "?")
        payload = str(rule.get("payload") or "")
        proxy = str(rule.get("proxy") or "")
        kind_width = 14
        proxy_width = min(20, max(8, width // 4))
        payload_width = max(8, width - kind_width - proxy_width - 2)
        style = "bold" if strength > 0.5 else ""
        line.append(_fit(kind, kind_width), style="dim")
        line.append(_fit(payload, payload_width), style=style)
        line.append(_fit(proxy, proxy_width), style="dim")
        return line

    def action_close(self) -> None:
        self.dismiss(None)
