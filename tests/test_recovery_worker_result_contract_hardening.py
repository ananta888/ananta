from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from agent.common.recovery_result_write_boundary import (
    defer_recovery_task_writes,
    defer_task_status_mutation,
)
from agent.services.recovery_dispatch_gate_service import (
    recovery_dispatch_request_fingerprint,
)
from agent.services.recovery_plan_contract import (
    build_recovery_dependency_binding,
)
from agent.services.recovery_worker_result_service import (
    RECOVERY_WORKER_RESULT_SCHEMA,
    RecoveryWorkerResultError,
    RecoveryWorkerResultService,
)


def _digest(envelope: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in envelope.items()
        if key != "digest"
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _envelope(
    projection: dict[str, Any],
    *,
    task_id: str = "recovery-child",
) -> dict[str, Any]:
    value = {
        "schema": RECOVERY_WORKER_RESULT_SCHEMA,
        "task_id": task_id,
        "phase": "propose",
        "verification_projection": projection,
        "digest": "",
    }
    value["digest"] = _digest(value)
    return value


def test_validate_rejects_recomputed_digest_with_unknown_projection_key() -> None:
    forged = _envelope(
        {
            "source_catalog": {"sources": []},
            "status": "passed",
        }
    )

    with pytest.raises(
        RecoveryWorkerResultError,
        match="projection_fields_invalid",
    ):
        RecoveryWorkerResultService.validate(
            forged,
            task_id="recovery-child",
            phase="propose",
        )


@pytest.mark.parametrize(
    ("projection", "reason"),
    [
        (
            {"source_catalog": {"__proto__": {"status": "passed"}}},
            "json_key_invalid",
        ),
        (
            {"source_catalog": {"sources": [{}] * 2_049}},
            "json_collection_limit_exceeded",
        ),
        (
            {"llm_diagnostics": {"detail": "x" * 131_073}},
            "json_string_limit_exceeded",
        ),
    ],
)
def test_validate_enforces_bounded_json_projection(
    projection: dict[str, Any],
    reason: str,
) -> None:
    forged = _envelope(projection)

    with pytest.raises(RecoveryWorkerResultError, match=reason):
        RecoveryWorkerResultService.validate(
            forged,
            task_id="recovery-child",
            phase="propose",
        )


def test_validate_rejects_non_json_projection_without_string_coercion() -> None:
    forged = {
        "schema": RECOVERY_WORKER_RESULT_SCHEMA,
        "task_id": "recovery-child",
        "phase": "propose",
        "verification_projection": {
            "llm_diagnostics": {"opaque": object()}
        },
        "digest": "0" * 64,
    }

    with pytest.raises(
        RecoveryWorkerResultError,
        match="not_json",
    ):
        RecoveryWorkerResultService.validate(
            forged,
            task_id="recovery-child",
            phase="propose",
        )


def test_validate_rejects_projection_over_encoded_envelope_limit() -> None:
    escaped_value = "\U0001f600" * 30_000
    oversized = _envelope(
        {
            "llm_diagnostics": {
                "first": escaped_value,
                "second": escaped_value,
                "third": escaped_value,
                "fourth": escaped_value,
            }
        }
    )

    with pytest.raises(
        RecoveryWorkerResultError,
        match="recovery_worker_result_too_large",
    ):
        RecoveryWorkerResultService.validate(
            oversized,
            task_id="recovery-child",
            phase="propose",
        )


def test_apply_proposal_context_never_overwrites_hub_authority() -> None:
    service = RecoveryWorkerResultService()
    worker_catalog = {"sources": [{"source_id": "SRC_WORKER"}]}
    envelope = _envelope(
        {
            "source_catalog": worker_catalog,
            "answer_verification": {"status": "worker_evidence"},
        }
    )
    local_task = {
        "id": "recovery-child",
        "verification_status": {
            "status": "pending",
            "record_id": "hub-record",
            "source_catalog": {
                "sources": [{"source_id": "SRC_HUB"}]
            },
        },
    }

    service.apply_proposal_context(
        task=local_task,
        value=envelope,
    )

    verification = local_task["verification_status"]
    assert verification["status"] == "pending"
    assert verification["record_id"] == "hub-record"
    assert verification["source_catalog"]["sources"] == [
        {"source_id": "SRC_HUB"}
    ]
    assert verification["answer_verification"] == {
        "status": "worker_evidence"
    }
    assert (
        verification["recovery_worker_results"]["propose"]
        == envelope
    )


def test_apply_rejects_a_different_existing_hub_proposal_envelope() -> None:
    service = RecoveryWorkerResultService()
    authoritative = _envelope(
        {"source_catalog": {"sources": [{"source_id": "SRC_1"}]}}
    )
    different = _envelope(
        {"source_catalog": {"sources": [{"source_id": "SRC_2"}]}}
    )
    local_task = {
        "id": "recovery-child",
        "verification_status": {
            "recovery_worker_results": {
                "propose": authoritative,
            }
        },
    }

    with pytest.raises(
        RecoveryWorkerResultError,
        match="recovery_proposal_context_mismatch",
    ):
        service.apply_proposal_context(
            task=local_task,
            value=different,
        )
    assert local_task["verification_status"][
        "recovery_worker_results"
    ]["propose"] == authoritative


def test_execute_fingerprint_cryptographically_binds_proposal_envelope() -> None:
    first = _envelope(
        {"source_catalog": {"sources": [{"source_id": "SRC_1"}]}}
    )
    second = _envelope(
        {"source_catalog": {"sources": [{"source_id": "SRC_2"}]}}
    )
    payload = {
        "task_id": "recovery-child",
        "command": "verify",
        "recovery_proposal_context": first,
    }

    first_fingerprint = recovery_dispatch_request_fingerprint(
        "execute",
        payload,
    )
    second_fingerprint = recovery_dispatch_request_fingerprint(
        "execute",
        {
            **payload,
            "recovery_proposal_context": second,
        },
    )

    assert first_fingerprint != second_fingerprint
    assert (
        first_fingerprint
        == recovery_dispatch_request_fingerprint(
            "execute",
            dict(payload),
        )
    )


def test_execute_context_must_match_authoritative_proposal_envelope() -> None:
    service = RecoveryWorkerResultService()
    authoritative = _envelope(
        {"source_catalog": {"sources": [{"source_id": "SRC_1"}]}}
    )
    different = _envelope(
        {"source_catalog": {"sources": [{"source_id": "SRC_2"}]}}
    )
    task = {
        "id": "recovery-child",
        "verification_status": {
            "recovery_worker_results": {
                "propose": authoritative,
            }
        },
    }

    assert service.bind_execute_proposal_context(
        task=task,
        value=authoritative,
    ) == authoritative
    with pytest.raises(
        RecoveryWorkerResultError,
        match="recovery_proposal_context_mismatch",
    ):
        service.bind_execute_proposal_context(
            task=task,
            value=different,
        )
    with pytest.raises(
        RecoveryWorkerResultError,
        match="recovery_proposal_context_required",
    ):
        service.bind_execute_proposal_context(
            task=task,
            value=None,
        )
    with pytest.raises(
        RecoveryWorkerResultError,
        match="recovery_proposal_context_authority_invalid",
    ):
        service.bind_execute_proposal_context(
            task={
                "id": "recovery-child",
                "verification_status": {
                    "recovery_worker_results": "corrupt"
                },
            },
            value=None,
        )
    with pytest.raises(
        RecoveryWorkerResultError,
        match="recovery_proposal_context_unexpected",
    ):
        service.bind_execute_proposal_context(
            task={
                "id": "recovery-child",
                "verification_status": {},
            },
            value=authoritative,
        )


def test_build_projection_uses_only_the_closed_boundary_allowlist() -> None:
    with defer_recovery_task_writes(
        task_id="recovery-child",
        phase="propose",
    ) as boundary:
        assert defer_task_status_mutation(
            "recovery-child",
            "proposing",
            event_type=None,
            event_actor="worker",
            event_details=None,
            force=False,
            values={
                "verification_status": {
                    "source_catalog": {"sources": []},
                    "status": "passed",
                    "record_id": "worker-record",
                }
            },
        )

    envelope = RecoveryWorkerResultService.build(boundary)

    assert set(envelope["verification_projection"]) == {
        "source_catalog"
    }


@pytest.mark.parametrize(
    ("preexisting", "children"),
    [
        (["source-task"], ["child-1"]),
        ([], ["source-task"]),
    ],
)
def test_dependency_binding_rejects_source_self_dependency(
    preexisting: list[str],
    children: list[str],
) -> None:
    with pytest.raises(
        ValueError,
        match="recovery_dependency_binding_invalid",
    ):
        build_recovery_dependency_binding(
            source_task_id="source-task",
            preexisting_dependency_ids=preexisting,
            child_task_ids=children,
        )
