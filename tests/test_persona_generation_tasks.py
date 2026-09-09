"""Real Hub queue/Registry, explicit headless policy and synthetic Worker port."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select, update

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.models.persona_asset_policy import PersonaSourcePin
from agent.models.persona_generation import PersonaGenerationRequest
from agent.services.persona_generated_source import PersonaGeneratedSourceAdmission
from agent.services.persona_generation_leases import HubPersonaGenerationLeases
from agent.services.persona_generation_policy import PersonaGenerationPolicy
from agent.services.persona_generation_service import HubPersonaGenerationService
from agent.services.persona_generation_state import HubPersonaGenerationState
from agent.services.source_control_access_policy import HubSourcePrincipal
from agent.services.task_runtime_service import compare_and_set_local_task_status
from tests import test_persona_inspection_tasks as inspection_cases
from worker.meet_media.persona_generation_frames import render

runtime = inspection_cases.runtime
pytestmark = pytest.mark.timeout(45)


@pytest.fixture
def generation_task(runtime):
    base = runtime
    principal = HubSourcePrincipal("actor", "tenant", "project", frozenset({"user"}))
    license = base.repository.get_source(
        tenant_id="tenant",
        project_id="project",
        source_id=base.admission.license_binding,
    )
    request = PersonaGenerationRequest.model_validate(
        {
            "recipe": {"profile": "procedural-avatar-v1", "media_kind": "image", "palette": "indigo"},
            "license": {"source_id": license.source_id, "binding_digest": license.binding_digest},
            "classification": "test_only",
        }
    )
    access = Mock()
    policy = PersonaGenerationPolicy(access=access, registry=base.registry)
    state = HubPersonaGenerationState()
    leases = HubPersonaGenerationLeases(state=state, policy=policy, registry=base.registry)
    worker, seen = Mock(), []

    def execute(assignment, recipe):
        leases.require(assignment)
        task = state.get(assignment["task_id"])
        assert task.status == "in_progress" and task.task_kind == "persona_media_generation"
        run = base.repository.get_run(tenant_id="tenant", project_id="project", run_id=assignment["run_id"])
        assert run.state == "reserved" and run.synthetic
        assert set(assignment["evidence"]["source_ids"]) == {
            request.license.source_id,
            task.verification_spec["recipe_source"]["source_id"],
        }
        seen.append(assignment)
        return render(recipe)

    worker.execute.side_effect = execute
    service = HubPersonaGenerationService(
        policy=policy,
        registry=base.registry,
        state=state,
        leases=leases,
        worker=worker,
        admission=PersonaGeneratedSourceAdmission(access=access, registry=base.registry),
        repository_revision="1" * 40,
        execution_profile_digest="a" * 64,
        environment_digest="b" * 64,
    )
    return SimpleNamespace(
        base=base,
        principal=principal,
        request=request,
        access=access,
        policy=policy,
        state=state,
        leases=leases,
        worker=worker,
        seen=seen,
        service=service,
    )


def generate(case):
    return case.service.generate(case.principal, "project", case.request)


def runs(case):
    with case.base.engine.connect() as connection:
        return connection.execute(select(HubRunEvidenceIdentityDB.__table__)).mappings().all()


def test_native_task_and_pre_reserved_run_produce_only_exact_generated_source(generation_task):
    c = generation_task
    result = generate(c)
    assert result.content.startswith(b"\x89PNG") and "content=" not in repr(result)
    assert result.output.classification == "test_only" and result.output.personal_likeness is False
    task = c.state.get(result.run_pin.task_id)
    run = runs(c)[0]
    assert task.status == "completed" and run["state"] == "succeeded"
    assert task.worker_execution_context["persona_generation"]["result_digest"] == result.output.digest()
    assert run["result_digest"] == result.output.digest()
    source = c.base.registry.require_source_identity(
        tenant_id="tenant",
        project_id="project",
        source_id=result.source.source_id,
        expected_binding_digest=result.source.binding_digest,
    )
    assert source.content_digest == result.output.content_sha256 and source.synthetic
    assert "content" not in task.model_dump_json()
    with pytest.raises(PermissionError):
        c.leases.require(c.seen[0])


@pytest.mark.parametrize("failure", ["worker", "bytes", "cancelled", "license", "deadline"])
def test_worker_failure_cancel_or_revocation_never_completes_or_returns_source(generation_task, failure):
    c = generation_task
    original = c.worker.execute.side_effect

    def execute(assignment, recipe):
        result = original(assignment, recipe)
        if failure == "worker":
            raise ValueError("synthetic worker failure")
        if failure == "bytes":
            return b"invalid PNG result"
        if failure == "cancelled":
            assert compare_and_set_local_task_status(
                assignment["task_id"], "cancelled", expected_statuses={"in_progress"}
            )
        if failure == "license":
            table = HubSourceEvidenceIdentityDB.__table__
            with c.base.engine.begin() as connection:
                connection.execute(
                    update(table).where(table.c.source_id == c.request.license.source_id).values(state="revoked")
                )
        if failure == "deadline":
            c.leases.clock = lambda: assignment["deadline"]
        return result

    c.worker.execute.side_effect = execute
    with pytest.raises((ValueError, PermissionError)):
        generate(c)
    assert runs(c)[0]["state"] == "failed"
    assert c.state.get(c.seen[0]["task_id"]).status in {"failed", "cancelled"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("lease_id", "old"),
        ("assignment_id", "foreign"),
        ("tenant_id", "foreign"),
        ("owner_subject", "foreign"),
        ("admission_digest", "0" * 64),
        ("source_sha256", "0" * 64),
    ],
)
def test_live_lease_rejects_any_assignment_mutation(generation_task, field, value):
    c = generation_task
    original = c.worker.execute.side_effect

    def execute(assignment, recipe):
        with pytest.raises((ValueError, PermissionError)):
            c.leases.require(assignment | {field: value})
        return original(assignment, recipe)

    c.worker.execute.side_effect = execute
    generate(c)


@pytest.mark.parametrize("role", ["worker", "service"])
def test_machine_principal_cannot_create_generation_task(generation_task, role):
    c = generation_task
    c.principal = replace(c.principal, roles=frozenset({role}))
    with pytest.raises(PermissionError):
        generate(c)
    assert not runs(c)
    c.worker.execute.assert_not_called()


def test_no_promotion_from_a_test_license_and_no_implicit_publish(generation_task):
    c = generation_task
    assert c.request.publish is False
    c.request = c.request.model_copy(update={"classification": "synthetic"})
    with pytest.raises(PermissionError, match="not_promotable"):
        generate(c)
    assert not runs(c)


def test_completed_task_race_cannot_replace_current_verification(generation_task, monkeypatch):
    from sqlmodel import Session

    from agent.database import engine
    from agent.db_models import TaskDB

    c = generation_task
    finish = c.state.finish

    def racing_finish(assignment, status, **kwargs):
        if status == "completed":
            with Session(engine) as session:
                task = session.get(TaskDB, assignment["task_id"])
                spec = dict(task.verification_spec)
                spec["persona_generation"] = spec["persona_generation"] | {"publish": True}
                task.verification_spec = spec
                session.add(task)
                session.commit()
        return finish(assignment, status, **kwargs)

    monkeypatch.setattr(c.state, "finish", racing_finish)
    with pytest.raises(ValueError, match="cancelled"):
        generate(c)
    assert runs(c)[0]["state"] == "failed"
    assert c.state.get(c.seen[0]["task_id"]).status == "failed"


@pytest.mark.parametrize(
    "field,value",
    [
        ("repository_revision", "main"),
        ("execution_profile_digest", "unknown"),
        ("environment_digest", ""),
    ],
)
def test_execution_bindings_are_required_before_any_run(generation_task, field, value):
    c = generation_task
    config = dict(
        policy=c.policy,
        registry=c.base.registry,
        state=c.state,
        leases=c.leases,
        worker=c.worker,
        admission=c.service.admission,
        repository_revision="1" * 40,
        execution_profile_digest="a" * 64,
        environment_digest="b" * 64,
    ) | {field: value}
    with pytest.raises(ValueError):
        HubPersonaGenerationService(**config)
    assert not runs(c)


def test_recipe_source_pin_is_registered_not_invented(generation_task):
    c = generation_task
    result = generate(c)
    pin = PersonaSourcePin.model_validate(c.state.get(result.run_pin.task_id).verification_spec["recipe_source"])
    source = c.base.registry.require_source_identity(
        tenant_id="tenant",
        project_id="project",
        source_id=pin.source_id,
        expected_binding_digest=pin.binding_digest,
    )
    assert source.origin_type == "persona_generation_recipe"
    assert source.content_digest == result.run_pin.input_digest
