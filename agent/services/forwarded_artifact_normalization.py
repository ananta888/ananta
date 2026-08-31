"""Fail-closed normalization for artifacts returned by remote workers."""

from __future__ import annotations


def normalize_forwarded_artifacts(
    *, task_id: str, artifacts: list[dict] | None,
) -> list[dict] | None:
    if artifacts is None:
        return None
    normalized: list[dict] = []
    for index, item in enumerate(artifacts, start=1):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        artifact_id = str(row.get("artifact_id") or row.get("id") or "").strip()
        kind = str(row.get("kind") or "").strip()
        path = str(
            row.get("path")
            or row.get("name")
            or row.get("filename")
            or row.get("title")
            or ""
        ).strip()
        artifact_id = artifact_id or f"{task_id}-artifact-{index:03d}"
        row["artifact_id"] = artifact_id
        row.setdefault("id", artifact_id)
        row["kind"] = kind or "task_output"
        if path:
            row["path"] = path
        row.setdefault("task_id", task_id)
        normalized.append(row)
    return normalized


def normalize_recovery_forwarded_artifacts(
    *,
    task_id: str,
    artifacts: object,
) -> list[dict] | None:
    """Reject unbounded or open Worker artifact claims before any Hub write."""

    if artifacts is None:
        return None
    from ananta_contracts.recovery_artifact_ingress import (
        RecoveryArtifactIngressContractError,
        validate_recovery_artifact_receipt_list,
    )

    try:
        receipts = validate_recovery_artifact_receipt_list(
            artifacts,
            task_id=task_id,
        )
    except RecoveryArtifactIngressContractError as exc:
        raise ValueError(exc.reason_code) from exc
    return normalize_forwarded_artifacts(task_id=task_id, artifacts=receipts)
