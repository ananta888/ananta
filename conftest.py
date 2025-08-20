"""Ensure project root is importable for tests and isolate DB for pytest.

We enforce a dedicated PostgreSQL test database so running systems/data are never
modified by unit/integration tests. The DB is created at session start and
dropped at session finish, regardless of test outcomes.
"""
import os
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest

# Optional: psycopg2 is in requirements; guard import to avoid discovery errors
try:  # pragma: no cover - import guard
    import psycopg2  # type: ignore
except Exception:  # pragma: no cover
    psycopg2 = None  # type: ignore

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ensure_psycopg2():
    if psycopg2 is None:
        raise RuntimeError(
            "psycopg2 is required for test DB isolation. Ensure psycopg2-binary is installed."
        )


def _with_db_name(db_url: str, new_db: str) -> str:
    """Return a copy of db_url with database name replaced by new_db."""
    parsed = urlparse(db_url)
    # parsed.path includes leading '/', e.g., '/ananta'
    return urlunparse(parsed._replace(path=f"/{new_db}"))


# Use a dedicated test database for all pytest runs by default
DEFAULT_BASE_URL = os.environ.get("DATABASE_BASE_URL", "postgresql://postgres@localhost:5432/ananta")
TEST_DB_NAME = os.environ.get("TEST_DB_NAME", "ananta_test")
TEST_DB_URL = _with_db_name(DEFAULT_BASE_URL, TEST_DB_NAME)

# Set DATABASE_URL early so tests importing modules use the test DB
os.environ.setdefault("DATABASE_URL", TEST_DB_URL)


def _create_test_db():
    _ensure_psycopg2()
    admin_url = _with_db_name(TEST_DB_URL, "postgres")
    conn = psycopg2.connect(admin_url)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        # Create DB if not exists
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,))
        exists = cur.fetchone() is not None
        if not exists:
            cur.execute(f"CREATE DATABASE {TEST_DB_NAME}")
    finally:
        cur.close()
        conn.close()


def _init_test_db_schema():
    # Initialize schemas/tables in the test DB
    try:
        from src.db import init_db  # lazy import to pick up env var
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"Unable to import init_db for schema setup: {e}")
    init_db()


def _drop_test_db():
    _ensure_psycopg2()
    admin_url = _with_db_name(TEST_DB_URL, "postgres")
    conn = psycopg2.connect(admin_url)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        # Terminate all connections to the test DB
        cur.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = %s AND pid <> pg_backend_pid()
            """,
            (TEST_DB_NAME,),
        )
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
    finally:
        cur.close()
        conn.close()


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session):
    """Create and initialize the dedicated test database before any tests run."""
    _create_test_db()
    _init_test_db_schema()


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """Drop the dedicated test database after all tests, regardless of outcome."""
    try:
        _drop_test_db()
    except Exception:
        # As a safety net, leave the DB if drop fails (e.g., local permissions). It still isolates prod.
        pass
