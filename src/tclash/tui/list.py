"""自绘列表控件：选中条用弹簧动画滑到目标行。

Textual 的 `DataTable` / `ListView` 只有整数行高亮，切换时是硬跳变。
这里自己实现 Line API 控件，把选中条的位置做成浮点数，按覆盖强度把
相邻两行交叉淡入淡出，于是上下移动是"滑"过去而不是"闪"过去。

左右换组时整块内容从侧边切入（`set_items(slide=±1)`），也走同一条弹簧。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Generic, Iterable, TypeVar

from rich.color import Color as RichColor
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events
from textual.color import Color
from textual.geometry import Region, Size
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.timer import Timer

from .spring import Spring

T = TypeVar("T")

FPS = 60.0
"""动画帧率。只在有动画时跑定时器，收敛后暂停。"""

SLIDE_STIFFNESS = 200.0
SLIDE_DAMPING = 20.0
"""换组时内容切入的弹簧参数：阻尼比 0.71，约 0.35 秒到位，带一点回弹。"""


@dataclass(slots=True)
class Item(Generic[T]):
    """列表一项。`key` 用来在刷新后找回光标位置。"""

    key: str
    payload: T


Renderer = Callable[[Any, int, float], Text]
"""`(payload, width, strength) -> Text`。strength 是选中条覆盖率 0..1，用来加强对比。"""


def _to_color(value: str | Color) -> Color:
    """解析颜色。textual 的 `Color.parse` 不认 `color(4)` 这种调色板写法，
    rich 认，所以先走 rich 再撒回 textual 的 Color。"""
    if isinstance(value, Color):
        return value
    try:
        return Color.from_rich_color(RichColor.parse(value))
    except Exception:  # noqa: BLE001 - 退回 textual 自己的解析
        return Color.parse(value)


class SpringList(ScrollView, Generic[T]):
    """弹簧选中条列表。"""

    can_focus = True

    BINDINGS = [
        ("enter", "activate", "切换"),
    ]

    DEFAULT_CSS = """
    SpringList {
        height: 1fr;
        background: transparent;
        padding: 0 1;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
    }
    """

    class Activated(Message):
        """回车 / 点击某一行。"""

        def __init__(self, widget: SpringList, key: str) -> None:
            super().__init__()
            self.widget = widget
            self.key = key

        @property
        def control(self) -> SpringList:
            """给 `@on(SpringList.Activated, "#proxies")` 选择器用。"""
            return self.widget

    class CursorMoved(Message):
        """光标行变化（键盘或鼠标）。"""

        def __init__(self, widget: SpringList, key: str | None, index: int) -> None:
            super().__init__()
            self.widget = widget
            self.key = key
            self.index = index

        @property
        def control(self) -> SpringList:
            return self.widget

    def __init__(
        self,
        renderer: Renderer,
        *,
        band_alpha: float = 0.32,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self.renderer = renderer
        self.band_alpha = band_alpha
        self.items: list[Item[T]] = []
        self.cursor = 0
        # 选中条当前所在的行（浮点），点吸附由弹簧收敛保证
        self.pos = Spring(0.0, stiffness=380.0, damping=34.0)
        # 内容水平偏移（格），换组时从 ±宽度 滑到 0
        self.slide = Spring(
            0.0, stiffness=SLIDE_STIFFNESS, damping=SLIDE_DAMPING, threshold=0.05
        )
        self.band_color = Color.parse("#7aa2f7")
        self.band_toward = Color.parse("#1a1b26")
        # 聚焦程度：失焦时选中条淡下去，也是弹簧过渡
        self.active = Spring(1.0, stiffness=220.0, damping=24.0, threshold=0.01)
        self._timer: Timer | None = None
        self._slide_dir = 0
        self._width = 0
        self._last_tick = 0.0

    # ------------------------------------------------------------- lifecycle
    def on_mount(self) -> None:
        self._timer = self.set_interval(1 / FPS, self._tick, pause=True)
        self._sync_size()

    def on_resize(self, event: events.Resize) -> None:
        self._sync_size()
        self.refresh()

    def on_focus(self) -> None:
        self.active.to(1.0)
        self._ensure_animating()

    def on_blur(self) -> None:
        self.active.to(0.0)
        self._ensure_animating()

    def _sync_size(self) -> None:
        width = self.scrollable_content_region.width
        self._width = width
        height = max(len(self.items), 1)
        self.virtual_size = Size(width, height)

    # ------------------------------------------------------------------ 数据
    def set_items(
        self,
        items: Iterable[Item[T]],
        *,
        cursor_key: str | None = None,
        slide: int = 0,
    ) -> None:
        """整批替换。

        Args:
            items: 新数据。
            cursor_key: 尽量把光标恢复到这一项。
            slide: 内容切入方向，+1 从右侧滑入，-1 从左侧，0 不动。
        """
        self.items = list(items)
        keys = [item.key for item in self.items]
        if cursor_key and cursor_key in keys:
            self.cursor = keys.index(cursor_key)
        elif self.items:
            self.cursor = max(0, min(self.cursor, len(self.items) - 1))
        else:
            self.cursor = 0

        self._sync_size()
        self.pos.snap(self.cursor)

        if slide and self._width > 0:
            self._slide_dir = slide
            self.slide.snap(self._width * slide)
            self.slide.to(0.0)
        else:
            self.slide.snap(0.0)
            self._slide_dir = 0

        self.scroll_to(y=0, animate=False)
        self._follow(force=True)
        self._ensure_animating()
        self.refresh()
        self.post_message(self.CursorMoved(self, self.cursor_key, self.cursor))

    def refresh_row(self, key: str) -> None:
        """只重画某一行（测速结果回来时用）。"""
        for index, item in enumerate(self.items):
            if item.key == key:
                top = index - int(self.scroll_offset.y)
                if 0 <= top < self.size.height:
                    self.refresh(Region(0, top, self.size.width, 1))
                return

    # ------------------------------------------------------------------ 光标
    @property
    def cursor_key(self) -> str | None:
        if 0 <= self.cursor < len(self.items):
            return self.items[self.cursor].key
        return None

    @property
    def cursor_payload(self) -> T | None:
        if 0 <= self.cursor < len(self.items):
            return self.items[self.cursor].payload
        return None

    def move_cursor(self, delta: int, *, wrap: bool = True) -> None:
        self.move_to(self.cursor + delta, wrap=wrap)

    def move_to(self, index: int, *, wrap: bool = True) -> None:
        if not self.items:
            return
        count = len(self.items)
        if wrap:
            index %= count
        else:
            index = max(0, min(index, count - 1))
        if index == self.cursor:
            self._follow()
            return
        self.cursor = index
        # 大跨度时给弹簧一点额外冲量，跳转才有"甩过去"的手感
        distance = index - self.pos.target
        if abs(distance) > 6:
            self.pos.kick(0.0)
        self.pos.to(float(index))
        self._follow()
        self._ensure_animating()
        self.post_message(self.CursorMoved(self, self.cursor_key, self.cursor))

    def action_activate(self) -> None:
        if self.cursor_key is not None:
            self.post_message(self.Activated(self, self.cursor_key))

    # ------------------------------------------------------------------ 动画
    def _ensure_animating(self) -> None:
        """有动画时才跑定时器，避免空闲时白烧 CPU。"""
        if self._timer is not None:
            self._last_tick = time.monotonic()
            self._timer.resume()

    def _tick(self) -> None:
        now = time.monotonic()
        dt = now - self._last_tick if self._last_tick else 1 / FPS
        self._last_tick = now
        before = (self.pos.value, self.slide.value, self.active.value)
        self.pos.step(dt)
        self.slide.step(dt)
        self.active.step(dt)
        if self.pos.settled:
            self.pos.value = float(self.pos.target)
        if self.slide.settled and self._slide_dir:
            self._slide_dir = 0
            self.scroll_to(x=0, animate=False)
        if (
            abs(before[0] - self.pos.value) > 1e-4
            or abs(before[1] - self.slide.value) > 1e-4
            or abs(before[2] - self.active.value) > 1e-4
        ):
            self._follow()
            self.refresh()
        if self.pos.settled and self.slide.settled and self.active.settled:
            self.refresh()
            if self._timer is not None:
                self._timer.pause()

    def _follow(self, *, force: bool = False) -> None:
        """保证光标行在可视区内，留两行余量。"""
        height = self.scrollable_content_region.height
        if height <= 0 or not self.items:
            return
        y0 = int(self.scroll_offset.y)
        margin = 2 if force else 1
        cursor = self.cursor
        if cursor < y0 + margin:
            self.scroll_to(y=max(0, cursor - margin), animate=False)
        elif cursor >= y0 + height - margin:
            self.scroll_to(y=cursor - height + margin + 1, animate=False)

    # ---------------------------------------------------------------- 选中条
    def set_band(self, accent: str | Color, toward: str | Color) -> None:
        """选中条配色。accent 是主色，toward 是背景（用于淡出方向）。"""
        self.band_color = _to_color(accent)
        self.band_toward = _to_color(toward)
        self.refresh()

    def _strength(self, index: int) -> float:
        distance = abs(index - self.pos.value)
        return max(0.0, 1.0 - distance)

    def _band(self, index: int) -> Style | None:
        strength = self._strength(index)
        if strength <= 0.02:
            return None
        mix = min(1.0, self.band_alpha * strength * (0.3 + 0.7 * self.active.value))
        # 手动在 RGB 空间插值：Color.blend() 遇到 ansi 调色板色会退化到
        # 最近的调色板序号，渐变会断裂，所以这里直接算三元组再转真彩。
        accent, toward = self.band_color, self.band_toward
        color = Color(
            int(toward.r + (accent.r - toward.r) * mix),
            int(toward.g + (accent.g - toward.g) * mix),
            int(toward.b + (accent.b - toward.b) * mix),
            1.0,
        )
        return Style(bgcolor=color.rich_color, bold=strength > 0.55)

    # ------------------------------------------------------------------ 渲染
    def render_line(self, y: int) -> Strip:
        width = self.scrollable_content_region.width
        if width <= 0:
            return Strip.blank(self.size.width, self.rich_style)
        scroll_x, scroll_y = self.scroll_offset
        index = int(scroll_y) + y
        if index >= len(self.items):
            return Strip.blank(width, self.rich_style)

        item = self.items[index]
        strength = self._strength(index)
        text = self.renderer(item.payload, width, strength)
        text.truncate(width, overflow="ellipsis", pad=True)
        strip = Strip(text.render(self.app.console), cell_length=width)
        strip = strip.adjust_cell_length(width)

        offset = int(round(self.slide.value))
        if offset:
            strip = self._shift(strip, offset, width)

        band = self._band(index)
        if band is not None:
            strip = Strip(
                [Segment(segment.text, (segment.style or Style()) + band) for segment in strip],
                width,
            )
        return strip

    @staticmethod
    def _shift(strip: Strip, offset: int, width: int) -> Strip:
        if offset > 0:
            pad = Segment(" " * min(offset, width))
            body = strip.crop(0, max(0, width - offset))
            return Strip([pad, *body], width)
        body = strip.crop(min(-offset, width), width)
        return body.adjust_cell_length(width)

    # ------------------------------------------------------------------ 鼠标
    def on_click(self, event: events.Click) -> None:
        index = int(self.scroll_offset.y) + event.y
        if 0 <= index < len(self.items):
            self.focus()
            self.move_to(index, wrap=False)
            self.post_message(self.Activated(self, self.items[index].key))
