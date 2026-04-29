from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from worker.core.degraded import build_degraded_state

_STATUS_ORDER = ("failed", "degraded", "inconclusive", "skipped", "passed")
_SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas" / "worker"
_SCHEMA_FILES = {
    "worker_execution_request.v1": "worker_execution_request.v1.json",
    "worker_execution_result.v1": "worker_execution_result.v1.json",
    "worker_execution_profile.v1": "worker_execution_profile.v1.json",
    "patch_artifact.v1": "patch_artifact.v1.json",
    "command_plan_artifact.v1": "command_plan_artifact.v1.json",
    "test_result_artifact.v1": "test_result_artifact.v1.json",
    "verification_artifact.v1": "verification_artifact.v1.json",
}


def _normalize_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"passed", "failed", "skipped", "degraded", "inconclusive"}:
        return normalized
    return "inconclusive"


def _overall_status(statuses: list[str]) -> str:
    if not statuses:
        return "skipped"
    normalized = [_normalize_status(item) for item in statuses]
    for candidate in _STATUS_ORDER:
        if candidate in normalized:
            return candidate
    return "inconclusive"


def build_verification_artifact(
    *,
    task_id: str,
    test_results: list[dict[str, Any]] | None = None,
    patch_artifact: dict[str, Any] | None = None,
    extra_evidence_refs: list[str] | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    evidence_refs: list[str] = []

    for index, test_result in enumerate(list(test_results or []), start=1):
        status = _normalize_status(str(test_result.get("status") or "inconclusive"))
        command = str(test_result.get("command") or "").strip()
        exit_code = test_result.get("exit_code")
        checks.append(
            {
                "check_id": f"test_result_{index}",
                "status": status,
                "detail": f"command={command or '<none>'}; exit_code={exit_code}",
            }
        )
        stdout_ref = str(test_result.get("stdout_ref") or "").strip()
        stderr_ref = str(test_result.get("stderr_ref") or "").strip()
        if stdout_ref:
            evidence_refs.append(stdout_ref)
        if stderr_ref:
            evidence_refs.append(stderr_ref)

    if patch_artifact is not None:
        patch_hash = str(patch_artifact.get("patch_hash") or "").strip()
        checks.append(
            {
                "check_id": "patch_artifact_presence",
                "status": "passed" if patch_hash else "inconclusive",
                "detail": "patch hash recorded" if patch_hash else "patch hash missing",
            }
        )
        if patch_hash:
            evidence_refs.append(f"patch:{patch_hash}")

    for ref in list(extra_evidence_refs or []):
        normalized = str(ref).strip()
        if normalized:
            evidence_refs.append(normalized)

    if not checks:
        checks.append(
            {
                "check_id": "verification_skipped",
                "status": "skipped",
                "detail": "No test results or patch evidence available for verification.",
            }
        )

    status = _overall_status([str(item.get("status") or "") for item in checks])
    deduplicated_evidence = list(dict.fromkeys(evidence_refs))
    if not deduplicated_evidence:
        deduplicated_evidence = ["verification:no-external-evidence"]

    return {
        "schema": "verification_artifact.v1",
        "task_id": str(task_id).strip(),
        "status": status,
        "checks": checks,
        "evidence_refs": deduplicated_evidence,
    }


def validate_worker_schema_payload(*, schema_name: str, payload: dict[str, Any]) -> None:
    schema_key = str(schema_name or "").strip()
    file_name = _SCHEMA_FILES.get(schema_key)
    if not file_name:
        raise ValueError(f"unknown_worker_schema:{schema_key or '<missing>'}")
    schema = json.loads((_SCHEMA_ROOT / file_name).read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(dict(payload or {})))
    if not errors:
        return
    first = errors[0]
    path = ".".join(str(item) for item in first.path) or "<root>"
    raise ValueError(f"schema_invalid:{schema_key}:{path}:{first.message}")


def validate_worker_schema_or_degraded(
    *,
    schema_name: str,
    payload: dict[str, Any],
    direction: str,
) -> tuple[bool, dict[str, Any] | None]:
    try:
        validate_worker_schema_payload(schema_name=schema_name, payload=payload)
        return True, None
    except ValueError as exc:
        return False, build_degraded_state(
            state="schema_invalid",
            machine_reason="schema_validation_failed",
            details={
                "schema_name": str(schema_name or "").strip(),
                "direction": str(direction or "").strip() or "unknown",
                "error": str(exc),
            },
        )
