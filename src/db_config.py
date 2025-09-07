import os
from urllib.parse import urlparse, urlunparse

# Central configuration for database connections with test/E2E isolation
# Strategy:
# - If E2E_DATABASE_URL is set, use it as authoritative.
# - Else, if test/e2e flags are present (TEST_MODE, ENABLE_E2E_TEST_MODELS, PLAYWRIGHT_*, VERIFY_AGENT, CI)
#   and E2E_ISOLATE_DB is not '0', use the base DATABASE_URL but with database name suffixed by '_e2e'.
# - Else, use DATABASE_URL as provided (or the default in docker).
# - Optionally ensure the target database exists (best effort; ignore on failure).

def _with_db_name(db_url: str, new_db: str) -> str:
    parsed = urlparse(db_url)
    return urlunparse(parsed._replace(path=f"/{new_db}"))


def _compute_base_url() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@db:5432/ananta")


def _should_isolate_to_e2e() -> bool:
    # Allow opt-out by setting E2E_ISOLATE_DB=0
    if str(os.environ.get("E2E_ISOLATE_DB", "1")).lower() in ("0", "false", "no"):
        return False
    flags = [
        os.environ.get("TEST_MODE"),
        os.environ.get("ENABLE_E2E_TEST_MODELS"),
        os.environ.get("PLAYWRIGHT_BASE_URL"),
        os.environ.get("PLAYWRIGHT_SKIP_WEBSERVER"),
        os.environ.get("VERIFY_AGENT"),
        os.environ.get("CI"),
    ]
    return any(bool(f) and str(f).lower() not in ("0", "false", "no") for f in flags)


def _ensure_db_exists(db_url: str) -> None:
    # Best-effort creation of the target database if it doesn't exist
    try:
        import psycopg2  # type: ignore
        from psycopg2 import sql as _sql  # type: ignore
    except Exception:
        return  # silently skip if driver not available
    try:
        parsed = urlparse(db_url)
        dbname = (parsed.path or "/ananta").lstrip("/")
        admin_url = urlunparse(parsed._replace(path="/postgres"))
        conn = psycopg2.connect(admin_url)
        conn.autocommit = True
        cur = conn.cursor()
        try:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            exists = cur.fetchone() is not None
            if not exists:
                cur.execute(_sql.SQL("CREATE DATABASE {};").format(_sql.Identifier(dbname)))
        finally:
            cur.close()
            conn.close()
    except Exception:
        # Ignore errors to avoid breaking startup; the app can still fail later if DB truly unreachable
        pass


def get_database_url() -> str:
    # Highest precedence: explicit E2E_DATABASE_URL
    e2e_url = os.environ.get("E2E_DATABASE_URL")
    if e2e_url:
        _ensure_db_exists(e2e_url)
        return e2e_url

    base = _compute_base_url()

    # If env explicitly points to a local test DB (common in pytest via conftest), do not alter it
    parsed = urlparse(base)
    host = parsed.hostname or ""
    dbname = (parsed.path or "/ananta").lstrip("/")
    if host in ("localhost", "127.0.0.1") or "test" in dbname.lower():
        return base

    # Otherwise, apply E2E isolation when appropriate
    if _should_isolate_to_e2e():
        e2e_db = f"{dbname}_e2e"
        url = _with_db_name(base, e2e_db)
        _ensure_db_exists(url)
        return url

    # Default: use as is
    return base


# Keep compatibility: module-level constant
DATABASE_URL = get_database_url()
