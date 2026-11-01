"""帮助页：键位 + 策略组类型说明。"""

from __future__ import annotations

from rich.table import Table
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

KEYS = [
    ("j / k", "上下移动光标（侧栏聚焦时切换策略组）"),
    ("h / l", "上一个 / 下一个策略组"),
    ("g / G", "跳到第一个 / 最后一个节点"),
    ("Enter", "在当前策略组里切换到选中节点"),
    ("t", "测速当前可见（含过滤结果）的节点"),
    ("T", "测速整组，忽略过滤条件"),
    ("b", "测速并在可见节点里切到最快的"),
    ("s", "按延迟排序 / 恢复配置顺序"),
    ("z", "显示 / 隐藏 DIRECT、剩余流量 这类节点"),
    ("Tab", "在侧栏 / 节点列表 / 过滤框之间轮转"),
    ("/", "过滤节点：中文、全拼、首字母、小鹤双拼都行（riben / ribf / rb），Esc 回到列表"),
    ("Ctrl+T", "循环切换主题"),
    ("u", "拉取并生效当前订阅（写 /etc/mihomo/config.yaml 后热重载）"),
    ("p", "订阅列表：Enter 切换、u 拉取更新"),
    ("r", "重新读取 mihomo 状态"),
    ("q", "退出"),
]


def _keys_table() -> Table:
    table = Table(box=None, show_header=False, pad_edge=False)
    table.add_column("按键", style="bold cyan", no_wrap=True)
    table.add_column("作用", style="")
    for keys, desc in KEYS:
        table.add_row(keys, desc)
    return table


def _groups_table() -> Table:
    table = Table(box=None, show_header=False, pad_edge=False)
    table.add_column("类型", style="bold", no_wrap=True)
    table.add_column("行为")
    table.add_row("手动选择", "自己挑节点。机场订阅的主组通常是这个，里面一般放着“自动测速”“故障转移”两个子组。")
    table.add_row("自动测速", "mihomo 定期给组内节点测速，自动使用延迟最低的；Enter 可以临时手动指定。")
    table.add_row("故障转移", "按顺序使用，当前节点不通才自动换下一个。")
    table.add_row("负载均衡", "把连接分摊到组内多个节点。")
    return table


class HelpScreen(ModalScreen[None]):
    """按 ? 呼出的帮助。"""

    BINDINGS = [
        ("escape", "dismiss", "关闭"),
        ("q", "dismiss", "关闭"),
        ("?", "dismiss", "关闭"),
    ]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help"):
            yield Static("按键", classes="help-heading")
            yield Static(_keys_table(), classes="help-block")
            yield Static("策略组怎么分", classes="help-heading")
            yield Static(_groups_table(), classes="help-block")
