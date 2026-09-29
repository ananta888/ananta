from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from agent.services._task_scoped_citation import (
    build_source_catalog_from_execution_context,
)
from agent.services.recovery_grounding_verification_service import (
    RecoveryGroundingVerificationService,
)
from agent.services.recovery_plan_contract import (
    calculate_recovery_task_payload_digest,
)
from agent.services.recovery_result_verification_service import (
    RecoveryResultVerificationService,
)
from agent.services.recovery_worker_result_service import (
    RecoveryWorkerResultService,
)
from agent.services.verification_service import VerificationService


class MemoryRepository:
    def __init__(self, rows: list[SimpleNamespace] | None = None) -> None:
        self.rows = {
            str(row.id): row for row in list(rows or [])
        }
        self.save_calls: list[SimpleNamespace] = []

    def get_by_id(self, row_id: str) -> SimpleNamespace | None:
        return self.rows.get(str(row_id))

    def save(self, row: SimpleNamespace) -> SimpleNamespace:
        self.rows[str(row.id)] = row
        self.save_calls.append(row)
        return row


class GateDerivedVerification:
    def __init__(self) -> None:
        self.record_calls: list[dict[str, Any]] = []

    def verify_from_artifacts(self, **values: Any) -> dict[str, Any]:
        return VerificationService().verify_from_artifacts(**values)

    def create_or_update_record(
        self,
        task_id: str,
        **values: Any,
    ) -> SimpleNamespace:
        self.record_calls.append(
            {"task_id": task_id, **values}
        )
        status = (
            "passed"
            if values["gate_results"]["passed"] is True
            else "failed"
        )
        return SimpleNamespace(
            id=f"record-{task_id}",
            status=status,
        )


def _worker_execution_context() -> dict[str, Any]:
    return {
        "kind": "worker_execution_context",
        "version": "v1",
        "context": {
            "chunks": [
                {
                    "source": "src/recovery.py",
                    "record_id": "record-1",
                    "metadata": {
                        "source_id": "SRC_0001",
                        "source_id_verified": True,
                        "source_id_verification": {
                            "status": "verified",
                            "reason_code": "source_id_verified",
                            "verified": True,
                        },
                        "source_version": "rev-1",
                        "tenant_id": "tenant-1",
                        "source_scope": "recovery",
                        "provenance_digest": "a" * 64,
                        "source_manifest_hash": "c" * 64,
                        "content_hash": "d" * 64,
                        "record_id": "record-1",
                        "file": "src/recovery.py",
                        "record_kind": "repo_file",
                        "sensitivity": "internal",
                        "line_start": 1,
                        "line_end": 4,
                    },
                }
            ],
            "bundle_metadata": {
                "retrieval_trace": {
                    "trace_id": "trace-1",
                    "context_hash": "b" * 64,
                    "manifest_hash": "c" * 64,
                    "tenant_id": "tenant-1",
                    "scope": "recovery",
                }
            },
        },
    }


def _task_source_catalog(
    task: SimpleNamespace,
) -> dict[str, Any]:
    catalog = build_source_catalog_from_execution_context(
        tid=task.id,
        task=dict(vars(task)),
        llm_scope="local_only",
    )
    assert isinstance(catalog, dict)
    sources = list(catalog.get("sources") or [])
    rejected = list(catalog.get("rejected_candidates") or [])
    assert catalog.get("catalog_state") == "current"
    assert rejected == []
    assert sources[0]["content_hash"] == "d" * 64
    return {
        "schema": catalog.get("schema"),
        "source_catalog_id": catalog.get("catalog_id"),
        "source_catalog_hash": catalog.get("catalog_hash"),
        "catalog_state": catalog.get("catalog_state"),
        "source_count": len(sources),
        "rejected_count": len(rejected),
        "retrieval_trace_id": catalog.get("retrieval_trace_id"),
        "retrieval_context_hash": catalog.get(
            "retrieval_context_hash"
        ),
        "retrieval_manifest_hash": catalog.get(
            "retrieval_manifest_hash"
        ),
        "sources": sources,
    }


def test_catalog_restores_retrieval_trace_from_persisted_selection_trace() -> None:
    context = _worker_execution_context()
    bundle_metadata = context["context"]["bundle_metadata"]
    trace = bundle_metadata.pop("retrieval_trace")
    bundle_metadata["selection_trace"] = {
        "retrieval_trace_id": trace["trace_id"],
        "context_hash": trace["context_hash"],
        "manifest_hash": trace["manifest_hash"],
    }
    task = SimpleNamespace(
        id="recovery-selection-trace",
        worker_execution_context=context,
    )

    catalog = build_source_catalog_from_execution_context(
        tid=task.id,
        task=dict(vars(task)),
    )

    assert catalog is not None
    assert catalog["catalog_state"] == "current"
    assert catalog["retrieval_trace_id"] == "trace-1"
    assert catalog["rejected_candidates"] == []


