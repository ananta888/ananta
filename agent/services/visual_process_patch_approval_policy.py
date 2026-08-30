"""Hub-owned approval policy for Visual Process patch decisions."""

from __future__ import annotations

from dataclasses import dataclass


class VisualProcessPatchApprovalError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class VisualProcessPatchApproval:
    mode: str
    reason_code: str
    human_intervention_required: bool


class VisualProcessPatchApprovalPolicy:
    """Authorize acceptance without weakening patch validation or persistence."""

    def authorize_acceptance(
        self,
        *,
        mode: str,
        confirmed: bool,
        hub_auto_enabled: bool,
    ) -> VisualProcessPatchApproval:
        normalized = str(mode or "interactive").strip().lower()
        if normalized == "interactive":
            if not confirmed:
                raise VisualProcessPatchApprovalError(
                    "assistant_patch_confirmation_required",
                    status_code=428,
                )
            return VisualProcessPatchApproval(
                mode=normalized,
                reason_code="patch_user_confirmed",
                human_intervention_required=True,
            )
        if normalized == "hub_auto":
            if confirmed:
                raise VisualProcessPatchApprovalError(
                    "assistant_patch_approval_mode_conflict",
                    status_code=422,
                )
            if not hub_auto_enabled:
                raise VisualProcessPatchApprovalError(
                    "assistant_patch_auto_approval_disabled",
                    status_code=403,
                )
            return VisualProcessPatchApproval(
                mode=normalized,
                reason_code="patch_hub_policy_auto_approved",
                human_intervention_required=False,
            )
        raise VisualProcessPatchApprovalError(
            "assistant_patch_approval_mode_invalid",
            status_code=422,
        )


__all__ = [
    "VisualProcessPatchApproval",
    "VisualProcessPatchApprovalError",
    "VisualProcessPatchApprovalPolicy",
]
