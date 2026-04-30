from __future__ import annotations

from client_surfaces.blender.addon.execution import execute_approved_action


def test_blender_policy_denies_without_approval():
    out=execute_approved_action(approved=False,script_hash='h',correlation_id='c')
    assert out["status"]=="blocked"


def test_blender_policy_allows_with_approval():
    out=execute_approved_action(approved=True,script_hash='h',correlation_id='c')
    assert out["status"]=="completed"
