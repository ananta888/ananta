from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from agent.services.semantic_media_program_evidence import (
    GateEvidence,
    ProgramEvidenceError,
    canonical_sha256,
    source_hash,
    write_report,
)
from agent.services.semantic_media_rollout_policy import (
    ROLLOUT_STAGES,
    SemanticMediaHealthSignals,
    evaluate_rollout_health,
)
from scripts.run_semantic_media_program_release_gate import (
    LOCAL_GATES,
    MILESTONE_GATES,
    QA_GATES,
    SCHEMA,
    TODO,
    _result,
    build_release_document,
    evaluate_optional_bound_gate,
    evaluate_sfu_artifacts,
    evaluate_visual_activation_artifacts,
    program_config_projection,
    program_source_projection,
    task_gate_requirements,
)

ROOT = Path(__file__).resolve().parents[1]


def test_release_gate_is_directly_executable_without_custom_pythonpath() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/run_semantic_media_program_release_gate.py", "--help"],
        cwd=ROOT,
        env={"PATH": __import__("os").environ["PATH"]},
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0
    assert "--execute-live-e2e" in completed.stdout


def _all_gate_ids() -> set[str]:
    identifiers = {gate.gate_id for gate in LOCAL_GATES}
    identifiers.update(value for values in MILESTONE_GATES.values() for value in values)
    identifiers.update(value for values in QA_GATES.values() for value in values)
    return identifiers


def test_qa_audit_gate_executes_every_transactional_authority_regression() -> None:
    qa_audit = next(gate for gate in LOCAL_GATES if gate.gate_id == "qa_audit")
    command = set(qa_audit.command)
    assert {
        "tests/architecture/test_semantic_media_atomic_audit_boundaries.py",
        "tests/test_semantic_media_audit_outbox.py",
        "tests/test_semantic_compute_atomic_audit.py",
        "tests/test_ml_intern_adapter_training_atomic_audit.py",
        "tests/test_speech_lifecycle_atomic_audit.py",
    } <= command


def test_release_command_runs_complete_python_and_angular_unit_suites() -> None:
    python_unit = next(gate for gate in LOCAL_GATES if gate.gate_id == "python_unit")
    angular_unit = next(gate for gate in LOCAL_GATES if gate.gate_id == "angular_unit")
    assert python_unit.command[-1] == "tests"
    assert angular_unit.command == ("npm", "run", "test:unit")
    assert {"python_unit", "angular_unit", "backend_worker_build"} <= set(QA_GATES["ASMP-QA-004"])


def test_every_local_gate_is_reachable_from_task_or_milestone_evidence() -> None:
    todo = json.loads(TODO.read_text(encoding="utf-8"))
    required = {
        identifier
        for values in (
            *MILESTONE_GATES.values(),
            *QA_GATES.values(),
            *(task_gate_requirements(task["id"]) for task in todo["tasks"]),
        )
        for identifier in values
    }
    assert {gate.gate_id for gate in LOCAL_GATES} <= required


def test_live_pair_and_sfu_evidence_are_required_for_runtime_and_group_acceptance() -> None:
    assert "qa_pair_e2e" in task_gate_requirements("ASMP-SEC-010")
    assert "qa_pair_e2e" in task_gate_requirements("ASMP-SPR-012")
    assert {"qa_group_e2e", "m3_sfu_live", "qa_chaos"} <= set(QA_GATES["ASMP-QA-006"])


def _gates(*, unavailable: str | None = None):
    rows = []
    for identifier in sorted(_all_gate_ids()):
        status = "unverified" if identifier == unavailable else "passed"
        reasons = ("external_evidence_unavailable",) if status == "unverified" else ()
        rows.append(_result(identifier, status, reasons, {"id": identifier, "status": status}))
    return rows


def _done_todo():
    document = json.loads(TODO.read_text(encoding="utf-8"))
    for task in document["tasks"]:
        task["status"] = "done"
        task["progress_percent"] = 100
    return document


def test_release_document_is_schema_valid_complete_content_free_and_go_only_when_every_gate_passes() -> None:
    document = build_release_document(
        gates=_gates(),
        stage="observe_only",
        todo_document=_done_todo(),
    )
    jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))).validate(document)
    assert document["decision"] == "go"
    assert document["ordinary_call_action"] == "preserve"
    assert len(document["milestones"]) == 12
    assert len(document["tasks"]) >= 120
    assert all(row["status"] == "passed" for row in document["tasks"])
    assert canonical_sha256(document) and "/home/" not in json.dumps(document)


