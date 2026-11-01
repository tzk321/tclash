"""订阅：拉取、缓存、合并本地覆盖项、校验、写盘、热重载。

一条完整链路（``tclash sub update``）：

    fetch(URL)  →  原始 yaml 存 cache  →  合并 overrides.yaml
                →  mihomo -t 校验  →  备份 /etc/mihomo/config.yaml
                →  原子替换  →  PUT /configs?force=true 热重载

写 /etc 需要 root，这里统一用 ``sudo -n``（免密）；拿不到权限时明确报错，
不做任何隐式提权。合并只覆盖 ``overrides.yaml`` 里显式列出的键，
订阅自带的其它内容保持原样。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import httpx
import yaml

from .settings import config_dir, data_dir

#: 机场按 UA 决定返回哪种格式，clash.meta 的兼容性最好
USER_AGENT = "clash.meta"
#: 保留最近几份 /etc/mihomo/config.yaml 备份
KEEP_BACKUPS = 5


class SubError(RuntimeError):
    """订阅相关失败：网络、格式、权限、校验。"""


# --------------------------------------------------------------------- model
@dataclass
class Profile:
    name: str
    url: str = ""
    updated_at: float | None = None
    interval: int | None = None  # profile-update-interval，单位小时
    upload: int = 0
    download: int = 0
    total: int = 0
    expire: int = 0
    node_count: int = 0
    error: str = ""

    @property
    def slug(self) -> str:
        cleaned = re.sub(r"[^\w.-]+", "-", self.name, flags=re.UNICODE).strip("-")
        return cleaned or "profile"

    @property
    def raw_path(self) -> Path:
        """机场原样返回的 yaml（未合并覆盖项）。"""
        return data_dir() / "profiles" / f"{self.slug}.raw.yaml"

    @property
    def merged_path(self) -> Path:
        """合并覆盖项后、准备写给 mihomo 的 yaml。"""
        return data_dir() / "profiles" / f"{self.slug}.yaml"

    @property
    def due(self) -> bool:
        """按订阅声明的更新间隔，判断是否该刷新了。"""
        if self.updated_at is None:
            return True
        if not self.interval:
            return False
        return time.time() - self.updated_at >= self.interval * 3600

    @property
    def used(self) -> int:
        return self.upload + self.download

    def traffic_line(self) -> str:
        if not self.total:
            return "流量未知"
        return f"已用 {human_bytes(self.used)} / {human_bytes(self.total)}"

    def expire_line(self) -> str:
        if not self.expire:
            return "到期未知"
        left = self.expire - int(time.time())
        if left <= 0:
            return "已到期"
        return f"剩 {left // 86400} 天"

    def updated_line(self) -> str:
        if self.updated_at is None:
            return "从未更新"
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.updated_at))
        if self.interval:
            return f"{stamp}（每 {self.interval} 小时）"
        return stamp


def human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# ------------------------------------------------------------------ profiles
@dataclass
class Profiles:
    """``~/.config/tclash/profiles.toml``：多订阅 + 当前生效的那个。"""

    active: str = ""
    items: dict[str, Profile] = field(default_factory=dict)

    @classmethod
    def path(cls) -> Path:
        return config_dir() / "profiles.toml"

    @classmethod
    def load(cls, path: Path | None = None) -> "Profiles":
        path = path or cls.path()
        if not path.exists():
            return cls()
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise SubError(f"读取 {path} 失败：{exc}") from exc
        known = {f.name for f in fields(Profile)}
        items: dict[str, Profile] = {}
        for name, entry in (raw.get("profiles") or {}).items():
            if not isinstance(entry, dict):
                continue
            items[name] = Profile(name=name, **{k: v for k, v in entry.items() if k in known})
        return cls(active=str(raw.get("active") or ""), items=items)

    def save(self, path: Path | None = None) -> Path:
        path = path or self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# tclash 订阅列表（URL 属于秘密，文件权限 600）", ""]
        lines.append(f"active = {_toml(self.active)}")
        for profile in self.items.values():
            lines.append("")
            lines.append(f"[profiles.{_toml_key(profile.name)}]")
            for f in fields(Profile):
                if f.name == "name":
                    continue
                value = getattr(profile, f.name)
                if value in ("", None, 0) and f.name != "url":
                    continue
                lines.append(f"{f.name} = {_toml(value)}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path

    def add(self, name: str, url: str) -> Profile:
        profile = self.items.get(name) or Profile(name=name)
        profile.url = url
        self.items[name] = profile
        if not self.active:
            self.active = name
        return profile

    def get(self, name: str) -> Profile:
        if name not in self.items:
            raise SubError(f"没有名为 {name!r} 的订阅，用 tclash sub add 添加")
        return self.items[name]

    def current(self) -> Profile:
        if not self.active:
            raise SubError("还没有配置订阅，先 tclash sub add <名字> <URL>")
        return self.get(self.active)


def _toml(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_key(name: str) -> str:
    # TOML 的裸键只允许 ASCII，中文名必须加引号
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else _toml(name)


# ------------------------------------------------------------------- fetch
async def fetch(url: str, *, timeout: float = 20.0) -> tuple[str, dict[str, str]]:
    """拉订阅原文，返回 (yaml 文本, 响应头)。"""
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=timeout, headers=headers
        ) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        raise SubError(f"拉取订阅失败：{exc}") from exc
    if resp.status_code != 200:
        raise SubError(f"拉取订阅失败：HTTP {resp.status_code} {resp.text[:120]}")
    text = resp.text
    if "proxies:" not in text:
        raise SubError("返回内容不像 Clash 配置（没有 proxies 段），检查 URL 或 User-Agent")
    return text, dict(resp.headers)


def count_nodes(text: str) -> int:
    """数 proxies 段里的节点个数（不能靠字符串匹配，proxy-groups 也长这样）。"""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return 0
    if not isinstance(data, dict):
        return 0
    proxies = data.get("proxies")
    return len(proxies) if isinstance(proxies, list) else 0


def apply_headers(profile: Profile, headers: dict[str, str], text: str) -> None:
    """把订阅响应头里的流量/间隔信息写进 profile。"""
    info = headers.get("subscription-userinfo") or headers.get("Subscription-Userinfo") or ""
    for chunk in info.split(";"):
        key, _, value = chunk.strip().partition("=")
        if not value.strip().isdigit():
            continue
        number = int(value.strip())
        if key.strip() == "upload":
            profile.upload = number
        elif key.strip() == "download":
            profile.download = number
        elif key.strip() == "total":
            profile.total = number
        elif key.strip() == "expire":
            profile.expire = number
    interval = headers.get("profile-update-interval") or headers.get("Profile-Update-Interval")
    if interval and interval.strip().isdigit():
        profile.interval = int(interval.strip())
    profile.node_count = count_nodes(text)
    profile.updated_at = time.time()
    profile.error = ""


# ---------------------------------------------------------------- overrides
def overrides_path() -> Path:
    return config_dir() / "overrides.yaml"


def load_overrides(path: Path | None = None) -> dict[str, Any]:
    path = path or overrides_path()
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise SubError(f"读取 {path} 失败：{exc}") from exc
    if not isinstance(data, dict):
        raise SubError(f"{path} 顶层必须是键值对")
    return data


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """只覆盖 patch 里显式出现的键，其它保持订阅原样。"""
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def merge_config(raw_text: str, overrides: dict[str, Any]) -> str:
    if not overrides:
        return raw_text
    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise SubError(f"订阅 yaml 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise SubError("订阅 yaml 顶层不是键值对")
    deep_merge(data, overrides)
    return yaml.safe_dump(
        data, allow_unicode=True, sort_keys=False, default_flow_style=False, width=4096
    )


# ------------------------------------------------------------------ install
def validate_config(candidate: Path, mihomo_dir: Path) -> None:
    """用内核自己校验配置，失败就不碰线上文件。"""
    binary = shutil.which("mihomo")
    if not binary:
        raise SubError("找不到 mihomo 可执行文件，无法校验配置")
    try:
        result = subprocess.run(
            [binary, "-t", "-d", str(mihomo_dir), "-f", str(candidate)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.SubprocessError as exc:
        raise SubError(f"校验配置失败：{exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise SubError("配置校验没通过：" + (detail[-1] if detail else "未知错误"))


def _sudo(*args: str) -> None:
    try:
        result = subprocess.run(
            ["sudo", "-n", *args], capture_output=True, text=True, timeout=60
        )
    except subprocess.SubprocessError as exc:
        raise SubError(f"sudo 执行失败：{exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        hint = "（需要 root 权限；当前没有免密 sudo）" if "password" in "".join(detail) else ""
        raise SubError(f"sudo {' '.join(args)} 失败{hint}：{detail[-1] if detail else ''}")


def install_config(candidate: Path, target: Path, *, dry_run: bool = False) -> Path | None:
    """备份后原子替换 target，返回备份路径。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = target.with_name(f"{target.name}.bak.{stamp}")
    if dry_run:
        return None
    if target.exists():
        _sudo("cp", "-a", str(target), str(backup))
    _sudo("install", "-m", "644", "-o", "root", "-g", "root", str(candidate), str(target))
    prune_backups(target)
    return backup


