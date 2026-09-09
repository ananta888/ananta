"""Generation receipt -> real Hub inspection task -> private immutable artifact.

Generation/decoder results are explicit deterministic doubles. This verifies
the admission integration, not a real generator or GPU execution.
"""

import hashlib
import time
from dataclasses import replace
from unittest.mock import Mock

import pytest

from agent.db_models import ArtifactDB, ArtifactVersionDB
from agent.models.persona_asset_policy import PersonaImagePolicy, PersonaVideoPolicy
from agent.models.persona_generated_source import PersonaGeneratedOutput
from agent.repositories.persona_asset_policy import SqlPersonaImagePolicies
from agent.repositories.persona_assets import SqlPersonaAssets
from agent.repositories.persona_video_assets import create_video_asset_catalog
from agent.repositories.persona_video_policies import create_video_policy_repository
from agent.services.artifact_store import ArtifactStore
from agent.services.persona_asset_policy_service import PersonaAssetPolicyService
from agent.services.persona_asset_service import PersonaAssetService
from agent.services.persona_asset_storage import PersonaAssetStorage
from agent.services.persona_generated_source import PersonaGeneratedSourceAdmission
from agent.services.persona_inspection_formats import PersonaImageInspectionFormat, PersonaVideoInspectionFormat
from agent.services.persona_inspection_task_state import HubPersonaTaskState
from agent.services.persona_inspection_tasks import HubPersonaInspectionReceipts, HubPersonaInspectionTasks
from agent.services.persona_policy_domains import PersonaImagePolicyDomain, PersonaVideoPolicyDomain
from agent.services.persona_video_asset_service import PersonaVideoAssetService
from agent.services.persona_video_storage import PersonaVideoStorage
from tests import test_persona_generated_source as generation_cases
from tests import test_persona_inspection_tasks as inspection_cases
from tests.test_persona_video_inspection import clip

generated = generation_cases.generated
runtime = inspection_cases.runtime
pytestmark = pytest.mark.timeout(45)


@pytest.mark.parametrize("kind", ["image", "video"])
def test_generated_source_flows_through_existing_inspection_storage_and_revocation(generated, runtime, tmp_path, kind):
    c = generated
    principal = replace(c.principal, subject_id="actor")
    is_image = kind == "image"
    content = runtime.content if is_image else clip().video
    inspected = runtime.image if is_image else replace(clip(), source_sha256=hashlib.sha256(content).hexdigest())
    output = PersonaGeneratedOutput.model_validate(
        c.output.model_dump()
        | {
            "owner_subject": "actor",
            "media_kind": kind,
            "media_type": "image/png" if is_image else "video/mp4",
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "content_size": len(content),
        }
    )
    run_pin = c.reserve(output)
    c.complete(run_pin, output)
    source = PersonaGeneratedSourceAdmission(access=Mock(), registry=c.registry).admit(
        principal,
        "project",
        output=output,
        run_pin=run_pin,
        content=content,
    )
    state = HubPersonaTaskState(kind=kind)
    format = PersonaImageInspectionFormat() if is_image else PersonaVideoInspectionFormat()
    receipts = HubPersonaInspectionReceipts(state=state, registry=c.registry, format=format)
    policies = SqlPersonaImagePolicies(c.engine) if is_image else create_video_policy_repository(c.engine)
    policies.initialize()
    policy = PersonaAssetPolicyService(
        access=Mock(),
        policies=policies,
        sources=c.registry,
        inspection_receipts=receipts,
        domain=PersonaImagePolicyDomain() if is_image else PersonaVideoPolicyDomain(),
    )
    terms = dict(
        tenant_id="tenant",
        project_id="project",
        policy_binding="explicit-generated-asset-policy",
        revision=1,
        source=source,
        license=output.license,
        consent=output.consent,
        personal_likeness=output.personal_likeness,
        origin_kind="generated",
        classification="test_only",
        subjects=("actor",),
        purposes=("inspect", "store", "preview"),
        expires_at_ms=int(time.time() * 1000) + 120_000,
    )
    permission = PersonaImagePolicy(**terms) if is_image else PersonaVideoPolicy(media_kind="video", **terms)
    policy.install(principal, permission, expected_revision=0)
    worker = Mock()

    def inspect(assignment, supplied, mime):
        task = state.get(assignment["task_id"])
        assert task.task_kind == f"persona_{kind}_inspection" and task.status == "in_progress"
        assert assignment["evidence"]["run_id"] != run_pin.run_id
        assert supplied == content and mime == output.media_type
        return inspected

    worker.execute.side_effect = inspect
    tasks = HubPersonaInspectionTasks(
        policy=policy,
        worker=worker,
        state=state,
        registry=c.registry,
        format=format,
        repository_revision="1" * 40,
        execution_profile_digest="a" * 64,
        environment_digest="b" * 64,
    )
    catalog = SqlPersonaAssets(c.engine) if is_image else create_video_asset_catalog(c.engine)
    ArtifactDB.__table__.create(c.engine)
    ArtifactVersionDB.__table__.create(c.engine)
    catalog.initialize()
    store = ArtifactStore(tmp_path / "private-artifacts")
    assets = (
        PersonaAssetService(policy=policy, tasks=tasks, catalog=catalog, storage=PersonaAssetStorage(store))
        if is_image
        else PersonaVideoAssetService(policy=policy, tasks=tasks, catalog=catalog, storage=PersonaVideoStorage(store))
    )
    asset = getattr(assets, f"admit_{kind}")(
        principal,
        "project",
        content=content,
        media_type=output.media_type,
        origin_binding=source.source_id,
        license_binding=output.license.source_id,
        consent_binding=output.consent.source_id,
    )
    reference = getattr(asset, kind)
    assert reference.classification == "test_only"
    read = getattr(assets, f"read_{kind}")
    assert read(principal, "project", reference.artifact_id) == inspected.preview
    with pytest.raises(PermissionError):
        read(principal, "project", reference.artifact_id, purpose="publish")
    policy.revoke_policy(principal, "project", source.source_id, expected_revision=1)
    with pytest.raises((ValueError, PermissionError)):
        read(principal, "project", reference.artifact_id)
    worker.execute.assert_called_once()
