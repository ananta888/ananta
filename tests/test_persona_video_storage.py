"""Private immutable clip storage with real Registry-issued test-only identities.

The tiny MP4 header is a structural test double, not a decoded/live clip. The
reserved run is not reported completed and cannot authorize production use.
"""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.models.persona_assets import PersonaAssetAdmission
from agent.models.persona_media import MediaAssetRef
from agent.models.persona_video_assets import PersonaVideoAsset, PersonaVideoInspectionBinding
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.services.artifact_store import ArtifactStore
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from agent.services.persona_video_storage import PersonaVideoStorage
from tests.test_persona_video_inspection import clip


@pytest.fixture
def video_storage(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'video-identities.db'}")
    HubSourceEvidenceIdentityDB.__table__.create(engine)
    HubRunEvidenceIdentityDB.__table__.create(engine)
    registry = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(engine))
    value = clip()
    source_ids = tuple(
        registry.register_source(
            tenant_id="tenant",
            project_id="project",
            origin_type=kind,
            origin_digest=digest,
            content_digest=digest,
            policy_digest="a" * 64,
            evidence_scope="test",
            synthetic=True,
        ).source_id
        for kind, digest in (("persona_video", value.source_sha256), ("license_document", "c" * 64))
    )
    run = registry.reserve_run(
        tenant_id="tenant",
        project_id="project",
        task_id="structural-test-task",
        assignment_id="structural-test-assignment",
        dispatch_lease_id="structural-test-lease",
        repository_revision="1" * 40,
        input_digest=value.source_sha256,
        execution_profile_digest="a" * 64,
        environment_digest="b" * 64,
        source_ids=source_ids,
        evidence_scope="test",
        synthetic=True,
        idempotency_key="structural-clip",
    )
    scope = dict(tenant_id="tenant", project_id="project", revision=1, classification="test_only")
    asset = PersonaVideoAsset(
        video=MediaAssetRef(**scope, artifact_id="clip", kind="video", sha256=value.video_sha256),
        preview=MediaAssetRef(**scope, artifact_id="clip-preview", kind="image", sha256=value.preview_sha256),
        admission=PersonaAssetAdmission(
            tenant_id="tenant",
            project_id="project",
            source_sha256=value.source_sha256,
            origin_kind="generated",
            origin_binding=source_ids[0],
            license_binding=source_ids[1],
            policy_binding="test-clip-policy",
            policy_revision=1,
            classification="test_only",
        ),
        inspection=PersonaVideoInspectionBinding(
            task_id=run.task_id,
            lease_id=run.dispatch_lease_id,
            run_id=run.run_id,
            assignment_id=run.assignment_id,
            run_binding_digest=run.binding_digest,
        ),
        frames=value.frames,
        video_size=len(value.video),
        preview_size=len(value.preview),
    )
    yield PersonaVideoStorage(ArtifactStore(tmp_path / "private")), asset, value
    engine.dispose()


def test_exact_mp4_and_png_names_are_immutable_and_roundtrip(video_storage):
    storage, asset, value = video_storage
    checkpoint = Mock()
    paths = storage.write(asset, value, checkpoint=checkpoint)
    assert checkpoint.call_count == 5
    assert set(paths) == {"clip", "clip-preview"}
    assert paths["clip"].endswith("/clip/v0001__clip.mp4")
    assert paths["clip-preview"].endswith("/clip-preview/v0001__preview.png")
    assert storage.read(asset, preview=False, checkpoint=Mock()) == value.video
    assert storage.read(asset, preview=True, checkpoint=Mock()) == value.preview
    assert storage.write(asset, value, checkpoint=Mock()) == paths
    assert "storage_path" not in asset.model_dump_json()


@pytest.mark.parametrize("phase", range(5))
def test_storage_stops_on_each_revocation_checkpoint(video_storage, phase):
    storage, asset, value = video_storage
    checkpoint = Mock(side_effect=[None] * phase + [PermissionError("revoked")])
    with pytest.raises(PermissionError):
        storage.write(asset, value, checkpoint=checkpoint)
    assert (storage.store.base_dir / "clip" / "v0001__clip.mp4").exists() == (phase >= 2)
    assert (storage.store.base_dir / "clip-preview" / "v0001__preview.png").exists() == (phase >= 4)


@pytest.mark.parametrize(
    "field,value",
    [("frames", 11), ("source_sha256", "0" * 64), ("video_sha256", "0" * 64), ("preview_sha256", "0" * 64)],
)
def test_inspection_mismatch_never_writes_any_file(video_storage, field, value):
    storage, asset, inspected = video_storage
    store = Mock()
    storage.store = store
    with pytest.raises(ValueError):
        storage.write(asset, replace(inspected, **{field: value}), checkpoint=Mock())
    store.store_immutable_bytes.assert_not_called()


def test_read_checks_authority_after_loading_and_validates_store_result(video_storage):
    storage, asset, value = video_storage
    storage.write(asset, value, checkpoint=Mock())
    with pytest.raises(PermissionError):
        storage.read(asset, preview=False, checkpoint=Mock(side_effect=[None, PermissionError("revoked")]))
    storage.store = Mock(load_immutable_bytes=Mock(return_value=b"wrong bytes"))
    with pytest.raises(ValueError, match="storage_mismatch"):
        storage.read(asset, preview=False, checkpoint=Mock())


@pytest.mark.parametrize("flag", ["false", 0, None])
def test_preview_selector_never_coerces_to_a_different_media_kind(video_storage, flag):
    storage, asset, _ = video_storage
    storage.store = Mock()
    with pytest.raises(ValueError, match="preview_flag"):
        storage.read(asset, preview=flag, checkpoint=Mock())
    storage.store.load_immutable_bytes.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "image"),
        ("tenant_id", "foreign"),
        ("project_id", "foreign"),
        ("revision", 2),
        ("classification", "production"),
        ("artifact_id", "clip-preview"),
    ],
)
def test_asset_references_are_closed_and_exactly_scoped(video_storage, field, value):
    _, asset, _ = video_storage
    payload = asset.model_dump(mode="json")
    payload["video"][field] = value
    with pytest.raises(ValueError):
        PersonaVideoAsset.model_validate(payload)


@pytest.mark.parametrize(
    "change",
    ["frames", "source", "duplicate_source", "run_missing", "unknown_field", "profile", "generated_production"],
)
def test_asset_metadata_requires_complete_structural_bindings(video_storage, change):
    _, asset, _ = video_storage
    payload = asset.model_dump(mode="json")
    if change == "frames":
        payload["frames"] = True
    elif change == "source":
        payload["admission"]["origin_binding"] = "not-issued"
    elif change == "duplicate_source":
        payload["admission"]["license_binding"] = payload["admission"]["origin_binding"]
    elif change == "run_missing":
        payload["inspection"].pop("run_id")
    elif change == "unknown_field":
        payload["public_url"] = "https://example.invalid/secret"
    elif change == "profile":
        payload["profile"] = "unbounded"
    else:
        for target in ("video", "preview", "admission"):
            payload[target]["classification"] = "production"
    with pytest.raises(ValueError):
        PersonaVideoAsset.model_validate(payload)
