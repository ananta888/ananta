"""Actual Hub SQL authority, active Task write fences and headless identity receipts."""

from copy import deepcopy
from dataclasses import replace

import jwt
import pytest
from sqlmodel import Session

from agent.db_models import OrganizationRoleAssignmentDB
from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_authority import MeetDialogAuthority
from agent.services.meet_dialog_principal_receipts import MeetDialogPrincipalReceipts
from agent.services.meet_dialog_tasks import HubDialogTasks
from tests.meet_dialog_lifecycle_fixture import PUBLISHER, seed_parent
from tests.test_meet_dialog_avatar_negotiation import system
from tests.test_meet_role_assignment_lifecycle import MUTATIONS, mutate
from tests.test_meet_signing_key_id import signer

pytestmark = pytest.mark.timeout(45)


def start_principal(engine, tmp_path, *, enabled=True):
    seed_parent(engine)
    f = system()
    f.tasks.organization_principals = enabled
    f.service.issuer, key = signer(tmp_path, "synthetic-key-1")
    started = f.service.start(f.principal, "project", f.payload, parent="meet-test-parent")
    task = f.tasks.get_by_id(started["task_id"])
    context = task.worker_execution_context["meet_dialog"]
    scope = f.f.authority.current(task.id, context["lease_id"], context["runtime_id"])
    return f, task, scope, key


def test_new_opted_in_task_keeps_organization_principal_across_hub_reconstruction_and_admission_disable(app, tmp_path):
    from agent.database import engine

    with app.app_context():
        f, task, scope, key = start_principal(engine, tmp_path)
        stored = task.worker_execution_context["meet_machine_principal"]
        assert stored == scope.machine_principal.projection()
        assert stored["assignment_id"] == "meet-test-assignment" and stored["organization_id"] == "meet-test-org"
        wire = f.worker.start_dialog.call_args.args[0]
        claims = jwt.decode(
            wire["meeting"]["grant"], key.public_key(), algorithms=["EdDSA"], audience="ananta-meet-machine-v2"
        )
        assert claims["sub"] == scope.machine_subject != "ananta"
        assert "meet_machine_principal" not in wire and "publisher_url" not in wire
        assert PUBLISHER not in str(claims) and set(claims) == {
            "iss",
            "aud",
            "sub",
            "iat",
            "exp",
            "jti",
            "roomId",
            "taskId",
            "tenantId",
            "projectId",
            "runtimeId",
            "sessionId",
            "capabilities",
        }
        restarted_tasks = HubDialogTasks(publisher_url=PUBLISHER, organization_principals=False)
        restarted = MeetDialogAuthority(restarted_tasks, f.f.binding, f.f.authority.policies, clock=lambda: f.f.now)
        again = restarted.current(task.id, scope.lease_id, scope.runtime_id)
        assert again.machine_principal == scope.machine_principal and again.machine_subject == claims["sub"]
        receipts = MeetDialogPrincipalReceipts(restarted, restarted_tasks, f.service.issuer.issuer)
        observed = receipts.inspect(f.principal, "project", task.id)
        assert observed["principal"] == stored and observed["issuer"] == claims["iss"]
        for value in [PUBLISHER, scope.lease_id, scope.runtime_id, wire["meeting"]["grant"]]:
            assert value not in str(observed)
        # Independent source controls and metadata are still mutable.
        result = f.service.control(
            f.principal,
            "project",
            task.id,
            {"expected_revision": 1, "chat": False, "audio": False, "screen": False, "avatar": True},
        )
        assert result["controls"]["avatar"]["enabled"] is True
        assert f.tasks.get_by_id(task.id).worker_execution_context["meet_machine_principal"] == stored
        assert f.tasks.finish_bound(task.id, scope.lease_id, scope.runtime_id, "cancelled")
        with pytest.raises(MeetError, match="inactive"):
            receipts.inspect(f.principal, "project", task.id)


def test_legacy_organization_task_is_not_retrofitted_by_enabling_new_principals(app, tmp_path):
    from agent.database import engine

    with app.app_context():
        f, task, scope, key = start_principal(engine, tmp_path, enabled=False)
        assert "meet_machine_principal" not in task.worker_execution_context and scope.machine_subject == "ananta"
        f.tasks.organization_principals = True
        current = f.f.authority.current(task.id, scope.lease_id, scope.runtime_id)
        assert current.machine_subject == "ananta"
        token = f.service.issuer.issue_dialog(f.f.authority, task.id, scope.lease_id, scope.runtime_id, f.f.now)
        assert (
            jwt.decode(token["grant"], key.public_key(), algorithms=["EdDSA"], audience="ananta-meet-machine-v2")["sub"]
            == "ananta"
        )
        with pytest.raises(MeetError, match="principal_unavailable"):
            MeetDialogPrincipalReceipts(f.f.authority, f.tasks, f.service.issuer.issuer).inspect(
                f.principal, "project", task.id
            )


