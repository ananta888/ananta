"""Tests for scripts/check_singleton_freeze.py (CLN-035).

Guards the ratchet that freezes hand-rolled get_*_service() boilerplate:
detection of accessor modules, the new/stale diff against the freeze file,
and the freeze file's own consistency. Headless and self-contained.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts" / "check_singleton_freeze.py"


@pytest.fixture(scope="module")
def csf():
    spec = importlib.util.spec_from_file_location("check_singleton_freeze_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_detector_finds_accessor_modules(csf) -> None:
    modules = csf.modules_with_service_accessors()
    assert modules, "the freeze ratchet expects the existing accessor modules"
    assert any("agent" in m for m in modules)


def test_freeze_file_matches_current_state(csf) -> None:
    frozen = {
        line.strip()
        for line in (ROOT / "reports" / "singleton-freeze.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert frozen == csf.modules_with_service_accessors(), (
        "reports/singleton-freeze.txt is out of sync — run "
        "`python scripts/check_singleton_freeze.py --update` after removing (not adding) accessors"
    )


def test_new_accessor_is_detected(tmp_path: Path, csf) -> None:
    service = tmp_path / "worker" / "example_service.py"
    service.parent.mkdir(parents=True)
    service.write_text(
        "class ExampleService:\n    pass\n\n\ndef get_example_service() -> ExampleService:\n    return ExampleService()\n",
        encoding="utf-8",
    )
    monkey_root = tmp_path
    original = csf.SCAN_ROOTS
    csf.SCAN_ROOTS = (str(monkey_root / "agent"), str(monkey_root / "worker"))
    try:
        assert csf.modules_with_service_accessors() == {str(service)}
    finally:
        csf.SCAN_ROOTS = original
