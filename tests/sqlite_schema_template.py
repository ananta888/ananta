"""Fast schema creation for file-backed SQLite test databases.

Many tests build a fresh file database per test with ``SQLModel.metadata.create_all(engine)``. For the
~330-table schema that costs 10-14 s: SQLite commits every DDL statement to disk. This module installs a
test-only fast path on ``MetaData.create_all``: when the target is an engine on a SQLite *file* that does not
exist yet (or is empty), the schema is copied from a template built once per process -- with durability
switched off, since the template is only ever read -- instead of being created statement by statement.

The copy holds exactly what ``create_all`` would have produced (same metadata, same table selection, the
metadata's DDL events included), so the tests are unaffected; everything else (in-memory databases,
connections, existing files) goes through the original ``create_all``. ``ANANTA_TEST_SQLITE_TEMPLATE=0``
switches the fast path off.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import Engine

_original_create_all = MetaData.create_all
_templates: dict[tuple[Any, ...], Path] = {}
_lock = threading.Lock()
_template_root: Path | None = None


def _sqlite_file(bind: Any) -> Path | None:
    if not isinstance(bind, Engine) or bind.dialect.name != "sqlite":
        return None
    database = bind.url.database
    if not database or database == ":memory:" or database.startswith("file:") or bind.url.query.get("mode"):
        return None
    return Path(database)


def _template_key(metadata: MetaData, tables: Any, checkfirst: bool) -> tuple[Any, ...]:
    selected = tuple(sorted(table.fullname for table in tables)) if tables is not None else None
    # the metadata object itself (not only its id): tables added later change the schema
    return (id(metadata), tuple(sorted(metadata.tables)), selected, checkfirst)


def _build_template(metadata: MetaData, tables: Any, checkfirst: bool) -> Path:
    global _template_root
    if _template_root is None:
        _template_root = Path(tempfile.mkdtemp(prefix="ananta-sqlite-templates-"))
    path = _template_root / f"schema-{len(_templates)}.sqlite3"
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def _fast(dbapi_connection: Any, _record: Any) -> None:  # a template needs no durability
        dbapi_connection.execute("PRAGMA synchronous=OFF")
        dbapi_connection.execute("PRAGMA journal_mode=MEMORY")

    try:
        _original_create_all(metadata, engine, tables=tables, checkfirst=checkfirst)
    finally:
        engine.dispose()
    return path


def _create_all(self: MetaData, bind: Any, tables: Any = None, checkfirst: bool = True) -> None:
    target = _sqlite_file(bind)
    if target is None or (target.exists() and target.stat().st_size > 0):
        return _original_create_all(self, bind, tables=tables, checkfirst=checkfirst)
    key = _template_key(self, tables, checkfirst)
    with _lock:
        template = _templates.get(key)
        if template is None:
            template = _templates[key] = _build_template(self, tables, checkfirst)
    target.parent.mkdir(parents=True, exist_ok=True)
    bind.dispose()  # no pooled connection may hold the (still empty) file open across the copy
    shutil.copyfile(template, target)
    # like create_all, leave one checked-in connection in the pool (tests may patch the clock afterwards)
    with bind.connect():
        pass
    return None


def install() -> None:
    if str(os.environ.get("ANANTA_TEST_SQLITE_TEMPLATE", "1")).strip().lower() in {"0", "false", "no", "off"}:
        return
    MetaData.create_all = _create_all  # type: ignore[method-assign]


__all__ = ["install"]
