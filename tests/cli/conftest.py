"""Shared fixtures for CLI tests."""
from __future__ import annotations

import io
import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from typing import Any

import pytest


@contextmanager
def capture_output():
    """Capture stdout and stderr as strings."""
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        yield out, err


def run_dispatch(dispatch_fn, argv: list[str]) -> tuple[int, str, str]:
    """Call dispatch_fn(argv), capture output, return (exit_code, stdout, stderr).

    SystemExit is caught and its code returned.
    """
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    rc = 0
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            result = dispatch_fn(list(argv))
        rc = 0 if result is None else int(result)
    except SystemExit as exc:
        rc = int(exc.code) if exc.code is not None else 0
    return rc, out_buf.getvalue(), err_buf.getvalue()


@pytest.fixture
def run():
    """Fixture that returns run_dispatch."""
    return run_dispatch
