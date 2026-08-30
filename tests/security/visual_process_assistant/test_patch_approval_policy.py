from __future__ import annotations

import pytest

from agent.services.visual_process_patch_approval_policy import (
    VisualProcessPatchApprovalError,
    VisualProcessPatchApprovalPolicy,
)


def test_interactive_acceptance_requires_confirmation() -> None:
    policy = VisualProcessPatchApprovalPolicy()

    with pytest.raises(VisualProcessPatchApprovalError, match="assistant_patch_confirmation_required"):
        policy.authorize_acceptance(mode="interactive", confirmed=False, hub_auto_enabled=True)

    approval = policy.authorize_acceptance(mode="interactive", confirmed=True, hub_auto_enabled=False)
    assert approval.reason_code == "patch_user_confirmed"
    assert approval.human_intervention_required is True


def test_hub_auto_acceptance_is_explicit_default_off_and_headless() -> None:
    policy = VisualProcessPatchApprovalPolicy()

    with pytest.raises(VisualProcessPatchApprovalError, match="assistant_patch_auto_approval_disabled"):
        policy.authorize_acceptance(mode="hub_auto", confirmed=False, hub_auto_enabled=False)
    with pytest.raises(VisualProcessPatchApprovalError, match="assistant_patch_approval_mode_conflict"):
        policy.authorize_acceptance(mode="hub_auto", confirmed=True, hub_auto_enabled=True)

    approval = policy.authorize_acceptance(mode="hub_auto", confirmed=False, hub_auto_enabled=True)
    assert approval.reason_code == "patch_hub_policy_auto_approved"
    assert approval.human_intervention_required is False
