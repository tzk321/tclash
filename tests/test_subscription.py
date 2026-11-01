"""订阅链路：合并、请求头解析、profiles 持久化、编排顺序。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
import yaml

from tclash import subscription as sub
from tclash.subscription import (
    Profile,
    Profiles,
    SubError,
    apply_headers,
    count_nodes,
    deep_merge,
    human_bytes,
    merge_config,
)

SAMPLE = """\
mixed-port: 7890
mode: rule
dns:
    enable: true
    fake-ip-range: 198.18.0.1/16
    nameserver: [223.5.5.5]
proxies:
    - { name: '日本01', server: 1.2.3.4, port: 443, type: hysteria2, password: x }
    - { name: '美国01', server: 5.6.7.8, port: 443, type: hysteria2, password: x }
proxy-groups:
    - { name: '主组', type: select, proxies: ['日本01', '美国01'] }
    - { name: '自动', type: url-test, proxies: ['日本01', '美国01'] }
rules:
    - 'MATCH,主组'
"""


@pytest.fixture()
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把配置/数据目录挪到 tmp，避免碰真实的 ~/.config/tclash。"""
    config = tmp_path / "config"
    data = tmp_path / "data"
    monkeypatch.setattr(sub, "config_dir", lambda: config)
    monkeypatch.setattr(sub, "data_dir", lambda: data)
    return tmp_path


# ------------------------------------------------------------------ 合并
def test_deep_merge_only_touches_listed_keys() -> None:
    base = {"dns": {"fake-ip-range": "198.18.0.1/16", "nameserver": ["a"]}, "mode": "rule"}
    merged = deep_merge(base, {"dns": {"fake-ip-range": "28.0.0.1/8"}})
    assert merged["dns"]["fake-ip-range"] == "28.0.0.1/8"
    assert merged["dns"]["nameserver"] == ["a"]
    assert merged["mode"] == "rule"


def test_merge_config_keeps_subscription_content() -> None:
    text = merge_config(SAMPLE, {"dns": {"fake-ip-range": "28.0.0.1/8"}})
    data = yaml.safe_load(text)
    assert data["dns"]["fake-ip-range"] == "28.0.0.1/8"
    assert data["mixed-port"] == 7890
    assert [p["name"] for p in data["proxies"]] == ["日本01", "美国01"]
    assert "198.18.0.1/16" not in text


def test_merge_config_without_overrides_is_identity() -> None:
    assert merge_config(SAMPLE, {}) == SAMPLE


def test_merge_config_rejects_non_mapping() -> None:
    with pytest.raises(SubError):
        merge_config("- just\n- a list\n", {"mode": "rule"})


# ------------------------------------------------------------------ 响应头
def test_count_nodes_ignores_groups() -> None:
    assert count_nodes(SAMPLE) == 2
    assert count_nodes("not: [valid") == 0


def test_apply_headers_reads_userinfo() -> None:
    profile = Profile(name="测试", url="http://x")
    apply_headers(
        profile,
        {
            "subscription-userinfo": "upload=1024; download=2048; total=1000; expire=1800000000",
            "profile-update-interval": "12",
        },
        SAMPLE,
    )
    assert profile.upload == 1024
    assert profile.download == 2048
    assert profile.total == 1000
    assert profile.used == 3072
    assert profile.expire == 1800000000
    assert profile.interval == 12
    assert profile.node_count == 2


def test_apply_headers_tolerates_missing_headers() -> None:
    profile = Profile(name="测试", url="http://x")
    apply_headers(profile, {}, SAMPLE)
    assert profile.total == 0
    assert profile.interval is None  # 没声明间隔 → 不自动更新
    assert profile.node_count == 2


def test_human_bytes() -> None:
    assert human_bytes(0) == "0 B"
    assert human_bytes(1536) == "1.5 KB"
    assert human_bytes(100 * 1024**3) == "100.0 GB"


# ------------------------------------------------------------------ profiles
def test_profiles_roundtrip_with_chinese_names(dirs: Path) -> None:
    profiles = Profiles()
    profiles.add("测试机场", "https://a.example/sub?token=1")
    profiles.add("backup", "https://b.example/sub")
    profiles.active = "测试机场"
    path = profiles.save()

    assert path.stat().st_mode & 0o777 == 0o600
    again = Profiles.load()
    assert again.active == "测试机场"
    assert set(again.items) == {"测试机场", "backup"}
    assert again.items["测试机场"].url == "https://a.example/sub?token=1"


def test_profiles_add_keeps_first_active(dirs: Path) -> None:
    profiles = Profiles()
    profiles.add("a", "http://a")
    profiles.add("b", "http://b")
    assert profiles.active == "a"


def test_profiles_missing_file_is_empty(dirs: Path) -> None:
    assert Profiles.load().items == {}


def test_profiles_current_without_active_raises(dirs: Path) -> None:
    with pytest.raises(SubError):
        Profiles().current()


