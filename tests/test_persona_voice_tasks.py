"""Actual Hub tasks/Registry with an in-process worker transport, not release evidence."""

import base64
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.models.persona_asset_policy import PersonaSourcePin, PersonaVoicePolicy
from agent.repositories.persona_voice_policies import create_voice_policy_repository
from agent.services.persona_inspection_leases import HubPersonaInspectionLeases
from agent.services.persona_inspection_task_state import HubPersonaTaskState
from agent.services.persona_inspection_tasks import HubPersonaInspectionReceipts, HubPersonaInspectionTasks
from agent.services.persona_voice_inspection import PersonaVoiceInspectionFormat
from agent.services.persona_voice_policy_service import create_voice_policy_service
from agent.services.source_control_access_policy import HubSourcePrincipal
from agent.services.task_runtime_service import compare_and_set_local_task_status
from ananta_contracts.meet_voice_catalog import DEFAULT_VOICE_ID
from ananta_contracts.persona_image import validate_assignment as validate_image_assignment
from ananta_contracts.persona_inspection_wire import VOICE_WIRE
from ananta_contracts.persona_voice import MEDIA_TYPE, inspect_voice_descriptor, voice_descriptor
from tests.test_persona_inspection_tasks import runtime as runtime
from worker.meet_media.persona_inspection_executor import PersonaInspectionExecutor
from worker.meet_media.persona_voice_inspector import PersonaVoiceInspector

pytestmark = pytest.mark.timeout(45)


@pytest.fixture
def voice_task(request, tmp_path):
    base = request.getfixturevalue("runtime")
    content = voice_descriptor(DEFAULT_VOICE_ID)
    inspected = inspect_voice_descriptor(content)

    def register(kind, digest):
        identity = base.registry.register_source(
            tenant_id="tenant",
            project_id="project",
            origin_type=kind,
            origin_digest=digest,
            content_digest=digest,
            policy_digest="a" * 64,
            evidence_scope="test",
            synthetic=True,
        )
        return PersonaSourcePin(source_id=identity.source_id, binding_digest=identity.binding_digest)

    state, format = HubPersonaTaskState(kind="voice"), PersonaVoiceInspectionFormat()
    receipts = HubPersonaInspectionReceipts(state=state, registry=base.registry, format=format)
    policies = create_voice_policy_repository(base.engine)
    policies.initialize()
    policy = create_voice_policy_service(
        access=Mock(), policies=policies, sources=base.registry, inspection_receipts=receipts
    )
    principal = HubSourcePrincipal("actor", "tenant", "project", frozenset({"user"}))
    permission = PersonaVoicePolicy(
        media_kind="voice",
        tenant_id="tenant",
        project_id="project",
        policy_binding="voice-policy",
        revision=1,
        source=register("persona_voice", inspected.source_sha256),
        license=register("license_document", "c" * 64),
        consent=register("media_consent", "d" * 64),
        origin_kind="licensed_pack",
        personal_likeness=True,
        classification="test_only",
        subjects=("actor",),
        purposes=("inspect", "store", "preview", "publish"),
        expires_at_ms=int(time.time() * 1000) + 120000,
    )
    policy.install(principal, permission, expected_revision=0)
    admission = policy.admit(
        principal,
        "project",
        inspected.source_sha256,
        origin_binding=permission.source.source_id,
        license_binding=permission.license.source_id,
        consent_binding=permission.consent.source_id,
    )
    leases = HubPersonaInspectionLeases(state=state, policy=policy, registry=base.registry, kind="voice")
    executor = PersonaInspectionExecutor(
        tmp_path / "voice-replay.sqlite",
        wire=VOICE_WIRE,
        inspector=PersonaVoiceInspector,
        guard_factory=lambda assignment: SimpleNamespace(require=lambda: leases.require(assignment)),
    )
    seen, worker = [], Mock()

    def execute(assignment, payload, mime):
        task = state.get(assignment["task_id"])
        run = base.repository.get_run(tenant_id="tenant", project_id="project", run_id=assignment["run_id"])
        assert task.task_kind == "persona_voice_inspection" and task.status == "in_progress"
        assert run.state == "reserved" and run.synthetic and run.evidence_scope == "test"
        with pytest.raises(ValueError):
            validate_image_assignment(assignment, time.time())
        seen.append(assignment)
        result = executor.execute(
            {"assignment": assignment, "content": base64.b64encode(payload).decode(), "media_type": mime}
        )
        assert (result["task_id"], result["lease_id"]) == (assignment["task_id"], assignment["lease_id"])
        return VOICE_WIRE.decode_inspection(result["voice"], assignment["source_sha256"])

    worker.execute.side_effect = execute
    tasks = HubPersonaInspectionTasks(
        policy=policy,
        worker=worker,
        state=state,
        registry=base.registry,
        format=format,
        repository_revision="1" * 40,
        execution_profile_digest="a" * 64,
        environment_digest="b" * 64,
    )
    return SimpleNamespace(
        base=base,
        state=state,
        tasks=tasks,
        worker=worker,
        policy=policy,
        receipts=receipts,
        leases=leases,
        principal=principal,
        permission=permission,
        admission=admission,
        content=content,
        inspected=inspected,
        seen=seen,
        executor=executor,
    )


