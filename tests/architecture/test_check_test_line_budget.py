"""Tests for scripts/check_test_line_budget.py (CLN-046)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts" / "check_test_line_budget.py"


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("check_test_line_budget_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_budget_catches_new_file(tmp_path: Path, gate, monkeypatch: pytest.MonkeyPatch) -> None:
    big = tmp_path / "test_big.py"
    big.write_text("# padding\n" * (gate.BUDGET_LINES + 1), encoding="utf-8")
    monkeypatch.setattr(gate, "TESTS_ROOT", tmp_path)
    assert gate.over_budget_files() == {str(big): gate.BUDGET_LINES + 1}


def test_budget_ignores_small_file(tmp_path: Path, gate, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "test_small.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    monkeypatch.setattr(gate, "TESTS_ROOT", tmp_path)
    assert gate.over_budget_files() == {}


def test_allowlist_is_in_sync(gate) -> None:
    import json

    data = json.loads((ROOT / "reports" / "test-line-budget-allowlist.json").read_text(encoding="utf-8"))
    listed = {e["path"] for e in data["allowlist"]}
    assert listed == set(gate.over_budget_files()), (
        "reports/test-line-budget-allowlist.json out of sync — update only after SHRINKING files"
    )
