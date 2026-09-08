"""Fail closed before test initialization/cleanup can touch a non-test runtime."""

from pathlib import Path

from sqlalchemy.engine import make_url

ERROR = "pytest_runtime_isolation_required_before_application_import"


def require_database_url(actual, expected):
    try:
        matches = make_url(actual) == make_url(expected)
    except Exception:
        matches = False
    if not matches:
        raise RuntimeError(ERROR)


def require_data_directory(actual, expected):
    try:
        matches = Path(actual).resolve() == Path(expected).resolve()
    except (TypeError, ValueError, OSError):
        matches = False
    if not matches:
        raise RuntimeError(ERROR)


def require_preloaded_runtime_isolation(modules, database_url, data_directory):
    database = modules.get("agent.database")
    if database is not None:
        require_database_url(getattr(getattr(database, "engine", None), "url", None), database_url)
    config = modules.get("agent.config")
    if config is not None:
        settings = getattr(config, "settings", None)
        require_database_url(getattr(settings, "effective_database_url", None), database_url)
        require_data_directory(getattr(settings, "data_dir", None), data_directory)