def execute(case):
    return case.tasks.execute(case.principal, case.admission, case.content, MEDIA_TYPE)


def test_real_task_and_pre_reserved_run_match_completed_descriptor(voice_task):
    result = execute(voice_task)
    voice_task.receipts.require_completed(voice_task.principal, voice_task.admission, result)
    task = voice_task.state.get(result.task_id)
    assert task.status == "completed" and set(task.worker_execution_context) == {"persona_voice"}
    assert "voice=" not in repr(result)
    assert DEFAULT_VOICE_ID not in task.model_dump_json()
    run = voice_task.base.repository.get_run(tenant_id="tenant", project_id="project", run_id=result.run_id)
    assert run.state == "succeeded" and run.synthetic and run.evidence_scope == "test"
    with pytest.raises(PermissionError, match="lease_revoked"):
        voice_task.leases.require(voice_task.seen[0])


@pytest.mark.parametrize("failure", ["worker", "revoke", "cancelled", "mutate"])
def test_worker_failure_revocation_cancellation_and_mutation_never_complete(voice_task, failure):
    original = voice_task.worker.execute.side_effect

    def worker(assignment, content, mime):
        value = original(assignment, content, mime)
        if failure == "worker":
            raise ValueError("explicit inspection failure")
        if failure == "revoke":
            voice_task.policy.revoke_policy(
                voice_task.principal, "project", voice_task.permission.source.source_id, expected_revision=1
            )
        elif failure == "cancelled":
            assert compare_and_set_local_task_status(
                assignment["task_id"], "cancelled", expected_statuses={"in_progress"}
            )
        elif failure == "mutate":
            return replace(value, voice_id="piper.de_DE.thorsten_emotional.medium.whisper")
        return value

    voice_task.worker.execute.side_effect = worker
    with pytest.raises((ValueError, PermissionError)):
        execute(voice_task)
    assignment = voice_task.seen[0]
    run = voice_task.base.repository.get_run(tenant_id="tenant", project_id="project", run_id=assignment["run_id"])
    assert run.state == "failed"
    assert voice_task.state.get(assignment["task_id"]).status in {"failed", "cancelled"}


def test_worker_replay_and_assignment_broadening_are_denied(voice_task):
    original = voice_task.worker.execute.side_effect

    def worker(assignment, content, mime):
        value = original(assignment, content, mime)
        for field in ("tenant_id", "project_id", "owner_subject", "lease_id", "source_sha256", "deadline"):
            changed = assignment[field] + 1 if field == "deadline" else "foreign"
            with pytest.raises(PermissionError):
                voice_task.leases.require(assignment | {field: changed})
        with pytest.raises(ValueError, match="replayed"):
            voice_task.executor.execute(
                {"assignment": assignment, "content": base64.b64encode(content).decode(), "media_type": mime}
            )
        return value

    voice_task.worker.execute.side_effect = worker
    execute(voice_task)


@pytest.mark.parametrize("field", ["lease_id", "assignment_id", "run_id", "run_binding_digest"])
def test_completed_result_cannot_replace_reserved_identity(voice_task, field):
    result = execute(voice_task)
    with pytest.raises(ValueError, match="receipt_mismatch"):
        voice_task.receipts.require_completed(
            voice_task.principal, voice_task.admission, replace(result, **{field: "unknown"})
        )


def test_missing_voice_policy_port_prevents_dispatch(voice_task):
    voice_task.tasks.policy = SimpleNamespace(require_current=Mock())
    with pytest.raises(PermissionError, match="voice_policy_kind_required"):
        execute(voice_task)
    voice_task.worker.execute.assert_not_called()


def test_live_callback_cannot_substitute_an_image_policy(voice_task):
    from agent.services.persona_policy_domains import PersonaImagePolicyDomain

    original = voice_task.worker.execute.side_effect

    def worker(assignment, content, mime):
        value = original(assignment, content, mime)
        domain = voice_task.policy.domain
        voice_task.policy.domain = PersonaImagePolicyDomain()
        try:
            with pytest.raises(PermissionError, match="media_kind_mismatch"):
                voice_task.leases.require(assignment)
        finally:
            voice_task.policy.domain = domain
        return value

    voice_task.worker.execute.side_effect = worker
    execute(voice_task)