def _run(task_id: str) -> dict[str, Any]:
    return {
        "source_id": "RUN_0001",
        "source_type": "tool_run",
        "task_id": task_id,
        "run_id": "run-authoritative",
        "tool_name": "shell",
        "command": "pytest",
        "exit_code": 0,
        "stdout_hash": "e" * 32,
        "stderr_hash": "f" * 32,
        "artifact_paths": [],
        "started_at": 1.0,
        "ended_at": 2.0,
        "allowed_for_llm_scope": True,
    }


def _answer(
    *,
    claim_type: str,
    citation_ref: str,
) -> str:
    return json.dumps(
        {
            "schema": "grounded_answer.v1",
            "answer": "Authoritatively grounded result.",
            "claims": [
                {
                    "claim_id": "CLM_0001",
                    "text": "The recovery step passed.",
                    "claim_type": claim_type,
                    "citation_refs": [citation_ref],
                    "confidence": "verified",
                }
            ],
            "unsupported_notes": [],
        }
    )


def _fixture(
    *,
    verification_status: dict[str, Any] | None = None,
    expected_artifacts: list[dict[str, Any]] | None = None,
    artifact: SimpleNamespace | None = None,
    version: SimpleNamespace | None = None,
    worker_execution_context: dict[str, Any] | None = None,
    hub_run_evidence_provider: (
        Callable[[str], list[dict[str, Any]]] | None
    ) = None,
) -> tuple[
    SimpleNamespace,
    SimpleNamespace,
    GateDerivedVerification,
    RecoveryResultVerificationService,
]:
    child = SimpleNamespace(
        id="recovery-grounding-child",
        goal_id="goal-1",
        plan_id="plan-1",
        source_task_id="source-1",
        team_id="team-1",
        derivation_reason="goal_task_recovery",
        assigned_agent_url="http://worker:5000",
        verification_spec={
            "expected_artifacts": list(
                expected_artifacts or []
            )
        },
        expected_artifacts=[],
        verification_status=dict(verification_status or {}),
        worker_execution_context=(
            dict(worker_execution_context)
            if worker_execution_context is not None
            else _worker_execution_context()
        ),
        status_reason_details={},
    )
    release = {
        "schema": "ananta.recovery_release_gate.v1",
        "release_epoch": "epoch-1",
        "plan_id": child.plan_id,
        "source_task_id": child.source_task_id,
        "goal_id": child.goal_id,
        "approval_request_id": "approval-1",
        "recovery_key": "recovery-key-1",
        "team_id": child.team_id,
    }
    child.status_reason_details = {
        "model_recovery_release": release
    }
    release["task_payload_digest"] = (
        calculate_recovery_task_payload_digest(child)
    )
    repos = SimpleNamespace(
        task_repo=MemoryRepository([child]),
        artifact_repo=MemoryRepository(
            [artifact] if artifact is not None else []
        ),
        artifact_version_repo=MemoryRepository(
            [version] if version is not None else []
        ),
    )
    verification = GateDerivedVerification()
    grounding = RecoveryGroundingVerificationService(
        hub_run_evidence_provider=hub_run_evidence_provider,
    )
    service = RecoveryResultVerificationService(
        repository_provider=lambda: repos,
        verification_service_provider=lambda: verification,
        grounding_verification_service_provider=lambda: grounding,
    )
    return child, repos, verification, service


def test_hub_reverifies_src_claim_against_task_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child, _repos, verification, service = _fixture()
    child.verification_status = {
        "source_catalog": _task_source_catalog(child),
        "answer_verification": {
            "citation_verification_status": "worker_forged_passed"
        },
    }
    status_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "agent.services.task_runtime_service.update_local_task_status",
        lambda task_id, status, **_values: status_calls.append(
            (task_id, status)
        ),
    )

    result = service.verify_and_record(
        task_id=child.id,
        response={
            "status": "completed",
            "exit_code": 0,
            "output": _answer(
                claim_type="source_fact",
                citation_ref="SRC_0001",
            ),
            # Response-side catalogs are not an authority source.
            "source_catalog": {
                "sources": [{"source_id": "SRC_9999"}]
            },
        },
        artifacts=[],
    )

    assert result is not None
    assert result["status"] == "passed"
    grounding = result["grounding_verification"]
    assert grounding["passed"] is True
    assert grounding["reason_code"] == (
        "recovery_citations_verified"
    )
    assert grounding["provided_source_ids"] == ["SRC_0001"]
    assert grounding["source_catalog_origin"] == (
        "hub_worker_execution_context"
    )
    assert grounding["citation_verification"]["status"] == (
        "verified"
    )
    assert verification.record_calls[0]["gate_results"][
        "passed"
    ] is True
    assert child.verification_status[
        "grounding_verification"
    ] == grounding
    assert status_calls == []


