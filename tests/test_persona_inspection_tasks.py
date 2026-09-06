"""Real Hub tasks/Registry identities with an explicit synthetic worker double."""

import hashlib
import io
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image
from sqlalchemy import create_engine, select, update

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.models.persona_assets import PersonaAssetAdmission, PersonaImageAsset
from agent.models.persona_media import MediaAssetRef
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from agent.services.persona_inspection_task_state import HubPersonaTaskState
from agent.services.persona_inspection_tasks import HubPersonaInspectionReceipts, HubPersonaInspectionTasks
from agent.services.task_runtime_service import compare_and_set_local_task_status
from worker.meet_media.persona_image import sanitize_image

pytestmark = pytest.mark.timeout(45)


@pytest.fixture
def runtime(app, tmp_path):
    from sqlmodel import Session

    from agent.database import engine as task_engine
    from agent.db_models import ProjectDB, ProjectMembershipDB

    # Real scoped Tasks require a real parent project on every SQL connection,
    # including connections opened by the threaded Hub lease callback.
    with Session(task_engine) as session:
        session.merge(
            ProjectDB(
                tenant_id="tenant", project_id="project", name="Synthetic persona test", created_by_subject_id="actor"
            )
        )
        session.commit()
        session.merge(ProjectMembershipDB(tenant_id="tenant", project_id="project", subject_id="actor", role="owner"))
        session.commit()
    engine = create_engine(f"sqlite:///{tmp_path / 'identities.db'}")
    HubSourceEvidenceIdentityDB.__table__.create(engine)
    HubRunEvidenceIdentityDB.__table__.create(engine)
    repository = SqlEvidenceIdentityRepository(engine)
    registry = HubEvidenceRegistryService(repository)
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    content = buffer.getvalue()
    image = sanitize_image(content, "image/png")

    def register(kind, digest):
        return registry.register_source(
            tenant_id="tenant",
            project_id="project",
            origin_type=kind,
            origin_digest=digest,
            content_digest=digest,
            policy_digest="a" * 64,
            evidence_scope="test",
            synthetic=True,
        ).source_id

    admission = PersonaAssetAdmission(
        tenant_id="tenant",
        project_id="project",
        source_sha256=hashlib.sha256(content).hexdigest(),
        origin_kind="generated",
        origin_binding=register("persona_image", image.source_sha256),
        license_binding=register("license_document", "c" * 64),
        policy_binding="synthetic-policy",
        policy_revision=1,
        classification="test_only",
    )
    state, policy, worker = HubPersonaTaskState(), Mock(), Mock()

    def execute(assignment, payload, media_type):
        task = state.get(assignment["task_id"])
        assert task.status == "in_progress" and task.task_kind == "persona_image_inspection"
        run = repository.get_run(tenant_id="tenant", project_id="project", run_id=assignment["run_id"])
        assert run.state == "reserved" and run.synthetic and run.evidence_scope == "test"
        assert assignment["evidence"]["run_id"] == run.run_id
        assert payload == content and media_type == "image/png"
        return image

    worker.execute.side_effect = execute
    tasks = HubPersonaInspectionTasks(
        policy=policy,
        worker=worker,
        state=state,
        registry=registry,
        repository_revision="1" * 40,
        execution_profile_digest="a" * 64,
        environment_digest="b" * 64,
    )
    principal = SimpleNamespace(tenant_id="tenant", subject_id="actor")
    with app.app_context():
        yield SimpleNamespace(
            tasks=tasks,
            state=state,
            policy=policy,
            worker=worker,
            admission=admission,
            registry=registry,
            repository=repository,
            engine=engine,
            principal=principal,
            content=content,
            image=image,
            receipts=HubPersonaInspectionReceipts(state=state, registry=registry),
        )
    engine.dispose()


def execute(runtime):
    return runtime.tasks.execute(runtime.principal, runtime.admission, runtime.content, "image/png")


