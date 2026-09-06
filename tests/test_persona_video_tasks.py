"""Real Hub tasks/Registry/video policy, with an explicit structural decoder double."""

import hashlib
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.models.persona_asset_policy import PersonaSourcePin, PersonaVideoPolicy
from agent.models.persona_media import MediaAssetRef
from agent.models.persona_video_assets import PersonaVideoAsset, PersonaVideoInspectionBinding
from agent.repositories.persona_video_policies import create_video_policy_repository
from agent.services.persona_inspection_formats import PersonaVideoInspectionFormat
from agent.services.persona_inspection_leases import HubPersonaInspectionLeases
from agent.services.persona_inspection_task_state import HubPersonaTaskState
from agent.services.persona_inspection_tasks import HubPersonaInspectionReceipts, HubPersonaInspectionTasks
from agent.services.persona_policy_domains import PersonaImagePolicyDomain
from agent.services.persona_video_policy_service import create_video_policy_service
from agent.services.source_control_access_policy import HubSourcePrincipal
from agent.services.task_runtime_service import compare_and_set_local_task_status
from ananta_contracts.persona_image import validate_assignment as validate_image_assignment
from ananta_contracts.persona_video import validate_assignment
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_video_inspection import clip


@pytest.fixture
def video_task(request):
    base = request.getfixturevalue("runtime")
    content = clip().video
    inspected = replace(clip(), source_sha256=hashlib.sha256(content).hexdigest())

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

    state, format = HubPersonaTaskState(kind="video"), PersonaVideoInspectionFormat()
    receipts = HubPersonaInspectionReceipts(state=state, registry=base.registry, format=format)
    policies = create_video_policy_repository(base.engine)
    policies.initialize()
    policy = create_video_policy_service(
        access=Mock(), policies=policies, sources=base.registry, inspection_receipts=receipts
    )
    principal = HubSourcePrincipal("actor", "tenant", "project", frozenset({"user"}))
    permission = PersonaVideoPolicy(
        media_kind="video",
        tenant_id="tenant",
        project_id="project",
        policy_binding="video-policy",
        revision=1,
        source=register("persona_video", inspected.source_sha256),
        license=register("license_document", "c" * 64),
        origin_kind="generated",
        personal_likeness=False,
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
        consent_binding=None,
    )
    leases = HubPersonaInspectionLeases(state=state, policy=policy, registry=base.registry, kind="video")
    seen, worker = [], Mock()

    def execute(assignment, payload, mime):
        assert validate_assignment(assignment, time.time()) == assignment
        with pytest.raises(ValueError):
            validate_image_assignment(assignment, time.time())
        task = state.get(assignment["task_id"])
        run = base.repository.get_run(tenant_id="tenant", project_id="project", run_id=assignment["run_id"])
        assert task.task_kind == "persona_video_inspection" and task.status == "in_progress"
        assert run.state == "reserved" and run.synthetic and run.evidence_scope == "test"
        leases.require(assignment)
        seen.append(assignment)
        assert payload == content and mime == "video/mp4"
        return inspected

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
    )


def execute(case):
    return case.tasks.execute(case.principal, case.admission, case.content, "video/mp4")


def test_video_is_delegated_under_pre_reserved_run_and_exact_current_policy(video_task):
    result = execute(video_task)
    video_task.receipts.require_completed(video_task.principal, video_task.admission, result)
    task = video_task.state.get(result.task_id)
    assert task.status == "completed" and set(task.worker_execution_context) == {"persona_video"}
    assert "video=" not in repr(result)
    assert "ftyp" not in task.model_dump_json()
    with pytest.raises(PermissionError):
        video_task.leases.require(video_task.seen[0])
    run = video_task.base.repository.get_run(tenant_id="tenant", project_id="project", run_id=result.run_id)
    assert run.state == "succeeded" and run.synthetic and run.evidence_scope == "test"


