from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.run_semantic_media_security_gate import (
    DEFAULT_MATRIX,
    evaluate_matrix,
    execute_automated_evidence,
)


def _matrix():
    return json.loads(DEFAULT_MATRIX.read_text(encoding="utf-8"))


def test_matrix_static_validation_binds_all_referenced_test_sources() -> None:
    evidence, summary = evaluate_matrix(_matrix())
    assert evidence.status == "passed"
    assert summary["referenced_test_file_count"] == 11
    assert summary["covered_threat_count"] == 13
    assert summary["covered_phase_count"] == 12


def test_automated_evidence_executes_unique_release_blocking_references(monkeypatch) -> None:
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, b"13 passed", b"")

    monkeypatch.setattr(
        "scripts.run_semantic_media_security_gate.subprocess.run",
        run,
    )
    execution = execute_automated_evidence(_matrix())
    assert execution["status"] == "passed"
    assert execution["test_file_count"] == 11
    assert len(calls) == 1
    assert calls[0][0][1:4] == ["-m", "pytest", "-q"]
    evidence, summary = evaluate_matrix(_matrix(), execution=execution)
    assert evidence.status == "passed"
    assert summary["executed_test_file_count"] == 11


def test_failed_or_missing_automated_evidence_blocks_gate(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path
    (root / "tests").mkdir()
    (root / "tests/test_security.py").write_text("def test_x(): pass\n", encoding="utf-8")
    matrix = {
        "cases": [
            {
                "test_reference": "tests/test_security.py",
                "evidence": "automated",
                "release_blocking": True,
            }
        ]
    }
    monkeypatch.setattr(
        "scripts.run_semantic_media_security_gate.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, b"", b"failed"),
    )
    execution = execute_automated_evidence(matrix, root=root)
    assert execution["status"] == "failed"

    evidence, _summary = evaluate_matrix(
        _matrix(),
        execution={
            "status": "failed",
            "reason_code": "security_matrix_automated_tests_failed",
            "test_file_count": 11,
            "exit_code": 1,
            "duration_ms": 1,
        },
    )
    assert evidence.status == "failed"
    assert "security_matrix_automated_tests_failed" in evidence.reason_codes
