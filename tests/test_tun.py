"""TUN 开关：文本级改配置、运行时 PATCH、持久化顺序。"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import yaml

from tclash import tun as tun_mod
from tclash.api import MihomoAPI
from tclash.settings import Settings
from tclash.subscription import SubError

LIVE = """\
mixed-port: 7890
mode: rule
dns:
  enable: true
  fake-ip-range: 28.0.0.1/8
proxies:
  - name: hk
    type: trojan
    tun: something
"""


# ------------------------------------------------------------------ 文本改写
def test_adds_block_when_missing() -> None:
    out = tun_mod.patch_tun_yaml(LIVE, True)
    assert out.startswith(LIVE)  # 其余内容逐字保留
    assert out.endswith("tun:\n  enable: true\n")
    assert "fake-ip-range: 28.0.0.1/8" in out


def test_flips_existing_block_and_keeps_comment() -> None:
    text = "tun:\n  enable: false # 手动关的\n  stack: mixed\nmode: rule\n"
    out = tun_mod.patch_tun_yaml(text, True)
    assert "  enable: true # 手动关的" in out
    assert "  stack: mixed" in out
    assert out.endswith("mode: rule\n")


def test_inserts_enable_into_existing_block() -> None:
    text = "tun:\n  stack: mixed\n  auto-route: true\nmode: rule\n"
    assert tun_mod.patch_tun_yaml(text, True) == (
        "tun:\n  enable: true\n  stack: mixed\n  auto-route: true\nmode: rule\n"
    )


def test_inline_mapping() -> None:
    flipped = tun_mod.patch_tun_yaml("tun: {enable: false, stack: mixed}\n", True)
    assert flipped == "tun: {enable: true, stack: mixed}\n"
    added = tun_mod.patch_tun_yaml("tun: {stack: mixed}\n", False)
    assert added == "tun: {enable: false, stack: mixed}\n"
    empty = tun_mod.patch_tun_yaml("tun: {}\n", True)
    assert empty == "tun: {enable: true}\n"


def test_null_value_becomes_block() -> None:
    assert tun_mod.patch_tun_yaml("tun: null\nmode: rule\n", True) == (
        "tun:\n  enable: true\nmode: rule\n"
    )


def test_ignores_indented_tun_key() -> None:
    out = tun_mod.patch_tun_yaml(LIVE, False)
    assert "    tun: something" in out  # 节点里的同名字段没被当顶层键
    assert yaml.safe_load(out)["proxies"][0]["tun"] == "something"
    assert yaml.safe_load(out)["tun"]["enable"] is False


def test_missing_trailing_newline() -> None:
    out = tun_mod.patch_tun_yaml("mode: rule", True)
    assert out == "mode: rule\ntun:\n  enable: true\n"


# -------------------------------------------------------------------- 读状态
def test_state_readers(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(LIVE + "tun:\n  enable: true\n", encoding="utf-8")
    assert tun_mod.file_enabled(cfg) is True
    assert tun_mod.file_enabled(tmp_path / "missing.yaml") is None
    cfg.write_text("mode: rule\n", encoding="utf-8")
    assert tun_mod.file_enabled(cfg) is False

    override = tmp_path / "overrides.yaml"
    override.write_text("dns:\n  fake-ip-range: 28.0.0.1/8\n", encoding="utf-8")
    assert tun_mod.override_enabled(override) is None
    override.write_text("dns:\n  fake-ip-range: 28.0.0.1/8\ntun:\n  enable: true\n", encoding="utf-8")
    assert tun_mod.override_enabled(override) is True


def test_write_override_keeps_other_keys(tmp_path) -> None:
    path = tmp_path / "overrides.yaml"
    original = "# 注释留着\ndns:\n  fake-ip-range: 28.0.0.1/8\n"
    path.write_text(original, encoding="utf-8")
    tun_mod.write_override(True, path)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# 注释留着\ndns:\n  fake-ip-range: 28.0.0.1/8\n")
    assert "tun:\n  enable: true" in text


# -------------------------------------------------------------------- API
def test_api_set_tun_patches_only_tun() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    async def go() -> None:
        client = httpx.AsyncClient(
            base_url="http://test", transport=httpx.MockTransport(handler), timeout=2
        )
        async with MihomoAPI("127.0.0.1:9090", client=client) as api:
            await api.set_tun(True)

    asyncio.run(go())
    assert len(seen) == 1
    assert seen[0].method == "PATCH"
    assert seen[0].url.path == "/configs"
    assert json.loads(seen[0].content) == {"tun": {"enable": True}}


# -------------------------------------------------------------------- 编排
class FakeAPI:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def set_tun(self, enable: bool) -> None:
        self.calls.append(("set_tun", enable))

    async def reload_config(self, path: str | None = None) -> None:
        self.calls.append(("reload", path))


def test_persist_writes_both_files_then_reloads(monkeypatch, tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(LIVE, encoding="utf-8")
    override = tmp_path / "overrides.yaml"
    override.write_text("dns:\n  fake-ip-range: 28.0.0.1/8\n", encoding="utf-8")
    monkeypatch.setattr(tun_mod.subscription, "overrides_path", lambda: override)

    order: list[str] = []

    def fake_validate(candidate, mihomo_dir):  # noqa: ARG001
        order.append("validate")

    def fake_install(candidate, target, **_):
        order.append("install")
        target.write_text(candidate.read_text(encoding="utf-8"), encoding="utf-8")
        return target.with_name("config.yaml.bak.test")

    monkeypatch.setattr(tun_mod.subscription, "validate_config", fake_validate)
    monkeypatch.setattr(tun_mod.subscription, "install_config", fake_install)

    api = FakeAPI()
    settings = Settings(config_path=str(cfg))
    result = asyncio.run(tun_mod.set_tun(api, settings, True))

    assert order == ["validate", "install"]
    assert api.calls == [("reload", str(cfg))]
    live = cfg.read_text(encoding="utf-8")
    assert "fake-ip-range: 28.0.0.1/8" in live
    assert yaml.safe_load(live)["tun"]["enable"] is True
    data = yaml.safe_load(override.read_text(encoding="utf-8"))
    assert data["dns"]["fake-ip-range"] == "28.0.0.1/8"
    assert data["tun"]["enable"] is True
    assert result.runtime is True and result.boot is True
    assert result.backup is not None


def test_persist_failure_changes_nothing(monkeypatch, tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(LIVE, encoding="utf-8")
    override = tmp_path / "overrides.yaml"
    override.write_text("dns:\n  fake-ip-range: 28.0.0.1/8\n", encoding="utf-8")
    monkeypatch.setattr(tun_mod.subscription, "overrides_path", lambda: override)

    def bad_validate(candidate, mihomo_dir):  # noqa: ARG001
        raise SubError("配置校验没通过")

    monkeypatch.setattr(tun_mod.subscription, "validate_config", bad_validate)

    with pytest.raises(SubError):
        asyncio.run(tun_mod.set_tun(FakeAPI(), Settings(config_path=str(cfg)), True))

    assert cfg.read_text(encoding="utf-8") == LIVE
    assert "tun" not in yaml.safe_load(override.read_text(encoding="utf-8"))


def test_now_only_skips_files(monkeypatch, tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(LIVE + "tun:\n  enable: true\n", encoding="utf-8")

    def boom(*args, **kwargs):
        raise AssertionError("--now 不应该写文件")

    monkeypatch.setattr(tun_mod.subscription, "validate_config", boom)
    monkeypatch.setattr(tun_mod.subscription, "install_config", boom)
    monkeypatch.setattr(tun_mod, "write_override", boom)

    api = FakeAPI()
    result = asyncio.run(tun_mod.set_tun(api, Settings(config_path=str(cfg)), False, persist=False))
    assert api.calls == [("set_tun", False)]
    assert result.runtime is False
    assert result.boot is True  # 文件里仍然是开，重启会回到开
    assert cfg.read_text(encoding="utf-8").endswith("tun:\n  enable: true\n")
