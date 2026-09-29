from __future__ import annotations

from scripts.run_hub_evidence_github_oauth_gate import (
    oauth_scope_assessment,
    projection_passed,
)


def _execution() -> dict:
    return {
        "assignment_bound": True,
        "refresh_status": "ok",
        "immutable_ref": "git-commit:" + "a" * 40,
        "item_count": 12,
        "total_bytes": 100,
        "manifest_digest": "b" * 64,
        "credential_ref_opaque": True,
        "token_exposed": False,
        "revoked_health": "authorization_required",
        "revoked_index_reason": "authorization_required",
    }


def test_projection_requires_refresh_immutable_content_and_revocation() -> None:
    execution = _execution()

    assert projection_passed(
        execution, expected_immutable_ref="git-commit:" + "a" * 40
    )

    execution["revoked_index_reason"] = None
    assert not projection_passed(
        execution, expected_immutable_ref="git-commit:" + "a" * 40
    )


def test_scope_assessment_does_not_call_broad_oauth_grant_least_privilege() -> None:
    assessment = oauth_scope_assessment(
        frozenset({"repo", "workflow", "gist"}), visibility="public"
    )

    assert assessment["required_repository_scope_present"] is True
    assert assessment["minimal_for_visibility"] is False
    assert assessment["observed"] == ["gist", "repo", "workflow"]


def test_scope_assessment_accepts_minimal_public_repository_grant() -> None:
    assessment = oauth_scope_assessment(
        frozenset({"public_repo"}), visibility="public"
    )

    assert assessment["required_repository_scope_present"] is True
    assert assessment["minimal_for_visibility"] is True