def test_worker_proposal_catalog_must_exactly_match_hub_context() -> None:
    child, _repos, _verification, service = _fixture()
    boundary = SimpleNamespace(
        task_id=child.id,
        phase="propose",
        mutations=[
            {
                "verification_projection": {
                    "source_catalog": _task_source_catalog(
                        child
                    )
                }
            }
        ],
    )
    proposal_envelope = RecoveryWorkerResultService().build(
        boundary
    )
    child.verification_status = {
        "recovery_worker_results": {
            "propose": proposal_envelope
        }
    }

    result = service.verify_and_record(
        task_id=child.id,
        response={
            "status": "completed",
            "exit_code": 0,
            "output": _answer(
                claim_type="source_fact",
                citation_ref="SRC_0001",
            ),
        },
        artifacts=[],
    )

    assert result is not None
    grounding = result["grounding_verification"]
    assert result["status"] == "passed"
    assert grounding["source_catalog_origin"] == (
        "hub_worker_execution_context"
    )
    assert grounding["provided_source_ids"] == ["SRC_0001"]


def test_worker_proposal_cannot_add_source_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child, _repos, verification, service = _fixture()
    worker_catalog = _task_source_catalog(child)
    worker_catalog["sources"][0]["source_id"] = "SRC_9999"
    worker_catalog["sources"][0]["source_ref"][
        "source_id"
    ] = "SRC_9999"
    boundary = SimpleNamespace(
        task_id=child.id,
        phase="propose",
        mutations=[
            {
                "verification_projection": {
                    "source_catalog": worker_catalog
                }
            }
        ],
    )
    child.verification_status = {
        "recovery_worker_results": {
            "propose": RecoveryWorkerResultService().build(
                boundary
            )
        }
    }
    monkeypatch.setattr(
        "agent.services.task_runtime_service.update_local_task_status",
        lambda *_args, **_values: None,
    )

    result = service.verify_and_record(
        task_id=child.id,
        response={
            "status": "completed",
            "exit_code": 0,
            "output": _answer(
                claim_type="source_fact",
                citation_ref="SRC_0001",
            ),
        },
        artifacts=[],
    )

    assert result is not None
    assert result["status"] == "failed"
    assert result["grounding_verification"]["reason_code"] == (
        "recovery_worker_source_catalog_mismatch"
    )
    assert verification.record_calls[0]["gate_results"][
        "passed"
    ] is False


