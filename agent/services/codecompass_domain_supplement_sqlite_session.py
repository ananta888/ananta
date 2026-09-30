"""Immutable, read-only SQLite sessions for CodeCompass domain supplements."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from agent.services.codecompass_domain_supplement_models import (
    MAX_DOMAIN_SUPPLEMENT_COMPRESSED_CHUNK_BYTES,
    CodeCompassDomainSupplementError,
    invoke_domain_supplement_checkpoint,
)

_MAX_COMPRESSED_CHUNK_BYTES = MAX_DOMAIN_SUPPLEMENT_COMPRESSED_CHUNK_BYTES
_SQLITE_PROGRESS_OPCODES = 10_000
_checkpoint = invoke_domain_supplement_checkpoint


@contextmanager
def sqlite_checkpoint_progress(
    connection: sqlite3.Connection,
    callback: Callable[[], object] | None,
) -> Iterator[None]:
    """Interrupt long read-only SQLite work through a caller-owned deadline."""

    if callback is None:
        yield
        return
    failure: list[Exception] = []

    def progress() -> int:
        try:
            callback()
        except Exception as exc:  # SQLite callbacks cannot propagate directly.
            if not failure:
                failure.append(exc)
            return 1
        return 0

    connection.set_progress_handler(progress, _SQLITE_PROGRESS_OPCODES)
    try:
        _checkpoint(callback)
        try:
            yield
        except sqlite3.OperationalError as exc:
            if failure:
                raise failure[0] from exc
            raise
        if failure:
            raise failure[0]
        _checkpoint(callback)
    finally:
        connection.set_progress_handler(None, 0)


@contextmanager
def open_domain_supplement_connection(
    path: Path,
    *,
    checkpoint: Callable[[], object] | None = None,
) -> Iterator[sqlite3.Connection]:
    try:
        uri = f"{path.resolve(strict=True).as_uri()}?mode=ro&immutable=1"
        connection = sqlite3.connect(
            uri,
            uri=True,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        if hasattr(connection, "setlimit"):
            connection.setlimit(
                sqlite3.SQLITE_LIMIT_LENGTH,
                _MAX_COMPRESSED_CHUNK_BYTES,
            )
            connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
            connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 64)
            connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 64 * 1024)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        with sqlite_checkpoint_progress(connection, checkpoint):
            yield connection
    except (OSError, sqlite3.Error) as exc:
        raise CodeCompassDomainSupplementError("domain_supplement_sqlite_invalid") from exc
    finally:
        if "connection" in locals():
            connection.close()
