from __future__ import annotations

import os
import pytest

from agent.cli.logo_assets import load_logo, logo_line_count, clear_asset_cache, strip_ansi_from_logo


def test_load_logo_returns_string():
    clear_asset_cache()
    result = load_logo(width=90, color=False)
    assert isinstance(result, str)
    assert len(result) > 0


def test_load_logo_respects_max_lines():
    clear_asset_cache()
    result = load_logo(width=90, color=False, max_lines=8)
    lines = result.split("\n")
    assert len(lines) <= 8


def test_load_logo_respects_disable_env(monkeypatch):
    monkeypatch.setenv("ANANTA_TUI_SPLASH", "0")
    clear_asset_cache()
    result = load_logo(width=90, color=True)
    assert result == ""


def test_load_logo_returns_empty_for_no_color():
    clear_asset_cache()
    result = load_logo(width=90, color=True)
    assert isinstance(result, str)


def test_logo_line_count():
    clear_asset_cache()
    count = logo_line_count(width=90, color=False)
    assert count > 0


def test_strip_ansi():
    text = "\x1b[38;2;255;0;0mhello\x1b[0m"
    stripped = strip_ansi_from_logo(text)
    assert stripped == "hello"


def test_strip_ansi_no_ansi():
    text = "hello world"
    assert strip_ansi_from_logo(text) == "hello world"
