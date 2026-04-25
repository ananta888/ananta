from __future__ import annotations

from typing import Any

_STATUS_ORDER = ("failed", "degraded", "inconclusive", "skipped", "passed")


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