def prune_backups(target: Path, keep: int = KEEP_BACKUPS) -> None:
    backups = sorted(target.parent.glob(f"{target.name}.bak.*"))
    for stale in backups[:-keep] if len(backups) > keep else []:
        _sudo("rm", "-f", str(stale))


def write_candidate(text: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return destination


def mihomo_dir(settings_config: str) -> Path:
    """``/etc/mihomo/config.yaml`` → ``/etc/mihomo``。"""
    return Path(settings_config).parent


def temp_candidate(text: str) -> Path:
    handle, name = tempfile.mkstemp(prefix="tclash-", suffix=".yaml")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(text)
    return Path(name)


# ----------------------------------------------------------------- 编排链路
@dataclass
class Result:
    """一次更新的结果，给 CLI/TUI 展示。"""

    profile: Profile
    steps: list[str] = field(default_factory=list)
    backup: Path | None = None
    reloaded: bool = False

    @property
    def summary(self) -> str:
        return self.steps[-1] if self.steps else "完成"


async def update(
    profiles: Profiles,
    profile: Profile,
    settings: Any,
    *,
    reload: bool = True,
    dry_run: bool = False,
    api: Any = None,
) -> Result:
    """拉订阅 → 存缓存 → 合并覆盖项 → 校验 → 写盘 → 热重载。"""
    result = Result(profile=profile)
    raw_text, headers = await fetch(profile.url, timeout=max(20.0, settings.timeout * 3))
    apply_headers(profile, headers, raw_text)
    write_candidate(raw_text, profile.raw_path)
    result.steps.append(f"拉取成功：{profile.node_count} 个节点")
    profiles.save()
    return await _apply_cached(
        profiles, profile, settings, result, reload=reload, dry_run=dry_run, api=api
    )


async def use(
    profiles: Profiles,
    profile: Profile,
    settings: Any,
    *,
    reload: bool = True,
    dry_run: bool = False,
    api: Any = None,
) -> Result:
    """把已有缓存（必要时重拉）应用为一个生效配置。"""
    result = Result(profile=profile)
    if not profile.raw_path.exists():
        return await update(
            profiles, profile, settings, reload=reload, dry_run=dry_run, api=api
        )
    result.steps.append(f"使用缓存：{profile.raw_path.name}")
    return await _apply_cached(
        profiles, profile, settings, result, reload=reload, dry_run=dry_run, api=api
    )


async def _apply_cached(
    profiles: Profiles,
    profile: Profile,
    settings: Any,
    result: Result,
    *,
    reload: bool,
    dry_run: bool,
    api: Any,
) -> Result:
    raw_text = profile.raw_path.read_text(encoding="utf-8")
    overrides = load_overrides()
    merged = merge_config(raw_text, overrides)
    if overrides:
        result.steps.append("已合并本地覆盖项：" + ", ".join(sorted(overrides)))
    write_candidate(merged, profile.merged_path)

    target = Path(settings.config_path)
    validate_config(profile.merged_path, mihomo_dir(settings.config_path))
    result.steps.append("内核校验通过")
    if dry_run:
        result.steps.append(f"试运行：未写入 {target}")
        return result

    result.backup = install_config(profile.merged_path, target)
    result.steps.append(f"已写入 {target}")
    profiles.active = profile.name
    profiles.save()

    if reload:
        await _reload(settings, api)
        result.reloaded = True
        result.steps.append("已热重载，当前连接不受影响")
    return result


async def _reload(settings: Any, api: Any) -> None:
    from .api import MihomoAPI

    owned = api is None
    client = api or MihomoAPI(settings.controller, settings.secret, timeout=settings.timeout)
    try:
        await client.reload_config(settings.config_path)
    finally:
        if owned:
            await client.aclose()
