"""Deterministic identities for Hub-delegated workflow adapter tasks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Protocol


class WorkflowAdapterIdentityView(Protocol):
    tenant_id: str
    subject_id: str
    workflow_id: str
    run_id: str
    step_id: str
    adapter_kind: str
    command: str
    idempotency_key: str
    payload: dict[str, Any]
    provider_binding: Any
    provider_decision_reason: str


def hub_task_id(submission: WorkflowAdapterIdentityView) -> str:
    digest = hashlib.sha256(
        "\x00".join(
            (
                submission.tenant_id,
                submission.subject_id,
                submission.workflow_id,
                submission.run_id,
                submission.step_id,
                submission.adapter_kind,
                submission.command,
                submission.idempotency_key,
            )
        ).encode("utf-8")
    ).hexdigest()[:28]
    return f"wfa-{digest}"


def hub_task_id_from_context(context: Mapping[str, Any]) -> str:
    values = (
        context.get("tenant_id"),
        context.get("subject_id"),
        context.get("workflow_id"),
        context.get("run_id"),
        context.get("step_id"),
        context.get("adapter_kind"),
        context.get("command"),
        context.get("idempotency_key"),
    )
    if any(not str(value or "") for value in values):
        return ""
    digest = hashlib.sha256(
        "\x00".join(str(value) for value in values).encode("utf-8")
    ).hexdigest()[:28]
    return f"wfa-{digest}"


def operation_id(submission: WorkflowAdapterIdentityView) -> str:
    digest = hashlib.sha256(
        "\x00".join(
            (
                submission.tenant_id,
                submission.run_id,
                submission.step_id,
                submission.command,
                submission.idempotency_key,
            )
        ).encode("utf-8")
    ).hexdigest()
    return f"workflow-adapter:{digest}"


def submission_request_digest(submission: WorkflowAdapterIdentityView) -> str:
    rendered = json.dumps(
        {
            "payload": dict(submission.payload),
            "provider_binding": (
                submission.provider_binding.to_dict()
                if submission.provider_binding is not None
                else None
            ),
            "provider_decision_reason": submission.provider_decision_reason,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


__all__ = [
    "WorkflowAdapterIdentityView",
    "hub_task_id",
    "hub_task_id_from_context",
    "operation_id",
    "submission_request_digest",
]
