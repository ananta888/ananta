from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ArtifactGuardDecision:
    task_id: str
    status: str
    reason_code: str
    verified_progress: float
    points_awarded: int

    def to_dict(self) -> dict[str, str | float | int]:
        return asdict(self)


class ArtifactGuard:
    def __init__(self, *, points_per_verified_task: int = 10) -> None:
        self._points_per_verified_task = points_per_verified_task

    def verify_completion(
        self,
        *,
        task_id: str,
        evidence_refs: list[str] | tuple[str, ...],
        verification_passed: bool,
        artifact_fresh: bool = True,
    ) -> ArtifactGuardDecision:
        if not evidence_refs:
            return ArtifactGuardDecision(
                task_id=task_id,
                status="open",
                reason_code="missing_evidence",
                verified_progress=0.0,
                points_awarded=0,
            )
        if not verification_passed:
            return ArtifactGuardDecision(
                task_id=task_id,
                status="failed",
                reason_code="verification_failed",
                verified_progress=0.0,
                points_awarded=0,
            )
        if not artifact_fresh:
            return ArtifactGuardDecision(
                task_id=task_id,
                status="open",
                reason_code="stale_artifact",
                verified_progress=0.0,
                points_awarded=0,
            )
        return ArtifactGuardDecision(
            task_id=task_id,
            status="verified",
            reason_code="verification_success",
            verified_progress=100.0,
            points_awarded=self._points_per_verified_task,
        )
