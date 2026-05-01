from __future__ import annotations

from client_surfaces.blender.addon.policy import capability_policy_state, render_policy_state


def test_policy_mapping_states() -> None:
    assert render_policy_state("denied") == "policy_denied"
    assert render_policy_state("approval_required") == "approval_required"
    assert render_policy_state("allow") == "allowed"
    assert capability_policy_state({"approval_required": True}) == "approval_required"
