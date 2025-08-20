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
import shutil

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


@pytest.fixture(autouse=True)
def _isolate_config_file(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Ensure tests never read or modify the real data/config.json.

    For each test, copy data/config.json (if exists) to a unique temp file and set
    ANANTA_CONFIG_PATH to that file so application code reads the temp copy.
    Any mutation by a test affects only the temp copy and is discarded after the test.
    """
    base_dir = Path(__file__).resolve().parent
    data_config = base_dir / "data" / "config.json"
    # Some environments may only ship root config.json; prefer data/config.json if present
    if not data_config.is_file():
        data_config = base_dir / "config.json"
    # Create temp copy per test
    tmp_dir = tmp_path_factory.mktemp("cfg")
    tmp_cfg = tmp_dir / "config.json"
    if data_config.is_file():
        shutil.copyfile(str(data_config), str(tmp_cfg))
    else:
        # If no config present, create an empty default
        tmp_cfg.write_text("{}", encoding="utf-8")
    # Point controller to the temp config path
    monkeypatch.setenv("ANANTA_CONFIG_PATH", str(tmp_cfg))
    # Yield to test; monkeypatch will auto-revert env var afterwards
    yield


@pytest.fixture(autouse=True)
def _reset_controller_runtime_state():
    """Reset controller in-memory state after each test.

    Tests may set or rely on controller._CONFIG_OVERRIDE or enqueue tasks into
    the fallback queue. To avoid cross-test contamination (e.g., agents list
    being overwritten), clear those after every test.
    """
    try:
        # Defer import so that modules under test can patch environment first
        import controller.controller as ctrl  # type: ignore
    except Exception:
        ctrl = None  # type: ignore
    # Run the test first
    yield
    # Now reset runtime state
    try:
        if ctrl is not None:
            try:
                # Reset any in-memory config override set by tests or routes
                setattr(ctrl, "_CONFIG_OVERRIDE", None)
            except Exception:
                pass
            try:
                # Clear the in-memory fallback task queue
                q = getattr(ctrl, "_FALLBACK_Q", None)
                if q is not None:
                    if hasattr(q, "clear"):
                        q.clear()  # type: ignore[attr-defined]
                    else:
                        # Fallback: pop until empty
                        while True:
                            try:
                                q.popleft()  # type: ignore[attr-defined]
                            except Exception:
                                break
            except Exception:
                pass
    except Exception:
        # Never fail a test due to cleanup issues
        pass