def test_active_legacy_task_cannot_be_retrofitted_through_repository_save_or_forced_cas(app, tmp_path):
    from agent.database import engine
    from agent.models.meet_machine_principal import principal_from_verified_role
    from agent.services.repository_registry import get_repository_registry
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    with app.app_context():
        f, task, scope, _ = start_principal(engine, tmp_path, enabled=False)
        context = deepcopy(task.worker_execution_context)
        context["meet_machine_principal"] = principal_from_verified_role(context["meet_role_assignment"]).projection()
        assert not compare_and_set_local_task_status(
            task.id, "in_progress", expected_statuses={"in_progress"}, force=True, worker_execution_context=context
        )
        candidate = f.tasks.get_by_id(task.id)
        candidate.worker_execution_context = context
        with pytest.raises(ValueError, match="^meet_dialog_principal_immutable$"):
            get_repository_registry().task_repo.save(candidate)
        assert f.f.authority.current(task.id, scope.lease_id, scope.runtime_id).machine_subject == "ananta"


def test_opt_in_does_not_invent_an_organization_for_a_project_only_task(app):
    with app.app_context():
        f = system()
        f.tasks.organization_principals = True
        started = f.service.start(f.principal, "project", f.payload)
        task = f.tasks.get_by_id(started["task_id"])
        context = task.worker_execution_context["meet_dialog"]
        assert "meet_machine_principal" not in task.worker_execution_context
        assert f.f.authority.current(task.id, context["lease_id"], context["runtime_id"]).machine_subject == "ananta"


@pytest.mark.parametrize("enabled", [1, 0, "1", "true", None, [], {}])
def test_principal_admission_configuration_rejects_truthy_coercion(enabled):
    with pytest.raises(ValueError, match="^meet_organization_principals_config_invalid$"):
        HubDialogTasks(organization_principals=enabled)


@pytest.mark.parametrize("change", MUTATIONS)
def test_current_principal_cannot_sign_or_return_identity_after_actual_assignment_revocation(app, tmp_path, change):
    from agent.database import engine

    with app.app_context():
        f, task, scope, _ = start_principal(engine, tmp_path)
        with Session(engine) as session:
            mutate(session, change)
        with pytest.raises(MeetError, match="meet_dialog_assignment"):
            f.service.issuer.issue_dialog(f.f.authority, task.id, scope.lease_id, scope.runtime_id, f.f.now)
        with pytest.raises(MeetError, match="meet_dialog_assignment"):
            MeetDialogPrincipalReceipts(f.f.authority, f.tasks, f.service.issuer.issuer).inspect(
                f.principal, "project", task.id
            )
        assert f.tasks.finish_bound(task.id, scope.lease_id, scope.runtime_id, "cancelled")


@pytest.mark.parametrize("change", ["remove", "replace", "null", "role_binding", "scope"])
def test_active_task_save_and_forced_cas_cannot_remove_replace_or_broaden_a_principal(app, tmp_path, change):
    from agent.database import engine
    from agent.services.repository_registry import get_repository_registry
    from agent.services.task_runtime_service import compare_and_set_local_task_status

    with app.app_context():
        f, task, scope, _ = start_principal(engine, tmp_path)
        context = deepcopy(task.worker_execution_context)
        if change == "remove":
            context.pop("meet_machine_principal")
        elif change == "replace":
            context["meet_machine_principal"]["subject"] = "ananta"
        elif change == "null":
            context["meet_machine_principal"] = None
        elif change == "role_binding":
            context["meet_role_assignment"]["snapshot_digest"] = "f" * 64
        patch = (
            {"worker_execution_context": context} if change != "scope" else {"assigned_agent_url": "http://other:8091"}
        )
        assert not compare_and_set_local_task_status(
            task.id, "in_progress", expected_statuses={"in_progress"}, force=True, **patch
        )
        candidate = f.tasks.get_by_id(task.id)
        for field, value in patch.items():
            setattr(candidate, field, value)
        with pytest.raises(ValueError, match="^meet_dialog_principal_immutable$"):
            get_repository_registry().task_repo.save(candidate)
        assert f.tasks.get_by_id(task.id).worker_execution_context == task.worker_execution_context
        assert (
            f.f.authority.current(task.id, scope.lease_id, scope.runtime_id).machine_principal
            == scope.machine_principal
        )


@pytest.mark.parametrize("kind", ["worker", "service", "foreign_owner", "foreign_project", "foreign_tenant"])
def test_principal_receipt_requires_exact_current_owner_scope(app, tmp_path, kind):
    from agent.database import engine

    with app.app_context():
        f, task, _, _ = start_principal(engine, tmp_path)
        changes = (
            {"roles": frozenset({kind})}
            if kind in {"worker", "service"}
            else {
                {"foreign_owner": "subject_id", "foreign_project": "project_id", "foreign_tenant": "tenant_id"}[
                    kind
                ]: "foreign"
            }
        )
        with pytest.raises(MeetError):
            MeetDialogPrincipalReceipts(f.f.authority, f.tasks, f.service.issuer.issuer).inspect(
                replace(f.principal, **changes), "project", task.id
            )


def test_enabled_new_organization_identity_needs_a_real_eligible_role_before_dispatch(app):
    from agent.database import engine

    with app.app_context():
        seed_parent(engine)
        f = system()
        f.tasks.organization_principals = True
        with Session(engine) as session:
            row = session.get(OrganizationRoleAssignmentDB, "meet-test-assignment")
            row.lifecycle = "suspended"
            session.add(row)
            session.commit()
        with pytest.raises(MeetError):
            f.service.start(f.principal, "project", f.payload, parent="meet-test-parent")
        f.issuer.issue_dialog.assert_not_called()
        f.worker.start_dialog.assert_not_called()
