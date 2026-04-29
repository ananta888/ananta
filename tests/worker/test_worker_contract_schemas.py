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
LOOP_STATE_SCHEMA = ROOT / "schemas" / "worker" / "worker_loop_state.v1.json"
PROGRESS_EVENT_SCHEMA = ROOT / "schemas" / "worker" / "worker_progress_event.v1.json"
PROFILE_SCHEMA = ROOT / "schemas" / "worker" / "worker_execution_profile.v1.json"
RETRIEVAL_INDEX_SCHEMA = ROOT / "schemas" / "worker" / "retrieval_index_contract.v1.json"
RETRIEVAL_PIPELINE_SCHEMA = ROOT / "schemas" / "worker" / "retrieval_pipeline_contract.v1.json"
WORKER_CONTEXT_BUNDLE_SCHEMA = ROOT / "schemas" / "worker" / "worker_context_bundle.v1.json"
STANDALONE_CONTRACT_SCHEMA = ROOT / "schemas" / "worker" / "standalone_task_contract.v1.json"
PLANNER_STATE_CONTRACT_SCHEMA = ROOT / "schemas" / "worker" / "planner_state_contract.v1.json"


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
        (COMMAND_SCHEMA, {"schema": "command_plan_artifact.v1", "task_id": "t", "capability_id": "worker.command.plan", "command": "pytest -q", "command_hash": "abc", "explanation": "run tests", "risk_classification": "medium", "required_approval": False, "working_directory": ".", "expected_effects": ["test results"]}),
        (TEST_SCHEMA, {"schema": "test_result_artifact.v1", "task_id": "t", "command": "pytest -q", "exit_code": 0, "status": "passed", "stdout_ref": "out.log", "stderr_ref": "err.log"}),
        (VERIFY_SCHEMA, {"schema": "verification_artifact.v1", "task_id": "t", "status": "passed", "checks": [{"check_id": "smoke", "status": "passed"}], "evidence_refs": ["out.log"]}),
        (RESULT_SCHEMA, {"schema": "worker_execution_result.v1", "task_id": "t", "trace_id": "tr", "status": "completed", "artifacts": [{"artifact_type": "patch_artifact", "artifact_ref": "patch:1"}]}),
        (WORKSPACE_SCHEMA, {"schema": "workspace_constraints.v1", "constraint_id": "wc-1", "allowed_roots": ["."], "writable_output_paths": ["ci-artifacts"], "max_files": 100, "max_bytes": 100000, "allowed_commands": ["pytest -q"], "allow_main_tree_apply": False}),
        (PROFILE_SCHEMA, {
            "schema": "worker_execution_profile.v1",
            "profile": "balanced",
            "profile_source": "agent_default",
            "policy": {"auto_allow_readonly_diagnostics": True, "allowlist_mode": "balanced"},
            "budgets": {"max_iterations": 4, "max_patch_attempts": 4, "max_runtime_seconds": 420},
            "context_limits": {"max_files": 12, "max_bytes": 120000, "max_prompt_context_chars": 8000},
            "approval_behavior": {"enforce_hub_tokens": True, "guarded_root_requires_token": True}
        }),
        (RETRIEVAL_INDEX_SCHEMA, {
            "schema": "retrieval_index_entry.v1",
            "chunk_id": "src/a.py:0:10:h",
            "path": "src/a.py",
            "text": "def a(): pass",
            "language": "python",
            "symbol_name": "a",
            "start_byte": 0,
            "end_byte": 12,
            "source_hash": "abc123",
            "embedding_version": "hash-v1",
            "embedding": [0.1, 0.2]
        }),
        (RETRIEVAL_PIPELINE_SCHEMA, {
            "schema": "retrieval_pipeline_contract.v1",
            "channels": ["dense", "lexical", "symbol"],
            "fallback_order": ["dense", "lexical", "symbol"],
            "weights": {"dense": 0.5, "lexical": 0.3, "symbol": 0.2}
        }),
        (WORKER_CONTEXT_BUNDLE_SCHEMA, {
            "schema": "worker_context_bundle.v1",
            "bundle_type": "worker_execution_context",
            "query": "retry timeout payment",
            "context_text": "selected context",
            "chunk_count": 1,
            "token_estimate": 12,
            "chunks": [
                {
                    "engine": "codecompass_fts",
                    "source": "src/PaymentService.java",
                    "content": "retry timeout logic",
                    "score": 1.2,
                    "metadata": {
                        "record_id": "method:PaymentService.retryTimeout",
                        "record_kind": "java_method",
                        "file": "src/PaymentService.java",
                        "source_manifest_hash": "mh-1"
                    }
                }
            ],
            "context_policy": {"mode": "balanced"},
            "selection_trace": {"fusion": {"mode": "deterministic_v2"}}
        }),
        (STANDALONE_CONTRACT_SCHEMA, {
            "schema": "standalone_task_contract.v1",
            "task_id": "t",
            "goal": "run tests",
            "command": "pytest -q",
            "worker_profile": "balanced",
            "files": ["src/a.py"],
            "diffs": [],
            "control_manifest": {"trace_id": "tr", "capability_id": "worker.command.execute", "context_hash": "ctx"}
        }),
        (PLANNER_STATE_CONTRACT_SCHEMA, {
            "schema": "planner_state_contract.v1",
            "task_id": "t",
            "state": "ready",
            "trace_ref": "trace:t",
            "updated_at": "2026-01-01T00:00:00+00:00"
        }),
        (PROGRESS_EVENT_SCHEMA, {"schema": "worker_progress_event.v1", "task_id": "t", "trace_id": "tr", "phase": "plan", "iteration": 1, "artifact_refs": ["patch:1"], "detail": "planned", "emitted_at": "2026-01-01T00:00:00+00:00"}),
        (LOOP_STATE_SCHEMA, {"schema": "worker_loop_state.v1", "task_id": "t", "trace_id": "tr", "context_hash": "ctx", "execution_profile": "balanced", "policy_state": "allow", "phase": "summarize", "iteration": 1, "patch_attempts": 1, "status": "completed", "stop_reason": "goal_reached", "artifacts": ["patch:1"], "events": [{"schema": "worker_progress_event.v1", "task_id": "t", "trace_id": "tr", "phase": "summarize", "iteration": 1, "artifact_refs": ["patch:1"], "detail": "done", "emitted_at": "2026-01-01T00:00:00+00:00"}]}),
    ]
    for schema_path, payload in examples:
        schema = _load(schema_path)
        errors = list(Draft202012Validator(schema).iter_errors(payload))
        assert errors == []