def test_release_document_binds_complete_declared_source_and_configuration(tmp_path: Path) -> None:
    todo = _done_todo()
    for task in todo["tasks"]:
        task["affected_files"] = ["agent/program.py"]
    todo["tasks"][0]["affected_files"].append("config/program.json")
    core = (
        "agent/services/semantic_media_program_evidence.py",
        "agent/services/semantic_media_rollout_policy.py",
        "docs/operations/semantic-media-rollout.md",
        "schemas/release/semantic_media_program_evidence.v1.json",
        "scripts/run_semantic_media_program_release_gate.py",
        "todos/archiv/todo.ai-snake-semantic-media-speech-program.json",
    )
    for relative in (*core, "agent/program.py", "config/program.json"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n" if path.suffix == ".json" else "CURRENT = True\n", encoding="utf-8")

    projection = program_source_projection(todo, root=tmp_path)
    assert {"agent/program.py", "config/program.json"} <= set(projection)
    assert "config/program.json" in program_config_projection(projection)
    first = build_release_document(gates=_gates(), stage="observe_only", todo_document=todo, root=tmp_path)

    (tmp_path / "agent/program.py").write_text("CURRENT = False\n", encoding="utf-8")
    source_changed = build_release_document(gates=_gates(), stage="observe_only", todo_document=todo, root=tmp_path)
    assert source_changed["source_sha256"] != first["source_sha256"]
    assert source_changed["config_sha256"] == first["config_sha256"]

    (tmp_path / "config/program.json").write_text('{"enabled": false}\n', encoding="utf-8")
    config_changed = build_release_document(gates=_gates(), stage="observe_only", todo_document=todo, root=tmp_path)
    assert config_changed["config_sha256"] != source_changed["config_sha256"]


def test_program_source_projection_rejects_missing_and_empty_declared_globs(tmp_path: Path) -> None:
    todo = {"tasks": [{"id": "ASMP-X", "affected_files": ["agent/*.py"]}]}
    with pytest.raises(ProgramEvidenceError, match="program_source_glob_empty"):
        program_source_projection(todo, root=tmp_path)


def test_program_source_projection_covers_every_cross_cutting_runtime_file() -> None:
    projection = set(
        program_source_projection(
            json.loads(TODO.read_text(encoding="utf-8")),
            root=ROOT,
        )
    )
    required: set[str] = set()
    for directory in (
        "worker/semantic_media",
        "worker/speech_reconciliation",
        "worker/speech_training",
        "frontend-angular/src/app/features/ml-intern",
        "frontend-angular/src/app/features/pair-view",
        "frontend-angular/src/app/features/voice",
        "frontend-angular/src/app/services",
        "schemas/release",
        "schemas/voice",
        "schemas/webrtc",
    ):
        required.update(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / directory).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
    for pattern in (
        "agent/bootstrap/semantic_media_services.py",
        "agent/db_models/semantic_*.py",
        "agent/db_models/speech_*.py",
        "agent/repositories/ml_intern_speech_*.py",
        "agent/repositories/semantic_*.py",
        "agent/repositories/speech_*.py",
        "agent/repositories/webrtc_*.py",
        "agent/routes/semantic_*.py",
        "agent/routes/speech_*.py",
        "agent/services/semantic_*.py",
        "agent/services/speech_*.py",
        "ananta_contracts/semantic_*.py",
        "ananta_contracts/speech_*.py",
        "ananta_contracts/webrtc_*.py",
    ):
        required.update(path.relative_to(ROOT).as_posix() for path in ROOT.glob(pattern) if path.is_file())
    assert sorted(required - projection) == []


def test_unavailable_external_gate_forces_no_go_and_explicit_unverified_coverage() -> None:
    document = build_release_document(
        gates=_gates(unavailable="qa_pair_e2e"),
        stage="single_pair_opt_in",
        todo_document=_done_todo(),
    )
    assert document["decision"] == "no_go"
    pair = next(row for row in document["tasks"] if row["id"] == "ASMP-QA-005")
    release = next(row for row in document["tasks"] if row["id"] == "ASMP-QA-012")
    assert pair["status"] == "unverified" and release["status"] == "unverified"
    assert document["ordinary_call_action"] == "preserve"


def test_verified_negative_gate_keeps_release_task_verified_but_forces_no_go() -> None:
    gates = _gates()
    visual = next(row for row in gates if row["id"] == "m5_visual_activation")
    visual.update(
        {
            "status": "failed",
            "reason_codes": ["spike_no_go"],
            "evidence_sha256": "a" * 64,
        }
    )
    document = build_release_document(
        gates=gates,
        stage="observe_only",
        todo_document=_done_todo(),
    )
    release = next(row for row in document["tasks"] if row["id"] == "ASMP-QA-012")
    visual_task = next(row for row in document["tasks"] if row["id"] == "ASMP-VIS-012")
    assert release["status"] == "passed"
    assert visual_task["status"] == "failed"
    assert document["decision"] == "no_go"


def test_unmapped_raw_gate_failure_can_never_produce_go() -> None:
    gates = _gates()
    backend_build = next(row for row in gates if row["id"] == "backend_worker_build")
    backend_build.update(
        {
            "status": "failed",
            "reason_codes": ["gate_command_failed"],
            "evidence_sha256": "b" * 64,
        }
    )
    document = build_release_document(
        gates=gates,
        stage="observe_only",
        todo_document=_done_todo(),
    )
    task = next(row for row in document["tasks"] if row["id"] == "ASMP-QA-004")
    assert task["status"] == "failed"
    assert document["decision"] == "no_go"
    assert "gate_command_failed" in document["reason_codes"]


def test_task_evidence_is_specific_and_never_overrides_incomplete_acceptance() -> None:
    todo = _done_todo()
    partial = next(row for row in todo["tasks"] if row["id"] == "ASMP-VIS-001")
    partial["status"] = "partial"
    partial["progress_percent"] = 90
    document = build_release_document(
        gates=_gates(unavailable="m5_visual_activation"),
        stage="observe_only",
        todo_document=todo,
    )
    visual_safe = next(row for row in document["tasks"] if row["id"] == "ASMP-VIS-001")
    visual_activation = next(row for row in document["tasks"] if row["id"] == "ASMP-VIS-012")
    contract = next(row for row in document["tasks"] if row["id"] == "ASMP-CTL-003")
    assert visual_safe["status"] == "unverified"
    assert "task_acceptance_not_complete" in visual_safe["reason_codes"]
    assert visual_activation["status"] == "unverified"
    assert contract["status"] == "passed"


def test_every_program_task_has_an_explicit_task_gate_projection() -> None:
    todo = json.loads(TODO.read_text(encoding="utf-8"))
    missing = [
        task["id"] for task in todo["tasks"] if task["id"] != "ASMP-QA-012" and not task_gate_requirements(task["id"])
    ]
    assert missing == []


def test_m9_and_qa_performance_require_current_peer_sync_benchmark_evidence() -> None:
    assert "m9_peer_sync_performance" in MILESTONE_GATES["ASMP-M9"]
    assert "m9_peer_sync_performance" in QA_GATES["ASMP-QA-009"]
    assert "m9_peer_sync_performance" in task_gate_requirements("ASMP-SYN-012")
    document = build_release_document(
        gates=_gates(unavailable="m9_peer_sync_performance"),
        stage="single_pair_opt_in",
        todo_document=_done_todo(),
    )
    task = next(row for row in document["tasks"] if row["id"] == "ASMP-SYN-012")
    assert document["decision"] == "no_go"
    assert task["status"] == "unverified"


def test_automatic_stop_covers_every_release_signal_without_ending_ordinary_call() -> None:
    scenarios = (
        SemanticMediaHealthSignals(security_findings=1),
        SemanticMediaHealthSignals(privacy_findings=1),
        SemanticMediaHealthSignals(e2ee_downgrades=1),
        SemanticMediaHealthSignals(live_p95_ratio_micros=1_050_001),
        SemanticMediaHealthSignals(live_p99_ratio_micros=1_050_001),
        SemanticMediaHealthSignals(quality_gate_passed=False),
        SemanticMediaHealthSignals(budget_ratio_micros=1_000_001),
        SemanticMediaHealthSignals(resource_ratio_micros=1_000_001),
    )
    for stage in ROLLOUT_STAGES:
        for signals in scenarios:
            decision = evaluate_rollout_health(stage, signals)
            assert decision.semantic_action == "stop_and_rollback"
            assert decision.ordinary_call_action == "preserve"
            assert decision.target_stage == "observe_only"


def test_m3_release_seam_revalidates_source_bound_real_sfu_artifacts(tmp_path: Path) -> None:
    report = json.loads((ROOT / "artifacts/test-gates/semantic-sfu.json").read_text(encoding="utf-8"))
    copied_report = tmp_path / "semantic-sfu.json"
    copied_report.write_text(json.dumps(report), encoding="utf-8")
    evidence = evaluate_sfu_artifacts(
        report_path=copied_report,
        spike_path=ROOT / "artifacts/domain/semantic-sfu-three-peer.json",
        load_path=ROOT / "artifacts/domain/semantic-sfu-load.json",
        failover_path=ROOT / "artifacts/domain/semantic-sfu-live-failover.json",
        group_path=ROOT / "artifacts/e2e/semantic-media-group-report.json",
    )
    expected_status = "passed" if report["verdict"] == "pass" else "failed"
    assert evidence.status == expected_status
    assert set(report["reasons"]) <= set(evidence.reason_codes)
    assert "sfu_gate_report_decision_stale" not in evidence.reason_codes
    assert "sfu_gate_report_source_stale" not in evidence.reason_codes
    assert evidence.measurements["external_live_failover_verified"] is True
    report["source_sha256"] = "0" * 64
    copied_report.write_text(json.dumps(report), encoding="utf-8")
    stale = evaluate_sfu_artifacts(
        report_path=copied_report,
        spike_path=ROOT / "artifacts/domain/semantic-sfu-three-peer.json",
        load_path=ROOT / "artifacts/domain/semantic-sfu-load.json",
        failover_path=ROOT / "artifacts/domain/semantic-sfu-live-failover.json",
        group_path=ROOT / "artifacts/e2e/semantic-media-group-report.json",
    )
    assert stale.status == "failed"
    assert "sfu_gate_report_source_stale" in stale.reason_codes


def test_visual_activation_seam_reports_current_no_go_as_verified_failure(tmp_path: Path) -> None:
    report_path = ROOT / "artifacts/test-gates/semantic-visual.json"
    result = evaluate_visual_activation_artifacts(
        report_path=report_path,
        spike_path=ROOT / "artifacts/domain/semantic-visual-feasibility.json",
        benchmark_path=ROOT / "artifacts/domain/semantic-visual-benchmark.json",
        lifecycle_path=ROOT / "artifacts/e2e/semantic-visual-lifecycle-report.json",
    )
    assert result["status"] == "failed"
    assert "spike_no_go" in result["reason_codes"]
    assert "semantic_visual_evidence_unavailable" not in result["reason_codes"]

    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["semantic_visual_activation"] = True
    tampered = tmp_path / "visual.json"
    tampered.write_text(json.dumps(report), encoding="utf-8")
    stale = evaluate_visual_activation_artifacts(
        report_path=tampered,
        spike_path=ROOT / "artifacts/domain/semantic-visual-feasibility.json",
        benchmark_path=ROOT / "artifacts/domain/semantic-visual-benchmark.json",
        lifecycle_path=ROOT / "artifacts/e2e/semantic-visual-lifecycle-report.json",
    )
    assert "semantic_visual_gate_report_stale" in stale["reason_codes"]


def test_optional_m10_report_requires_current_explicit_source_and_config_projection(tmp_path: Path) -> None:
    (tmp_path / "source.py").write_text("CURRENT = True\n", encoding="utf-8")
    (tmp_path / "policy.json").write_text('{"enabled": false}\n', encoding="utf-8")
    report_path = tmp_path / "offline.json"
    write_report(
        report_path,
        GateEvidence(
            gate_id="m10_offline",
            status="passed",
            reason_codes=(),
            source_sha256=source_hash(tmp_path, ("source.py",)),
            config_sha256=source_hash(tmp_path, ("policy.json",)),
            measurements={"verified_runs": 1},
        ),
    )
    evidence = evaluate_optional_bound_gate(
        gate_id="m10_offline",
        report_path=report_path,
        source_paths=(Path("source.py"),),
        config_paths=(Path("policy.json"),),
        unavailable_reason="offline_reconciliation_evidence_unavailable",
        root=tmp_path,
    )
    assert evidence.status == "passed"

    (tmp_path / "source.py").write_text("CURRENT = False\n", encoding="utf-8")
    with pytest.raises(ProgramEvidenceError, match="gate_report_source_stale"):
        evaluate_optional_bound_gate(
            gate_id="m10_offline",
            report_path=report_path,
            source_paths=(Path("source.py"),),
            config_paths=(Path("policy.json"),),
            unavailable_reason="offline_reconciliation_evidence_unavailable",
            root=tmp_path,
        )
