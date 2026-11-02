"""Tests for scripts/check_worker_boundaries.py (CLN-029 / CLN-055).

Guards the hub/worker import-boundary detector: new coupling in either
direction must be flagged, whitelisted seams (contracts/ports, agent.config,
agent.auth) must pass, and the baseline format must stay parseable.
Headless and self-contained.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts" / "check_worker_boundaries.py"


@pytest.fixture()
def wb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    spec = importlib.util.spec_from_file_location("check_worker_boundaries_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    worker = tmp_path / "worker"
    agent_services = tmp_path / "agent" / "services"
    worker.mkdir(parents=True)
    agent_services.mkdir(parents=True)
    monkeypatch.setattr(module, "WORKER_ROOT", worker)
    monkeypatch.setattr(module, "AGENT_SERVICES_ROOT", agent_services)
    return module


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_rule_a_flags_worker_importing_agent_service(wb) -> None:
    _write(wb.WORKER_ROOT / "core" / "x.py", "from agent.services.mail_account_service import Y\n")
    findings = wb.rule_a_violations()
    assert [f["target"] for f in findings] == ["agent.services.mail_account_service"]


def test_rule_a_allows_contract_seams(wb) -> None:
    _write(
        wb.WORKER_ROOT / "core" / "x.py",
        "from agent.config import settings\nfrom agent.auth import check_registered_worker_auth\n"
        "from agent.services.mail_task_ports import Y\nfrom agent.services.vector_store_contract import Z\n"
        "from ananta_contracts.collaboration_workspace import W\n",
    )
    assert wb.rule_a_violations() == []


def test_rule_b_flags_agent_services_importing_worker(wb) -> None:
    _write(wb.AGENT_SERVICES_ROOT / "x.py", "from worker.core.execution_envelope import Y\n")
    findings = wb.rule_b_violations()
    assert [f["target"] for f in findings] == ["worker.core.execution_envelope"]


def test_clean_trees_have_no_findings(wb) -> None:
    _write(wb.WORKER_ROOT / "core" / "x.py", "import json\n")
    _write(wb.AGENT_SERVICES_ROOT / "y.py", "from agent.config import settings\n")
    assert wb.rule_a_violations() == []
    assert wb.rule_b_violations() == []


def test_function_level_lazy_import_is_not_flagged(wb) -> None:
    _write(
        wb.WORKER_ROOT / "core" / "x.py",
        "def f():\n    from agent.services.mail_account_service import Y\n",
    )
    assert wb.rule_a_violations() == []


def test_baseline_file_format_is_parseable() -> None:
    spec = importlib.util.spec_from_file_location("check_worker_boundaries_baseline", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    baseline = module._baseline_files()
    assert baseline, "reports/worker-boundary-baseline.txt must exist with the tracked coupling"
    for name in baseline:
        assert name.startswith(("worker/", "agent/services/")), name
