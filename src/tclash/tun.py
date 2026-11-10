"""TUN 开关：运行时热切换 + 开机默认（写配置）。

运行时走 ``PATCH /configs {"tun": {"enable": ...}}``，mihomo 的
``ReCreateTun`` 只合并传入的字段，不会碰端口和策略组。

开机默认要落到两处，缺一不可：

- ``/etc/mihomo/config.yaml`` 的 ``tun.enable``：内核启动时读它；
- ``~/.config/tclash/overrides.yaml`` 的 ``tun.enable``：``tclash sub update``
  会用订阅 + 覆盖项重新生成 config.yaml，不写覆盖项的话下一次更新就把 TUN 冲掉。

改配置一律做文本级替换，只动 ``tun.enable`` 这一行，其余内容（订阅生成的
dns / 规则 / 注释）逐字保留。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import subscription
from .settings import Settings
from .subscription import SubError

TUN_KEY = "tun"
ENABLE_KEY = "enable"

_TOP_TUN = re.compile(r"^tun\s*:")
_ENABLE_LINE = re.compile(r"^(\s*)enable\s*:\s*([^#\n]*?)\s*(#.*)?$")


# --------------------------------------------------------------- 文本级改写
def patch_tun_yaml(text: str, enable: bool) -> str:
    """把 ``tun.enable`` 设成 ``enable``，其余内容逐字保留。

    支持三种已有写法：块形式（``tun:`` + 缩进子行）、行内映射
    （``tun: {enable: false}``）、空值（``tun:`` 或 ``tun: null``）。
    找不到顶层 ``tun:`` 时在文件末尾追加一个最小块。
    """
    value = "true" if enable else "false"
    lines = text.splitlines(keepends=True)
    index = next((i for i, line in enumerate(lines) if _TOP_TUN.match(line)), None)

    if index is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        return "".join(lines) + f"tun:\n  {ENABLE_KEY}: {value}\n"

    inline = lines[index].split(":", 1)[1].strip()
    if inline and not inline.startswith("#"):
        return _patch_inline(lines, index, inline, value)

    # 块形式：先找 tun: 下面第一个 enable 子行
    for offset in range(index + 1, len(lines)):
        raw = lines[offset]
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw.startswith((" ", "\t")):
            break  # 回到顶层，说明 tun 块已经结束
        match = _ENABLE_LINE.match(raw.rstrip("\n"))
        if match is None:
            continue
        indent, _, comment = match.groups()
        suffix = f" {comment}" if comment else ""
        lines[offset] = f"{indent}{ENABLE_KEY}: {value}{suffix}\n"
        return "".join(lines)

    # 没有 enable 子行：插在 tun: 之后，沿用第一个子行的缩进
    indent = _child_indent(lines, index)
    lines.insert(index + 1, f"{indent}{ENABLE_KEY}: {value}\n")
    return "".join(lines)


def _child_indent(lines: list[str], index: int) -> str:
    for raw in lines[index + 1 :]:
        if raw.strip() and not raw.lstrip().startswith("#"):
            if raw.startswith((" ", "\t")):
                return raw[: len(raw) - len(raw.lstrip(" \t"))]
            break
    return "  "


def _patch_inline(lines: list[str], index: int, inline: str, value: str) -> str:
    if inline.startswith("{") and inline.endswith("}"):
        inner = inline[1:-1].strip()
        if not inner:
            lines[index] = f"tun: {{{ENABLE_KEY}: {value}}}\n"
            return "".join(lines)
        pattern = re.compile(rf"(?<![\w-]){ENABLE_KEY}\s*:\s*[^,}}]+")
        if pattern.search(inner):
            inner = pattern.sub(f"{ENABLE_KEY}: {value}", inner, count=1)
            lines[index] = f"tun: {{{inner}}}\n"
        else:
            lines[index] = f"tun: {{{ENABLE_KEY}: {value}, {inner}}}\n"
        return "".join(lines)

    # tun: null / tun: ~ → 改写成块形式
    lines[index] = "tun:\n"
    lines.insert(index + 1, f"  {ENABLE_KEY}: {value}\n")
    return "".join(lines)


# ------------------------------------------------------------------- 读状态
def runtime_enabled(configs: dict[str, Any]) -> bool:
    """内核当前是否开着 TUN（来自 ``GET /configs``）。"""
    tun = configs.get(TUN_KEY) or {}
    return bool(tun.get(ENABLE_KEY)) if isinstance(tun, dict) else False


def file_enabled(config_path: str | Path) -> bool | None:
    """``/etc/mihomo/config.yaml`` 里的 ``tun.enable``；读不到返回 None。"""
    path = Path(config_path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    tun = data.get(TUN_KEY) or {}
    return bool(tun.get(ENABLE_KEY)) if isinstance(tun, dict) else False


def override_enabled(path: Path | None = None) -> bool | None:
    """覆盖项里是否显式写了 ``tun.enable``；没写返回 None。"""
    try:
        data = subscription.load_overrides(path)
    except SubError:
        return None
    tun = data.get(TUN_KEY)
    if not isinstance(tun, dict) or ENABLE_KEY not in tun:
        return None
    return bool(tun.get(ENABLE_KEY))


# ------------------------------------------------------------------- 写状态
def write_override(enable: bool, path: Path | None = None) -> Path:
    """在 overrides.yaml 里记下 ``tun.enable``，注释和其它覆盖项保留。"""
    path = path or subscription.overrides_path()
    text = ""
    if path.exists():
        text = path.read_text(encoding="utf-8")
    elif not text:
        text = "# tclash 本地覆盖项：订阅更新时合并进 /etc/mihomo/config.yaml\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(patch_tun_yaml(text, enable), encoding="utf-8")
    return path


def persist_config(config_path: str | Path, enable: bool) -> Path | None:
    """改 ``/etc/mihomo/config.yaml``：内核校验 → 备份 → 原子替换。"""
    target = Path(config_path)
    if not target.exists():
        raise SubError(f"找不到配置文件 {target}")
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise SubError(f"读取 {target} 失败：{exc}") from exc

    candidate = subscription.temp_candidate(patch_tun_yaml(raw, enable))
    try:
        subscription.validate_config(candidate, target.parent)
        return subscription.install_config(candidate, target)
    finally:
        candidate.unlink(missing_ok=True)


# --------------------------------------------------------------------- 编排
@dataclass
class TunResult:
    enabled: bool
    runtime: bool
    boot: bool
    steps: list[str] = field(default_factory=list)
    backup: Path | None = None
    overrides: Path | None = None


async def set_tun(
    api: Any,
    settings: Settings,
    enable: bool,
    *,
    persist: bool = True,
) -> TunResult:
    """切换 TUN。

    ``persist=True``（默认）先写覆盖项和 config.yaml，再热重载让运行时跟上；
    ``persist=False`` 只 PATCH 运行时，重启后回到文件里的状态。
    """
    result = TunResult(enabled=enable, runtime=False, boot=False)

    if persist:
        result.backup = await asyncio.to_thread(persist_config, settings.config_path, enable)
        result.steps.append(
            f"已写入 {settings.config_path}"
            + (f"（备份 {result.backup.name}）" if result.backup else "")
        )
        result.overrides = await asyncio.to_thread(write_override, enable)
        result.steps.append(f"覆盖项已更新：{result.overrides}")
        await api.reload_config(settings.config_path)
        result.steps.append("已热重载")
        result.boot = True
        result.runtime = enable
        return result

    await api.set_tun(enable)
    result.runtime = enable
    result.boot = file_enabled(settings.config_path) is True
    result.steps.append("只改了运行时，重启后回到配置文件的状态")
    return result
