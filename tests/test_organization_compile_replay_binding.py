from __future__ import annotations

import pytest

from agent.services.organization_compile_application_service import (
    OrganizationCompileApplicationService,
    OrganizationCompileBindingError,
)


def _service() -> OrganizationCompileApplicationService:
    return OrganizationCompileApplicationService(
        catalog=None,  # type: ignore[arg-type]
        admission_policy=None,
        signing_secret="organization-replay-test-secret",
        token_ttl_seconds=60,
    )


def _bound_plan(service: OrganizationCompileApplicationService) -> tuple[dict, dict]:
    bound = {
        "schema": "organization_compile_token.v1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "principal_id": "operator-1",
        "organization_id": "organization-1",
        "definition_ref": "enterprise_scrum_organization@1",
        "definition_revision": "b" * 64,
        "composition_mode": "standard",
        "team_count": 8,
        "custom_composition": None,
        "admission_exception_ref": None,
        "plan_digest": "a" * 64,
        "admin_policy_hash": "c" * 64,
        "title": "Organization One",
    }
    client_plan = {
        **{key: bound[key] for key in (
            "organization_id",
            "definition_revision",
            "plan_digest",
            "admin_policy_hash",
        )},
        "compile_token": service._signer.dumps(bound),
    }
    return bound, client_plan


def test_replay_binding_keeps_signature_and_scope_checks_but_ignores_only_ttl() -> None:
    service = _service()
    bound, client_plan = _bound_plan(service)
    service._ttl = -1

    with pytest.raises(OrganizationCompileBindingError) as exc:
        service.recompile_bound_plan(
            tenant_id="tenant-1",
            project_id="project-1",
            principal_id="operator-1",
            client_plan=client_plan,
        )

    assert exc.value.reason_code == "organization_compile_plan_expired"
    assert service.verify_replay_binding(
        tenant_id="tenant-1",
        project_id="project-1",
        principal_id="operator-1",
        client_plan=client_plan,
    ) == bound


@pytest.mark.parametrize(
    ("change", "principal_id", "reason_code"),
    (
        ({}, "other-operator", "organization_compile_scope_mismatch"),
        ({"plan_digest": "d" * 64}, "operator-1", "organization_plan_digest_binding_invalid"),
        ({"compile_token": "tampered-token"}, "operator-1", "organization_compile_token_invalid"),
    ),
)
def test_replay_binding_fails_closed_for_tampered_or_cross_scope_input(
    change: dict[str, str],
    principal_id: str,
    reason_code: str,
) -> None:
    service = _service()
    _bound, client_plan = _bound_plan(service)
    client_plan.update(change)

    with pytest.raises(OrganizationCompileBindingError) as exc:
        service.verify_replay_binding(
            tenant_id="tenant-1",
            project_id="project-1",
            principal_id=principal_id,
            client_plan=client_plan,
        )

    assert exc.value.reason_code == reason_code
