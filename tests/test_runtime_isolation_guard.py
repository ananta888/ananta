"""Owned sentinel databases prove early-import rejection before any test cleanup."""

import os
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.isolation_guard import (
    ERROR,
    require_data_directory,
    require_database_url,
    require_preloaded_runtime_isolation,
)


@pytest.mark.parametrize("actual", [None, "sqlite:///data/ananta.db", "sqlite:///:memory:", "postgresql://invalid/db"])
def test_no_database_other_than_the_exact_process_owned_test_database_is_accepted(actual):
    with pytest.raises(RuntimeError, match="^" + ERROR + "$"):
        require_database_url(actual, "sqlite:///file:synthetic?mode=memory&cache=shared&uri=true")


def test_url_normalization_accepts_only_the_same_named_memory_database():
    require_database_url(
        "sqlite:///file:synthetic?mode=memory&cache=shared&uri=true",
        "sqlite:///file:synthetic?uri=true&cache=shared&mode=memory",
    )


@pytest.mark.parametrize("actual", [None, "data", "/", "/tmp/foreign-test-directory"])
def test_data_cleanup_requires_the_exact_test_owned_directory(tmp_path, actual):
    with pytest.raises(RuntimeError, match="^" + ERROR + "$"):
        require_data_directory(actual, tmp_path)
    require_data_directory(tmp_path, tmp_path)


def test_preloaded_settings_are_checked_even_without_an_engine(tmp_path):
    settings = SimpleNamespace(effective_database_url="sqlite:///:memory:", data_dir="data")
    modules = {"agent.config": SimpleNamespace(settings=settings)}
    with pytest.raises(RuntimeError, match="^" + ERROR + "$"):
        require_preloaded_runtime_isolation(modules, "sqlite:///:memory:", tmp_path)
    settings.data_dir = tmp_path
    require_preloaded_runtime_isolation(modules, "sqlite:///:memory:", tmp_path)
    require_preloaded_runtime_isolation({}, "sqlite:///:memory:", tmp_path)


@pytest.mark.timeout(15)
@pytest.mark.parametrize("preload", ["synthetic-module", "actual-repository-import"])
def test_actual_conftest_rejects_preloaded_foreign_engine_without_touching_owned_sentinel(tmp_path, preload):
    database = tmp_path / "synthetic-sentinel.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT)")
        connection.execute("INSERT INTO sentinel VALUES ('preserve')")
    before = database.read_bytes()
    script = """
import runpy, sys
from types import SimpleNamespace
if sys.argv[2] == 'synthetic-module':
    sys.modules['agent.database'] = SimpleNamespace(engine=SimpleNamespace(url='sqlite:///' + sys.argv[1]))
else:
    from agent.repositories.meet_dialog_phases import TaskDialogPhases
try:
    runpy.run_path('tests/conftest.py')
except RuntimeError as error:
    assert str(error) == 'pytest_runtime_isolation_required_before_application_import'
else:
    raise AssertionError('foreign runtime reached test initialization')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(database), preload],
        env=os.environ | {"DATABASE_URL": "sqlite:///" + str(database), "DATA_DIR": str(tmp_path / "owned-data")},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, "early-import isolation guard failed; runtime details redacted"
    assert database.read_bytes() == before