def test_unknown_src_id_fails_closed_on_the_hub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child, _repos, verification, service = _fixture()
    status_calls: list[tuple[str, str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "agent.services.task_runtime_service.update_local_task_status",
        lambda task_id, status, **values: status_calls.append(
            (task_id, status, values)
        ),
    )

    result = service.verify_and_record(
        task_id=child.id,
        response={
            "status": "completed",
            "exit_code": 0,
            "output": _answer(
                claim_type="source_fact",
                citation_ref="SRC_9999",
            ),
        },
        artifacts=[],
    )

    assert result is not None
    grounding = result["grounding_verification"]
    assert result["status"] == "failed"
    assert grounding["passed"] is False
    assert grounding["reason_code"] == (
        "recovery_output_unknown_source_id"
    )
    assert grounding["unknown_source_ids"] == ["SRC_9999"]
    assert verification.record_calls[0]["gate_results"][
        "passed"
    ] is False
    assert status_calls[0][:2] == (
        child.id,
        "verification_failed",
    )


def test_worker_run_reference_is_not_hub_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child, _repos, verification, service = _fixture(
        verification_status={
            "answer_verification": {
                "tool_run_refs": [
                    _run("recovery-grounding-child")
                ]
            }
        }
    )
    monkeypatch.setattr(
        "agent.services.task_runtime_service.update_local_task_status",
        lambda *_args, **_values: None,
    )

    result = service.verify_and_record(
        task_id=child.id,
        response={
            "status": "completed",
            "exit_code": 0,
            "output": _answer(
                claim_type="tool_result",
                citation_ref="RUN_0001",
            ),
        },
        artifacts=[],
    )

    assert result is not None
    assert result["status"] == "failed"
    grounding = result["grounding_verification"]
    assert grounding["reason_code"] == (
        "recovery_hub_run_evidence_missing"
    )
    assert grounding["unknown_source_ids"] == ["RUN_0001"]
    assert grounding["provided_run_ids"] == []
    assert grounding["hub_run_authority_available"] is False
    assert verification.record_calls[0]["gate_results"][
        "passed"
    ] is False


def test_injected_hub_run_repository_can_ground_tool_claim() -> None:
    child, _repos, verification, service = _fixture(
        verification_status={
            "answer_verification": {
                "tool_run_refs": [
                    _run("recovery-grounding-child")
                ]
            }
        },
        hub_run_evidence_provider=lambda task_id: [
            _run(task_id)
        ],
    )

    result = service.verify_and_record(
        task_id=child.id,
        response={
            "status": "completed",
            "exit_code": 0,
            "output": _answer(
                claim_type="tool_result",
                citation_ref="RUN_0001",
            ),
        },
        artifacts=[],
    )

    assert result is not None
    assert result["status"] == "passed"
    grounding = result["grounding_verification"]
    assert grounding["provided_run_ids"] == ["RUN_0001"]
    assert grounding["hub_run_authority_available"] is True
    assert grounding["citation_verification"]["status"] == (
        "verified"
    )
    assert verification.record_calls[0]["gate_results"][
        "passed"
    ] is True


@pytest.mark.parametrize(
    ("output", "reason_code"),
    [
        ("", "recovery_result_evidence_missing"),
        (
            "Worker says the recovery succeeded.",
            "recovery_grounded_answer_required",
        ),
    ],
)
def test_output_without_grounding_or_artifact_cannot_pass_generic_gate(
    output: str,
    reason_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child, _repos, verification, service = _fixture()
    monkeypatch.setattr(
        "agent.services.task_runtime_service.update_local_task_status",
        lambda *_args, **_values: None,
    )

    result = service.verify_and_record(
        task_id=child.id,
        response={
            "status": "completed",
            "exit_code": 0,
            "output": output,
        },
        artifacts=[],
    )

    assert result is not None
    assert result["status"] == "failed"
    assert result["grounding_verification"]["reason_code"] == (
        reason_code
    )
    assert verification.record_calls[0]["gate_results"][
        "passed"
    ] is False


def test_task_context_mutation_breaks_approval_payload_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child, _repos, verification, service = _fixture()
    child.worker_execution_context["context"]["chunks"][0][
        "metadata"
    ]["source_id"] = "SRC_9999"
    monkeypatch.setattr(
        "agent.services.task_runtime_service.update_local_task_status",
        lambda *_args, **_values: None,
    )

    result = service.verify_and_record(
        task_id=child.id,
        response={
            "status": "completed",
            "exit_code": 0,
            "output": _answer(
                claim_type="source_fact",
                citation_ref="SRC_9999",
            ),
        },
        artifacts=[],
    )

    assert result is not None
    assert result["status"] == "failed"
    assert result["grounding_verification"]["reason_code"] == (
        "recovery_task_payload_binding_invalid"
    )
    assert verification.record_calls[0]["gate_results"][
        "passed"
    ] is False


def test_failure_status_can_be_deferred_until_result_guard_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child, _repos, verification, service = _fixture()
    status_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        "agent.services.task_runtime_service.update_local_task_status",
        lambda *args, **_values: status_calls.append(args),
    )

    result = service.verify_and_record(
        task_id=child.id,
        response={
            "status": "completed",
            "exit_code": 0,
            "output": "",
        },
        artifacts=[],
        publish_failure_status=False,
    )

    assert result is not None
    assert result["status"] == "failed"
    assert verification.record_calls[0]["gate_results"][
        "passed"
    ] is False
    assert status_calls == []


def test_legacy_worker_db_artifact_is_not_hub_authority() -> None:
    artifact = SimpleNamespace(
        id="artifact-1",
        latest_version_id="version-1",
        created_by="http://worker:5000",
    )
    version = SimpleNamespace(
        id="version-1",
        artifact_id=artifact.id,
        sha256="a" * 64,
    )
    child, _repos, verification, service = _fixture(
        expected_artifacts=[
            {
                "relative_path": "result.txt",
                "required": True,
            }
        ],
        artifact=artifact,
        version=version,
    )

    result = service.verify_and_record(
        task_id=child.id,
        response={
            "status": "completed",
            "exit_code": 0,
            "output": "",
        },
        artifacts=[
            {
                "artifact_id": artifact.id,
                "artifact_version_id": version.id,
                "task_id": child.id,
                "relative_path": "result.txt",
                "content_hash": "a" * 64,
            }
        ],
    )

    assert result is not None
    assert result["status"] == "failed"
    grounding = result["grounding_verification"]
    assert grounding["reason_code"] == (
        "recovery_result_evidence_missing"
    )
    assert grounding["verified_artifact_present"] is False
    assert verification.record_calls[0]["gate_results"][
        "passed"
    ] is False
