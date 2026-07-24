from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.run_kanban_model_dashboard_release_gate import (
    EVIDENCE_SCHEMA,
    OUTPUT_SCHEMA,
    ReleaseGateError,
    run_gate,
)
from scripts.run_kanban_model_dashboard_evidence import (
    ARTIFACT_BOUNDARY,
    PRODUCER_NAME,
    SUITE_SPECS,
    suite_allowlist_sha256,
)


COMMIT = "a" * 40
AS_OF = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
SUITES = (
    "contract",
    "backend",
    "angular",
    "tui",
    "security",
    "accessibility",
    "performance",
)


def _profile(path: Path) -> Path:
    value = {
        "schema": "ananta.kanban-model-dashboard.release-profile.v1",
        "profile_id": "test",
        "max_age_seconds": 86400,
        "required_suites": list(SUITES),
        "evidence_file_pattern": "{suite}.json",
        "rollout_stages": [
            {"id": "read_only_models", "required_suites": list(SUITES)},
            {"id": "read_only_board", "required_suites": list(SUITES)},
            {"id": "board_writes", "required_suites": list(SUITES)},
            {
                "id": "allowlisted_default_selection",
                "required_suites": list(SUITES),
            },
        ],
        "excluded_actions": [
            "worker_start",
            "worker_orchestration",
            "model_load",
            "model_unload",
            "direct_provider_url",
            "shell_command",
        ],
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _evidence(directory: Path, suite: str, **updates: object) -> None:
    digest = hashlib.sha256(suite.encode()).hexdigest()
    runtime = {
        "python_version": "3.12.0",
        "python_executable": sys.executable,
        "npx_executable": "/usr/bin/npx",
    }
    commands = []
    for index, spec in enumerate(SUITE_SPECS[suite].commands):
        argv = [
            (
                sys.executable
                if token == "{python}"
                else "/usr/bin/npx"
                if token == "{npx}"
                else token
            )
            for token in spec.argv
        ]
        commands.append(
            {
                "index": index,
                "allowlist_argv": list(spec.argv),
                "argv": argv,
                "cwd": spec.cwd,
                "env_overrides": dict(spec.env),
                "timeout_seconds": spec.timeout_seconds,
                "started_at": "2026-07-23T10:59:00Z",
                "finished_at": "2026-07-23T11:00:00Z",
                "duration_ms": 60_000,
                "exit_code": 0,
                "status": "passed",
                "stdout_bytes": 0,
                "stdout_sha256": hashlib.sha256(b"").hexdigest(),
                "stderr_bytes": 0,
                "stderr_sha256": hashlib.sha256(b"").hexdigest(),
                "result_validation": {
                    "kind": spec.validator,
                    "status": "passed",
                    "observed": {},
                },
            }
        )
    value = {
        "schema": EVIDENCE_SCHEMA,
        "suite": suite,
        "status": "passed",
        "commit_sha": COMMIT,
        "started_at": "2026-07-23T10:59:00Z",
        "produced_at": "2026-07-23T11:00:00Z",
        "producer": {
            "name": PRODUCER_NAME,
            "version": "1",
            "artifact_boundary": ARTIFACT_BOUNDARY,
            "allowlist_sha256": suite_allowlist_sha256(suite),
        },
        "candidate": {
            "commit_sha": COMMIT,
            "checkout_commit_sha": COMMIT,
            "verified": True,
            "input_blobs_verified": True,
            "evidence_path_not_in_candidate": True,
        },
        "runtime": runtime,
        "input_hashes": {"source": digest},
        "inputs": [
            {
                "path": "source",
                "size_bytes": len(suite),
                "sha256": digest,
                "candidate_sha256": digest,
            }
        ],
        "commands": commands,
        "result_hashes": {},
        "reason_codes": [],
    }
    value.update(updates)
    (directory / f"{suite}.json").write_text(json.dumps(value), encoding="utf-8")


def test_release_gate_passes_only_complete_fresh_commit_bound_evidence(
    tmp_path: Path,
) -> None:
    profile = _profile(tmp_path / "profile.json")
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    for suite in SUITES:
        _evidence(evidence_dir, suite)

    result = run_gate(
        profile_path=profile,
        evidence_dir=evidence_dir,
        commit_sha=COMMIT,
        as_of=AS_OF,
    )

    assert result["schema"] == OUTPUT_SCHEMA
    assert result["status"] == "passed"
    assert result["reason_codes"] == []
    assert all(stage["status"] == "passed" for stage in result["rollout_stages"])


@pytest.mark.parametrize(
    ("suite", "updates", "reason"),
    [
        ("contract", {"status": "failed"}, "contract:evidence_status_not_passed"),
        ("backend", {"commit_sha": "b" * 40}, "backend:evidence_commit_mismatch"),
        (
            "security",
            {"produced_at": "2026-07-20T11:00:00Z"},
            "security:evidence_stale",
        ),
        (
            "angular",
            {"input_hashes": {}},
            "angular:evidence_input_hashes_missing",
        ),
        (
            "tui",
            {"schema": "unknown"},
            "tui:evidence_schema_invalid",
        ),
    ],
)
def test_release_gate_fails_closed_for_invalid_evidence(
    tmp_path: Path,
    suite: str,
    updates: dict[str, object],
    reason: str,
) -> None:
    profile = _profile(tmp_path / "profile.json")
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    for current in SUITES:
        _evidence(evidence_dir, current, **(updates if current == suite else {}))

    result = run_gate(
        profile_path=profile,
        evidence_dir=evidence_dir,
        commit_sha=COMMIT,
        as_of=AS_OF,
    )

    assert result["status"] == "failed"
    assert reason in result["reason_codes"]
    assert result["fail_closed"] is True


def test_release_gate_fails_closed_for_missing_and_symlinked_evidence(
    tmp_path: Path,
) -> None:
    profile = _profile(tmp_path / "profile.json")
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    for suite in SUITES:
        if suite != "performance":
            _evidence(evidence_dir, suite)
    target = tmp_path / "external.json"
    target.write_text("{}", encoding="utf-8")
    (evidence_dir / "performance.json").symlink_to(target)

    result = run_gate(
        profile_path=profile,
        evidence_dir=evidence_dir,
        commit_sha=COMMIT,
        as_of=AS_OF,
    )

    assert result["status"] == "failed"
    assert (
        "performance:evidence_missing_or_unsafe" in result["reason_codes"]
    )


def test_release_gate_rejects_unbound_commit() -> None:
    with pytest.raises(ReleaseGateError, match="commit_sha_invalid"):
        run_gate(
            profile_path=Path("unused"),
            evidence_dir=Path("unused"),
            commit_sha="HEAD",
            as_of=AS_OF,
        )


def test_release_gate_rejects_failed_or_non_allowlisted_commands(
    tmp_path: Path,
) -> None:
    profile = _profile(tmp_path / "profile.json")
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    for suite in SUITES:
        _evidence(evidence_dir, suite)
    backend_path = evidence_dir / "backend.json"
    backend = json.loads(backend_path.read_text())
    backend["commands"][0]["exit_code"] = 1
    backend["commands"][0]["status"] = "failed"
    backend_path.write_text(json.dumps(backend))

    result = run_gate(
        profile_path=profile,
        evidence_dir=evidence_dir,
        commit_sha=COMMIT,
        as_of=AS_OF,
    )

    assert result["status"] == "failed"
    assert "backend:evidence_commands_not_passed" in result["reason_codes"]


def test_release_gate_rejects_circular_input_binding(tmp_path: Path) -> None:
    profile = _profile(tmp_path / "profile.json")
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    for suite in SUITES:
        _evidence(evidence_dir, suite)
    contract_path = evidence_dir / "contract.json"
    contract = json.loads(contract_path.read_text())
    circular = "artifacts/e2e/kanban-model-dashboard/contract.json"
    contract["input_hashes"] = {
        circular: contract["inputs"][0]["sha256"]
    }
    contract["inputs"][0]["path"] = circular
    contract_path.write_text(json.dumps(contract))

    result = run_gate(
        profile_path=profile,
        evidence_dir=evidence_dir,
        commit_sha=COMMIT,
        as_of=AS_OF,
    )

    assert result["status"] == "failed"
    assert "contract:evidence_inputs_invalid" in result["reason_codes"]
