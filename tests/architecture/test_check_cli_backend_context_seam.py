"""Tests for scripts/check_cli_backend_context_seam.py (CLN-052).

Guards that cli_backends reaches agent.services only through the
default_context seam, and that the seam is monkeypatchable end to end.
Headless and self-contained.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts" / "check_cli_backend_context_seam.py"


@pytest.fixture(scope="module")
def seam_check():
    spec = importlib.util.spec_from_file_location("check_cli_backend_context_seam", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_repository_cli_backends_are_clean(seam_check) -> None:
    assert seam_check.main() == 0, "direct agent.services imports crept into cli_backends"


def test_detector_flags_direct_service_import(tmp_path: Path, seam_check, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = tmp_path / "agent" / "cli_backends"
    pkg.mkdir(parents=True)
    (pkg / "bad_module.py").write_text(
        "from agent.services.some_service import get_some_service\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(seam_check, "ROOT", pkg)
    monkeypatch.setattr(seam_check, "ALLOWED_MODULE", "agent/cli_backends/context.py")
    assert seam_check.main() == 1


def test_default_context_accessors_are_monkeypatchable() -> None:
    from agent.cli_backends import context as ctx

    original = ctx.default_context.decision_service

    class _Fake:
        @staticmethod
        def get():
            return "fake"

    ctx.default_context.decision_service = _Fake.get
    try:
        assert ctx.default_context.decision_service() == "fake"
    finally:
        ctx.default_context.decision_service = original
    assert ctx.default_context.decision_service is original