@pytest.mark.parametrize("failure", ["worker", "revoke", "cancelled"])
def test_failed_or_revoked_inspection_never_gets_success_receipt(video_task, failure):
    original = video_task.worker.execute.side_effect

    def worker(assignment, content, mime):
        value = original(assignment, content, mime)
        if failure == "worker":
            raise ValueError("synthetic decoder failure")
        if failure == "revoke":
            video_task.policy.revoke_policy(
                video_task.principal, "project", video_task.permission.source.source_id, expected_revision=1
            )
        else:
            assert compare_and_set_local_task_status(
                assignment["task_id"], "cancelled", expected_statuses={"in_progress"}
            )
        return value

    video_task.worker.execute.side_effect = worker
    with pytest.raises((ValueError, PermissionError)):
        execute(video_task)
    assignment = video_task.seen[0]
    run = video_task.base.repository.get_run(tenant_id="tenant", project_id="project", run_id=assignment["run_id"])
    assert run.state == "failed"
    assert video_task.state.get(assignment["task_id"]).status in {"failed", "cancelled"}


def test_result_frame_mutation_and_cross_kind_receipt_are_rejected(video_task):
    result = execute(video_task)
    with pytest.raises(ValueError, match="receipt_mismatch"):
        video_task.receipts.require_completed(
            video_task.principal, video_task.admission, replace(result, video=replace(result.video, frames=11))
        )
    with pytest.raises(PermissionError, match="kind_mismatch"):
        HubPersonaInspectionLeases(
            state=video_task.state, policy=video_task.policy, registry=video_task.base.registry
        ).require(video_task.seen[0])


def test_asset_receipt_rechecks_run_metadata_and_frame_digest(video_task):
    result = execute(video_task)
    common = dict(tenant_id="tenant", project_id="project", revision=1, classification="test_only")
    asset = PersonaVideoAsset(
        video=MediaAssetRef(**common, kind="video", artifact_id="clip", sha256=result.video.video_sha256),
        preview=MediaAssetRef(**common, kind="image", artifact_id="preview", sha256=result.video.preview_sha256),
        admission=video_task.admission,
        inspection=PersonaVideoInspectionBinding(
            task_id=result.task_id,
            lease_id=result.lease_id,
            run_id=result.run_id,
            assignment_id=result.assignment_id,
            run_binding_digest=result.run_binding_digest,
        ),
        frames=result.video.frames,
        video_size=len(result.video.video),
        preview_size=len(result.video.preview),
    )
    video_task.policy.require_asset(video_task.principal, asset, "publish")
    mutated = PersonaVideoAsset.model_validate(asset.model_dump() | {"frames": 11})
    with pytest.raises(ValueError):
        video_task.policy.require_asset(video_task.principal, mutated, "publish")


@pytest.mark.parametrize("mime", ["image/png", "video/webm"])
def test_video_task_never_delegates_another_media_kind(video_task, mime):
    with pytest.raises(ValueError, match="media_type_invalid"):
        video_task.tasks.execute(video_task.principal, video_task.admission, video_task.content, mime)
    video_task.worker.execute.assert_not_called()


def test_video_task_cannot_use_legacy_or_image_only_policy_port(video_task):
    video_task.tasks.policy = SimpleNamespace(require_current=Mock())
    with pytest.raises(PermissionError, match="policy_kind_required"):
        execute(video_task)
    video_task.tasks.policy = video_task.policy
    video_task.policy.domain = PersonaImagePolicyDomain()
    with pytest.raises(PermissionError, match="media_kind_mismatch"):
        execute(video_task)
    video_task.worker.execute.assert_not_called()


@pytest.mark.parametrize("field", ["tenant_id", "project_id", "owner_subject", "lease_id", "source_sha256", "deadline"])
def test_live_worker_cannot_broaden_or_replace_assignment_projection(video_task, field):
    original = video_task.worker.execute.side_effect

    def worker(assignment, content, mime):
        value = original(assignment, content, mime)
        changed = assignment[field] + 1 if field == "deadline" else "0" * 64 if field == "source_sha256" else "foreign"
        with pytest.raises(PermissionError):
            video_task.leases.require(assignment | {field: changed})
        assert video_task.state.finish(assignment | {"schema": "ananta.persona-image-task.v1"}, "failed") is False
        video_task.leases.require(assignment)
        return value

    video_task.worker.execute.side_effect = worker
    result = execute(video_task)
    video_task.receipts.require_completed(video_task.principal, video_task.admission, result)
