"""Harness-owned SQLite selection; callers choose a mode, never a runtime path."""

import os
import tempfile
from functools import cache
from pathlib import Path


@cache
def _database_url(process_id, mode):
    if mode == "memory":
        return f"sqlite:///file:ananta-pytest-{process_id}?mode=memory&cache=shared&uri=true"
    if mode != "wal":
        raise RuntimeError("pytest_database_mode_invalid")
    # Retained for failed-test diagnostics; never reuse or clean another run's
    # directory. Caching covers a repeated conftest import in this process only.
    directory = Path(tempfile.mkdtemp(prefix=f"ananta-pytest-db-{process_id}-"))
    return "sqlite:///" + str(directory / "test.db")


def isolated_database_url(mode):
    return _database_url(os.getpid(), mode)
