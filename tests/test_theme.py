from __future__ import annotations

import pytest

from tclash.theme import THEME_CYCLE, detect_background, next_theme, resolve_theme


def test_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TCLASH_BACKGROUND", "light")
    assert detect_background() == "light"
    monkeypatch.setenv("TCLASH_BACKGROUND", "dark")
    assert detect_background() == "dark"


def test_colorfgbg_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TCLASH_BACKGROUND", raising=False)
    monkeypatch.setenv("COLORFGBG", "15;0")
    assert detect_background() == "dark"
    monkeypatch.setenv("COLORFGBG", "0;15")
    assert detect_background() == "light"


def test_resolve_theme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TCLASH_BACKGROUND", "dark")
    assert resolve_theme("auto") == "ansi-dark"
    assert resolve_theme("") == "ansi-dark"
    assert resolve_theme("textual-light") == "textual-light"


def test_next_theme_cycles() -> None:
    assert next_theme("ansi-dark") == "ansi-light"
    assert next_theme("nord") == THEME_CYCLE[0]
    assert next_theme("不存在的主题") == THEME_CYCLE[0]


def test_cycle_themes_are_builtin() -> None:
    from textual.theme import BUILTIN_THEMES

    for name in THEME_CYCLE:
        assert name in BUILTIN_THEMES, name