def test_real_task_and_pre_reserved_run_match_only_the_exact_completed_result(runtime):
    result = execute(runtime)
    runtime.receipts.require_completed(runtime.principal, runtime.admission, result)
    task = runtime.state.get(result.task_id)
    assert task.status == "completed" and task.worker_execution_context["persona_image"]["owner_subject"] == "actor"
    run = runtime.repository.get_run(tenant_id="tenant", project_id="project", run_id=result.run_id)
    assert run.state == "succeeded" and run.synthetic and run.evidence_scope == "test"
    stored = json.dumps(task.model_dump(), default=str)
    assert "content" not in task.worker_execution_context["persona_image"]
    assert repr(runtime.content) not in stored
    assert "image=" not in repr(result)


@pytest.mark.parametrize("failure", ["worker", "hash", "policy", "cancelled"])
def test_failure_or_revocation_never_produces_an_acceptable_inspection_receipt(runtime, failure):
    original = runtime.worker.execute.side_effect

    def worker(assignment, content, media_type):
        image = original(assignment, content, media_type)
        if failure == "worker":
            raise ValueError("synthetic_worker_failure")
        if failure == "hash":
            return replace(image, image_sha256="f" * 64)
        if failure == "policy":
            runtime.policy.require_current.side_effect = PermissionError("revoked")
        if failure == "cancelled":
            assert compare_and_set_local_task_status(
                assignment["task_id"], "cancelled", expected_statuses={"in_progress"}
            )
        return image

    runtime.worker.execute.side_effect = worker
    with pytest.raises((ValueError, PermissionError)):
        execute(runtime)
    with runtime.engine.connect() as connection:
        run = connection.execute(select(HubRunEvidenceIdentityDB.__table__)).mappings().one()
    assert run["state"] == "failed"
    assert runtime.state.get(run["task_id"]).status in {"failed", "cancelled"}


@pytest.mark.parametrize("field", ["lease_id", "assignment_id", "run_id", "run_binding_digest"])
def test_caller_cannot_replace_registered_execution_identity(runtime, field):
    result = execute(runtime)
    with pytest.raises(ValueError, match="receipt_mismatch"):
        runtime.receipts.require_completed(runtime.principal, runtime.admission, replace(result, **{field: "unknown"}))


def test_registry_detects_mutated_completed_run_binding(runtime):
    result = execute(runtime)
    with runtime.engine.begin() as connection:
        connection.execute(update(HubRunEvidenceIdentityDB).values(environment_digest="f" * 64))
    with pytest.raises(ValueError, match="binding_mismatch"):
        runtime.receipts.require_completed(runtime.principal, runtime.admission, result)


def test_projection_failure_closes_reserved_run_without_worker_execution(runtime):
    runtime.registry.assignment_projection = Mock(side_effect=ValueError("synthetic_projection_failed"))
    with pytest.raises(ValueError, match="projection_failed"):
        execute(runtime)
    runtime.worker.execute.assert_not_called()
    with runtime.engine.connect() as connection:
        assert connection.execute(select(HubRunEvidenceIdentityDB.__table__.c.state)).scalar_one() == "failed"


def stored_asset(runtime, result):
    common = dict(tenant_id="tenant", project_id="project", revision=1, kind="image", classification="test_only")
    admission = runtime.admission.model_dump()
    admission.pop("tenant_id")
    admission.pop("project_id")
    admission.pop("classification")
    return PersonaImageAsset(
        **admission,
        image=MediaAssetRef(**common, artifact_id="image", sha256=result.image.image_sha256),
        preview=MediaAssetRef(**common, artifact_id="preview", sha256=result.image.preview_sha256),
        inspection_task_id=result.task_id,
        inspection_lease_id=result.lease_id,
        inspection_run_id=result.run_id,
        inspection_assignment_id=result.assignment_id,
        inspection_run_binding_digest=result.run_binding_digest,
        image_size=len(result.image.png),
        preview_size=len(result.image.preview),
    )


