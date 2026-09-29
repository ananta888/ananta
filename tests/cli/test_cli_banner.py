from __future__ import annotations

import os

import pytest

from agent.cli.banner import get_banner, print_banner


def test_color_false_has_no_ansi_escapes():
    result = get_banner(color=False)
    assert "\x1b" not in result
    assert len(result) > 0


def test_color_true_contains_ansi_escapes(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    result = get_banner(color=True, width=120)
    assert "\x1b[38;2;" in result or "\x1b[48;2;" in result
    assert len(result) > 0


def test_ananta_no_banner_env_suppresses_output(monkeypatch):
    monkeypatch.setenv("ANANTA_NO_BANNER", "1")
    result = get_banner(color=True)
    assert result == ""


def test_no_color_env_returns_mono_fallback(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    result = get_banner()
    assert result != ""
    assert "\x1b" not in result


def test_width_large_selects_large_style(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    large = get_banner(color=True, width=120, style="auto")
    medium = get_banner(color=True, width=90, style="auto")
    assert large != medium


def test_width_medium_selects_medium_style(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    medium = get_banner(color=True, width=90, style="auto")
    small = get_banner(color=True, width=70, style="auto")
    assert medium != small


def test_width_small_uses_mono_fallback(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    result = get_banner(color=True, width=60, style="auto")
    assert "\x1b" not in result


def test_explicit_style_medium(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    result = get_banner(color=True, width=120, style="medium")
    assert "\x1b[38;2;" in result or "\x1b[48;2;" in result


def test_explicit_style_mono(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    result = get_banner(color=True, width=120, style="mono")
    assert "\x1b" not in result


def test_invalid_style_falls_back_to_auto(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    result = get_banner(color=True, width=120, style="invalid")
    assert "\x1b[38;2;" in result or "\x1b[48;2;" in result


def test_print_banner_outputs_to_stdout(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    print_banner(color=True, width=120)
    captured = capsys.readouterr()
    assert len(captured.out) > 0


def test_print_banner_empty_when_suppressed(capsys, monkeypatch):
    monkeypatch.setenv("ANANTA_NO_BANNER", "1")
    print_banner(color=True)
    captured = capsys.readouterr()
    assert captured.out == ""
