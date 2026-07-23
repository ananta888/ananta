from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.run_kanban_model_dashboard_release_gate import (
    EVIDENCE_SCHEMA,
    OUTPUT_SCHEMA,
    ReleaseGateError,
    run_gate,
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
    value = {
        "schema": EVIDENCE_SCHEMA,
        "suite": suite,
        "status": "passed",
        "commit_sha": COMMIT,
        "produced_at": "2026-07-23T11:00:00Z",
        "input_hashes": {"source": hashlib.sha256(suite.encode()).hexdigest()},
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
    _evidence(tmp_path, "external")
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