def test_stored_asset_receipt_survives_task_archival_but_not_registry_mutation(runtime):
    result = execute(runtime)
    asset = stored_asset(runtime, result)
    runtime.state.get = Mock(side_effect=AssertionError("immutable receipt does not require an active task"))
    runtime.receipts.require_asset(runtime.principal, asset)
    with runtime.engine.begin() as connection:
        connection.execute(update(HubRunEvidenceIdentityDB).values(result_digest="f" * 64))
    with pytest.raises(ValueError, match="binding_mismatch"):
        runtime.receipts.require_asset(runtime.principal, asset)


def test_legacy_asset_without_registered_inspection_run_is_not_silently_promoted(runtime):
    asset = stored_asset(runtime, execute(runtime)).model_dump()
    asset.update(inspection_run_id=None, inspection_assignment_id=None, inspection_run_binding_digest=None)
    with pytest.raises(ValueError, match="unverified"):
        runtime.receipts.require_asset(runtime.principal, PersonaImageAsset.model_validate(asset))


def test_different_actor_cannot_adopt_the_completed_inspection(runtime):
    result = execute(runtime)
    with pytest.raises(ValueError, match="receipt_mismatch"):
        runtime.receipts.require_completed(
            SimpleNamespace(tenant_id="tenant", subject_id="other"), runtime.admission, result
        )


def test_live_lease_rechecks_policy_and_cannot_survive_completion(runtime):
    from agent.services.persona_inspection_leases import HubPersonaInspectionLeases

    leases = HubPersonaInspectionLeases(state=runtime.state, policy=runtime.policy, registry=runtime.registry)
    original = runtime.worker.execute.side_effect
    assignments = []

    def worker(assignment, content, media_type):
        assignments.append(assignment)
        leases.require(assignment)
        with pytest.raises(PermissionError):
            leases.require(assignment | {"lease_id": "stale"})
        runtime.policy.require_current.side_effect = PermissionError("policy revoked")
        with pytest.raises(PermissionError, match="policy revoked"):
            leases.require(assignment)
        runtime.policy.require_current.side_effect = None
        return original(assignment, content, media_type)

    runtime.worker.execute.side_effect = worker
    execute(runtime)
    with pytest.raises(PermissionError, match="lease_revoked"):
        leases.require(assignments[0])


def test_real_http_worker_and_signed_hub_callback_complete_a_reserved_task(runtime, app, tmp_path, monkeypatch):
    import threading

    from werkzeug.serving import WSGIRequestHandler, make_server

    from agent.services.persona_image_transport import HttpPersonaImageWorker
    from agent.services.persona_inspection_leases import HubPersonaInspectionLeases
    from worker.meet_media.persona_executor import PersonaImageExecutor
    from worker.meet_media.persona_lease import PersonaLeaseGuard
    from worker.meet_media.persona_server import create_server

    class SilentHandler(WSGIRequestHandler):
        def log(self, *_args, **_kwargs):
            pass

    key = b"synthetic-in-memory-persona-key-0001"
    app.config["ROLE"] = "hub"
    app.extensions["persona_image_worker_key"] = key
    app.extensions["persona_image_leases"] = HubPersonaInspectionLeases(
        state=runtime.state, policy=runtime.policy, registry=runtime.registry
    )
    hub = make_server("127.0.0.1", 0, app, threaded=True, request_handler=SilentHandler)
    executor = PersonaImageExecutor(
        tmp_path / "replay.sqlite",
        guard_factory=lambda assignment: PersonaLeaseGuard(
            f"http://127.0.0.1:{hub.server_port}/api/persona-media/v1/internal/image-lease", key, assignment
        ),
    )
    errors = []
    original_execute = executor.execute

    def diagnostic_execute(payload):
        try:
            return original_execute(payload)
        except Exception as error:
            errors.append(str(error))
            raise

    executor.execute = diagnostic_execute
    worker = create_server(("127.0.0.1", 0), key, executor)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (hub, worker)]
    for thread in threads:
        thread.start()
    # Explicit network test seam: production's DNS policy still rejects loopback.
    monkeypatch.setattr("agent.services.persona_image_transport.pin_private_container_address", lambda *_: "127.0.0.1")
    runtime.tasks.worker = HttpPersonaImageWorker(f"http://worker.test:{worker.server_port}/v1/persona-images", key)
    try:
        try:
            result = execute(runtime)
        except ValueError:
            pytest.fail(f"HTTP inspection failed: {errors}")
        runtime.receipts.require_completed(runtime.principal, runtime.admission, result)
        assert result.image.png == runtime.image.png
        assert result.image.preview == runtime.image.preview
        _assert_real_policy_catalog_lifecycle(runtime, app, tmp_path)
    finally:
        for server in (worker, hub):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=3)
            assert not thread.is_alive()


