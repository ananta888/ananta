"""Artifact-first task completion policy application."""

from __future__ import annotations

import logging


def apply_artifact_first_completion(
    tid: str,
    *,
    collection_result: dict,
    advisory_parse_result: dict | None = None,
    exit_code: int | None = None,
    retry_count: int = 0,
    expected_paths: list[str] | None = None,
    verification_required: bool = False,
    allow_synthesized_manifest: bool = False,
) -> str:
    """Apply artifact-first completion without retrying valid-artifact parse failures."""

    from agent.services.task_artifact_completion_gate_service import get_task_artifact_completion_gate_service
    from agent.services.task_retry_policy_service import (
        REASON_ADVISORY_JSON_PARSE_FAILED,
        get_task_retry_policy_service,
    )
    from agent.services.task_runtime_service import update_local_task_status

    completion_gate = get_task_artifact_completion_gate_service()
    final_status, decision = completion_gate.decide(
        task_id=tid,
        collection_result=collection_result,
        advisory_parse_result=advisory_parse_result,
        exit_code=exit_code,
        retry_count=retry_count,
        expected_paths=expected_paths,
        verification_required=verification_required,
        allow_synthesized_manifest=allow_synthesized_manifest,
    )
    if advisory_parse_result and advisory_parse_result.get("parse_error"):
        retry_classification = get_task_retry_policy_service().classify(
            reason=REASON_ADVISORY_JSON_PARSE_FAILED,
            retry_count=retry_count,
            has_valid_artifacts=bool(collection_result.get("manifest_valid")),
        )
        if retry_classification.classification == "ignored":
            logging.info(
                "artifact parse failed but artifacts are valid for task %s; not requeueing "
                "(reason_code=advisory_parse_failed_ignored)",
                tid,
            )
    update_local_task_status(
        tid,
        final_status,
        event_type="artifact_first_completion",
        event_actor="system",
        event_details=completion_gate.event_details(decision=decision),
    )
    return final_status
