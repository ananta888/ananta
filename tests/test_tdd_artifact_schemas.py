from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
TEST_PLAN_SCHEMA = ROOT / "schemas" / "worker" / "tdd_test_plan_artifact.v1.json"
TDD_CYCLE_SCHEMA = ROOT / "schemas" / "worker" / "tdd_cycle_artifact.v1.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_tdd_artifact_schemas_validate_minimal_and_full_examples() -> None:
    test_plan_schema = _load(TEST_PLAN_SCHEMA)
    cycle_schema = _load(TDD_CYCLE_SCHEMA)

    minimal_plan = {
        "schema": "tdd_test_plan_artifact.v1",
        "task_id": "TDD-T04",
        "behavior_statement": "Password must be at least 12 characters.",
        "test_files": ["tests/test_password_policy.py"],
        "planned_tests": [
            {
                "test_id": "password-min-length",
                "file_path": "tests/test_password_policy.py",
                "expected_behavior": "Reject values shorter than 12 characters.",
                "phase": "red",
            }
        ],
        "status": "planned",
    }
    full_cycle = {
        "schema": "tdd_cycle_artifact.v1",
        "task_id": "TDD-T05",
        "behavior_statement": "Reject weak passwords.",
        "test_files": ["tests/test_password_policy.py"],
        "cycle_status": "green_passed",
        "red_result": {
            "status": "red_expected",
            "test_result_artifact_ref": "test_result_artifact:TDD-T05:red",
            "notes": "Test failed before implementation as expected.",
        },
        "patch_refs": ["patch_artifact:TDD-T05:001"],
        "green_result": {
            "status": "green_passed",
            "test_result_artifact_ref": "test_result_artifact:TDD-T05:green",
        },
        "refactor_notes": {"status": "refactor_skipped", "notes": "No structural cleanup needed."},
        "verification_refs": ["verification_artifact:TDD-T05:final"],
    }

    assert list(Draft202012Validator(test_plan_schema).iter_errors(minimal_plan)) == []
    assert list(Draft202012Validator(cycle_schema).iter_errors(full_cycle)) == []


def test_tdd_cycle_schema_rejects_invalid_cycle_status() -> None:
    cycle_schema = _load(TDD_CYCLE_SCHEMA)
    invalid = {
        "schema": "tdd_cycle_artifact.v1",
        "task_id": "TDD-T05",
        "behavior_statement": "Reject weak passwords.",
        "test_files": ["tests/test_password_policy.py"],
        "cycle_status": "unknown_status",
        "red_result": {"status": "red_expected", "test_result_artifact_ref": "test_result_artifact:TDD-T05:red"},
        "patch_refs": ["patch_artifact:TDD-T05:001"],
        "green_result": {"status": "green_passed", "test_result_artifact_ref": "test_result_artifact:TDD-T05:green"},
        "refactor_notes": {"status": "refactor_skipped"},
        "verification_refs": ["verification_artifact:TDD-T05:final"],
    }

    errors = list(Draft202012Validator(cycle_schema).iter_errors(invalid))
    assert errors