def _assert_real_policy_catalog_lifecycle(runtime, app, tmp_path):
    import time

    from agent.db_models import ArtifactDB, ArtifactVersionDB
    from agent.models.persona_asset_policy import PersonaImagePolicy, PersonaSourcePin
    from agent.repositories.persona_asset_policy import SqlPersonaImagePolicies
    from agent.repositories.persona_assets import SqlPersonaAssets
    from agent.services.artifact_store import ArtifactStore
    from agent.services.persona_asset_policy_service import PersonaAssetPolicyService
    from agent.services.persona_asset_service import PersonaAssetService
    from agent.services.persona_asset_storage import PersonaAssetStorage
    from agent.services.source_control_access_policy import HubSourcePrincipal

    for model in (ArtifactDB, ArtifactVersionDB):
        model.__table__.create(runtime.engine)
    policies, catalog = SqlPersonaImagePolicies(runtime.engine), SqlPersonaAssets(runtime.engine)
    policies.initialize()
    catalog.initialize()
    policy = PersonaAssetPolicyService(
        access=app.extensions["project_access_authority"],
        policies=policies,
        sources=runtime.registry,
        inspection_receipts=runtime.receipts,
    )
    principal = HubSourcePrincipal("actor", "tenant", "project", frozenset({"user"}))

    def pin(source_id):
        identity = runtime.repository.get_source(tenant_id="tenant", project_id="project", source_id=source_id)
        return PersonaSourcePin(source_id=identity.source_id, binding_digest=identity.binding_digest)

    managed = PersonaImagePolicy(
        tenant_id="tenant",
        project_id="project",
        policy_binding="explicit-synthetic-image-policy",
        revision=1,
        source=pin(runtime.admission.origin_binding),
        license=pin(runtime.admission.license_binding),
        origin_kind="generated",
        personal_likeness=False,
        classification="test_only",
        subjects=("actor",),
        purposes=("inspect", "store", "preview"),
        expires_at_ms=int((time.time() + 60) * 1000),
    )
    policy.install(principal, managed, expected_revision=0)
    runtime.tasks.policy = app.extensions["persona_image_leases"].policy = policy
    service = PersonaAssetService(
        policy=policy,
        tasks=runtime.tasks,
        catalog=catalog,
        storage=PersonaAssetStorage(ArtifactStore(tmp_path / "images")),
    )
    asset = service.admit_image(
        principal,
        "project",
        content=runtime.content,
        media_type="image/png",
        origin_binding=managed.source.source_id,
        license_binding=managed.license.source_id,
    )
    assert service.read_image(principal, "project", asset.image.artifact_id) == runtime.image.preview
    with pytest.raises(PermissionError, match="use_denied"):
        service.read_image(principal, "project", asset.image.artifact_id, purpose="publish")
    policy.revoke_policy(principal, "project", managed.source.source_id, expected_revision=1)
    with pytest.raises(ValueError, match="unavailable"):
        service.read_image(principal, "project", asset.image.artifact_id)
    assert service.revoke(principal, "project", asset.image.artifact_id, expected_revision=2) == 3