def test_profile_due_follows_interval() -> None:
    profile = Profile(name="x", url="http://x")
    assert profile.due  # 从未更新
    profile.updated_at = int(time.time())
    assert not profile.due  # 没声明间隔 → 不自动更新
    profile.interval = 1
    assert not profile.due
    profile.updated_at = int(time.time()) - 7200
    assert profile.due


# ------------------------------------------------------------------ 编排
def test_update_pipeline_merges_validates_and_installs(
    dirs: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    target = tmp_path / "etc" / "config.yaml"
    target.parent.mkdir()
    target.write_text("mode: old\n")

    async def fake_fetch(url: str, *, timeout: float) -> tuple[str, dict[str, str]]:
        calls.append(f"fetch:{url}")
        return SAMPLE, {"subscription-userinfo": "total=100; upload=1; download=2; expire=1"}

    monkeypatch.setattr(sub, "fetch", fake_fetch)
    monkeypatch.setattr(sub, "load_overrides", lambda *a, **k: {"dns": {"fake-ip-range": "28.0.0.1/8"}})
    monkeypatch.setattr(sub, "validate_config", lambda *a, **k: calls.append("validate"))
    monkeypatch.setattr(
        sub,
        "install_config",
        lambda candidate, dest, *, dry_run=False: calls.append(f"install:{dest}") or None,
    )

    async def fake_reload(settings: object, api: object) -> None:
        calls.append("reload")

    monkeypatch.setattr(sub, "_reload", fake_reload)

    profiles = Profiles()
    profile = profiles.add("测试", "http://sub/x")
    settings = type("S", (), {"config_path": str(target), "timeout": 5.0})()

    result = asyncio.run(sub.update(profiles, profile, settings))

    assert calls == ["fetch:http://sub/x", "validate", f"install:{target}", "reload"]
    assert profile.node_count == 2
    assert profiles.active == "测试"
    assert result.reloaded
    assert "已合并本地覆盖项：dns" in result.steps
    merged = yaml.safe_load(profile.merged_path.read_text())
    assert merged["dns"]["fake-ip-range"] == "28.0.0.1/8"
    assert profile.raw_path.read_text() == SAMPLE  # 原始缓存保持机场原样
    # profiles.toml 落盘后能读回
    assert Profiles.load().items["测试"].node_count == 2


def test_update_dry_run_writes_nothing(
    dirs: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "etc" / "config.yaml"
    target.parent.mkdir()
    target.write_text("mode: old\n")

    async def fake_fetch(url: str, *, timeout: float) -> tuple[str, dict[str, str]]:
        return SAMPLE, {}

    monkeypatch.setattr(sub, "fetch", fake_fetch)
    monkeypatch.setattr(sub, "load_overrides", lambda *a, **k: {})
    monkeypatch.setattr(sub, "validate_config", lambda *a, **k: None)

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run 不应该写盘")

    monkeypatch.setattr(sub, "install_config", boom)
    monkeypatch.setattr(sub, "_reload", boom)

    profiles = Profiles()
    profile = Profile(name="测试", url="http://sub/x")
    profiles.items[profile.name] = profile  # 不经过 add()，active 保持空
    settings = type("S", (), {"config_path": str(target), "timeout": 5.0})()

    result = asyncio.run(sub.update(profiles, profile, settings, dry_run=True))

    assert target.read_text() == "mode: old\n"
    assert profiles.active == ""  # 试运行不改当前订阅
    assert result.summary.startswith("试运行")


def test_update_failure_before_install_keeps_old_config(
    dirs: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "etc" / "config.yaml"
    target.parent.mkdir()
    target.write_text("mode: old\n")

    async def fake_fetch(url: str, *, timeout: float) -> tuple[str, dict[str, str]]:
        return SAMPLE, {}

    def reject(*args: object, **kwargs: object) -> None:
        raise SubError("配置校验没通过：bad proxy")

    monkeypatch.setattr(sub, "fetch", fake_fetch)
    monkeypatch.setattr(sub, "load_overrides", lambda *a, **k: {})
    monkeypatch.setattr(sub, "validate_config", reject)

    profiles = Profiles()
    profile = Profile(name="测试", url="http://sub/x")
    profiles.items[profile.name] = profile  # 不经过 add()，active 保持空
    settings = type("S", (), {"config_path": str(target), "timeout": 5.0})()

    with pytest.raises(SubError):
        asyncio.run(sub.update(profiles, profile, settings))
    assert target.read_text() == "mode: old\n"
    assert profiles.active == ""


def test_use_falls_back_to_update_when_no_cache(
    dirs: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[str] = []

    async def fake_update(*args: object, **kwargs: object) -> sub.Result:
        seen.append("update")
        return sub.Result(profile=args[1], steps=["拉取成功：1 个节点"])

    monkeypatch.setattr(sub, "update", fake_update)
    profiles = Profiles()
    profile = profiles.add("测试", "http://sub/x")
    settings = type("S", (), {"config_path": str(tmp_path / "c.yaml"), "timeout": 5.0})()

    result = asyncio.run(sub.use(profiles, profile, settings))
    assert seen == ["update"]
    assert result.steps == ["拉取成功：1 个节点"]
