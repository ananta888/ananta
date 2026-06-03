from __future__ import annotations

import os
from contextlib import contextmanager

from client_surfaces.operator_tui import terminal


def test_get_tty_size_uses_ioctl(monkeypatch):
    class _FakeTTY:
        def fileno(self) -> int:
            return 9

    @contextmanager
    def _open():
        yield _FakeTTY()

    monkeypatch.setattr(terminal, "_open_dev_tty", _open)
    monkeypatch.setattr(terminal, "_tty_ioctl_size", lambda fd: (132, 41))

    cols, rows = terminal.get_tty_size((120, 32))
    assert (cols, rows) == (132, 41)


def test_get_tty_size_falls_back_when_no_tty(monkeypatch):
    @contextmanager
    def _open():
        raise OSError("no tty")
        yield  # pragma: no cover

    monkeypatch.setattr(terminal, "_open_dev_tty", _open)
    monkeypatch.setattr(
        terminal.shutil,
        "get_terminal_size",
        lambda *args, **kwargs: os.terminal_size((101, 29)),
    )

    cols, rows = terminal.get_tty_size((120, 32))
    assert (cols, rows) == (101, 29)


def test_get_tty_size_falls_back_when_ioctl_returns_invalid(monkeypatch):
    class _FakeTTY:
        def fileno(self) -> int:
            return 9

    @contextmanager
    def _open():
        yield _FakeTTY()

    monkeypatch.setattr(terminal, "_open_dev_tty", _open)
    monkeypatch.setattr(terminal, "_tty_ioctl_size", lambda fd: (0, 0))
    monkeypatch.setattr(
        terminal.shutil,
        "get_terminal_size",
        lambda *args, **kwargs: os.terminal_size((88, 27)),
    )

    cols, rows = terminal.get_tty_size((120, 32))
    assert (cols, rows) == (88, 27)
