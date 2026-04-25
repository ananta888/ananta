from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
REQUEST_SCHEMA = ROOT / "schemas" / "worker" / "worker_execution_request.v1.json"
RESULT_SCHEMA = ROOT / "schemas" / "worker" / "worker_execution_result.v1.json"
PATCH_SCHEMA = ROOT / "schemas" / "worker" / "patch_artifact.v1.json"
COMMAND_SCHEMA = ROOT / "schemas" / "worker" / "command_plan_artifact.v1.json"
TEST_SCHEMA = ROOT / "schemas" / "worker" / "test_result_artifact.v1.json"
VERIFY_SCHEMA = ROOT / "schemas" / "worker" / "verification_artifact.v1.json"
WORKSPACE_SCHEMA = ROOT / "schemas" / "worker" / "workspace_constraints.v1.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_worker_request_schema_accepts_bounded_payload() -> None:
    schema = _load(REQUEST_SCHEMA)
    payload = {
        "schema": "worker_execution_request.v1",
        "task_id": "AW-T02",
        "goal_id": "G1",
        "trace_id": "tr-1",
        "capability_id": "worker.patch.propose",
        "mode": "patch_propose",
        "context_envelope_ref": {
            "context_bundle_id": "ctx-1",
            "context_hash": "h-1",
            "retrieval_refs": [{"source_id": "docs", "path": "docs/architecture/ananta_native_worker.md", "reason": "scope"}],
            "context_chunk_limit": 8,
            "context_byte_limit": 50000
        },
        "policy_decision_ref": {"decision_id": "p1", "decision": "allow", "policy_version": "v1"},
        "workspace_constraints_ref": {"constraint_id": "wc-1"},
        "requested_outputs": ["patch_artifact", "trace_metadata"]
    }
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    assert errors == []


def test_worker_artifact_schemas_validate_examples() -> None:
    examples = [
        (PATCH_SCHEMA, {"schema": "patch_artifact.v1", "task_id": "t", "capability_id": "worker.patch.propose", "base_ref": "HEAD", "patch": "diff --git a/a b/a\n", "patch_hash": "x", "changed_files": ["a"], "risk_classification": "high"}),
        (COMMAND_SCHEMA, {"schema": "command_plan_artifact.v1", "task_id": "t", "capability_id": "worker.command.plan", "command": "pytest -q", "explanation": "run tests", "risk_classification": "medium", "required_approval": False, "working_directory": ".", "expected_effects": ["test results"]}),
        (TEST_SCHEMA, {"schema": "test_result_artifact.v1", "task_id": "t", "command": "pytest -q", "exit_code": 0, "status": "passed", "stdout_ref": "out.log", "stderr_ref": "err.log"}),
        (VERIFY_SCHEMA, {"schema": "verification_artifact.v1", "task_id": "t", "status": "passed", "checks": [{"check_id": "smoke", "status": "passed"}], "evidence_refs": ["out.log"]}),
        (RESULT_SCHEMA, {"schema": "worker_execution_result.v1", "task_id": "t", "trace_id": "tr", "status": "completed", "artifacts": [{"artifact_type": "patch_artifact", "artifact_ref": "patch:1"}]}),
        (WORKSPACE_SCHEMA, {"schema": "workspace_constraints.v1", "constraint_id": "wc-1", "allowed_roots": ["."], "writable_output_paths": ["ci-artifacts"], "max_files": 100, "max_bytes": 100000, "allowed_commands": ["pytest -q"], "allow_main_tree_apply": False}),
    ]
    for schema_path, payload in examples:
        schema = _load(schema_path)
        errors = list(Draft202012Validator(schema).iter_errors(payload))
        assert errors == []
