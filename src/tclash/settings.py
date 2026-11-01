"""配置读写：``~/.config/tclash/config.toml``。

配置文件不存在时会尝试从 mihomo 的 config.yaml 里嗅探
``external-controller`` / ``secret``，让第一次运行就能直接用。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

APP_NAME = "tclash"
DEFAULT_CONTROLLER = "http://127.0.0.1:9090"
DEFAULT_TEST_URL = "http://www.gstatic.com/generate_204"
DEFAULT_MIHOMO_DIR = "/etc/mihomo"
VALID_MODES = ("rule", "global", "direct")


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / APP_NAME


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / APP_NAME


def profiles_path() -> Path:
    return config_dir() / "profiles.toml"


@dataclass
class Settings:
    controller: str = DEFAULT_CONTROLLER
    secret: str = ""
    timeout: float = 8.0
    test_timeout_ms: int = 5000
    concurrency: int = 8
    test_url: str = DEFAULT_TEST_URL
    config_path: str = f"{DEFAULT_MIHOMO_DIR}/config.yaml"
    # 阶段 3：自动测速切换
    whitelist: list[str] = field(default_factory=list)
    auto_switch: bool = False
    auto_interval: int = 600
    auto_group: str = ""

    # ------------------------------------------------------------------ load
    @classmethod
    def path(cls) -> Path:
        return config_dir() / "config.toml"

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or cls.path()
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                data = {}

        known = {f.name for f in fields(cls)}
        settings = cls(**{k: v for k, v in data.items() if k in known})

        # 环境变量优先级最高
        if env := os.environ.get("TCLASH_CONTROLLER"):
            settings.controller = env
        if (env := os.environ.get("TCLASH_SECRET")) is not None:
            settings.secret = env

        if not path.exists():
            settings.discover()
        return settings

    def discover(self) -> None:
        """从 mihomo 的配置文件嗅探控制器地址（只读，失败就保持默认）。"""
        try:
            import yaml

            cfg_file = Path(self.config_path)
            if not cfg_file.is_file():
                return
            raw = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 - 嗅探失败不应该影响启动
            return

        controller = raw.get("external-controller")
        if isinstance(controller, str) and controller.strip():
            addr = controller.strip()
            if addr.startswith(("http://", "https://")):
                self.controller = addr
            else:
                self.controller = f"http://{addr}"
            secret = raw.get("secret")
            if isinstance(secret, str):
                self.secret = secret

    # ------------------------------------------------------------------ save
    def save(self, path: Path | None = None) -> Path:
        path = path or self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# tclash 配置（URL/secret 属于秘密，文件权限 600）", ""]
        for f in fields(self):
            value = getattr(self, f.name)
            lines.append(f"{f.name} = {_toml_value(value)}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'
