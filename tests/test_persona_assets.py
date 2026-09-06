"""Synthetic artifact catalog, immutable storage and revocation tests."""

import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image
from sqlalchemy import create_engine, select, update

from agent.db_models import ArtifactDB, ArtifactVersionDB
from agent.models.persona_assets import PersonaAssetAdmission, PersonaImageAsset
from agent.models.persona_media import MediaAssetRef
from agent.repositories.persona_assets import SqlPersonaAssets, assets, events
from agent.services.artifact_store import ArtifactStore
from agent.services.artifact_visibility_policy import is_artifact_visible_on_generic_surfaces
from agent.services.persona_asset_service import PersonaAssetService, PersonaInspectionResult
from agent.services.persona_asset_storage import PersonaAssetStorage
from worker.meet_media.persona_image import sanitize_image

pytestmark = pytest.mark.timeout(30)


@pytest.fixture
def setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'assets.db'}")
    ArtifactDB.__table__.create(engine)
    ArtifactVersionDB.__table__.create(engine)
    repository = SqlPersonaAssets(engine)
    repository.initialize()
    storage = PersonaAssetStorage(ArtifactStore(tmp_path / "private"))
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(output, format="PNG")
    inspected = sanitize_image(output.getvalue(), "image/png")
    common = dict(tenant_id="tenant", project_id="project", revision=1, kind="image", classification="test_only")
    asset = PersonaImageAsset(
        image=MediaAssetRef(**common, artifact_id="image", sha256=inspected.image_sha256),
        preview=MediaAssetRef(**common, artifact_id="preview", sha256=inspected.preview_sha256),
        source_sha256=inspected.source_sha256,
        origin_kind="generated",
        origin_binding="synthetic-origin",
        license_binding="synthetic-license",
        policy_binding="synthetic-policy",
        policy_revision=1,
        inspection_task_id="synthetic-task",
        inspection_lease_id="synthetic-lease",
        image_size=len(inspected.png),
        preview_size=len(inspected.preview),
    )
    yield repository, storage, asset, inspected
    engine.dispose()


def test_pending_and_active_artifacts_never_escape_generic_surfaces(setup):
    repository, storage, asset, inspected = setup
    repository.reserve(asset, actor="synthetic")
    with pytest.raises(ValueError, match="not_active"):
        repository.get_active("tenant", "project", "image")
    with repository.engine.connect() as connection:
        for row in connection.execute(select(ArtifactDB.__table__)).mappings():
            assert not is_artifact_visible_on_generic_surfaces(row)
    paths = storage.write(asset, inspected, checkpoint=Mock())
    assert (
        repository.transition(
            "tenant", "project", "image", actor="synthetic", expected_revision=1, state="active", stored_paths=paths
        )
        == 2
    )
    assert repository.get_active("tenant", "project", "image") == (asset, 2)
    assert storage.read(asset, preview=True, checkpoint=Mock()) == inspected.preview
    with repository.engine.connect() as connection:
        for row in connection.execute(select(ArtifactDB.__table__)).mappings():
            assert row["status"] == "stored" and not is_artifact_visible_on_generic_surfaces(row)
    assert "storage_path" not in asset.model_dump_json()


def test_revoked_pending_reservation_cannot_be_activated_late(setup):
    repository, storage, asset, inspected = setup
    repository.reserve(asset, actor="synthetic")
    paths = storage.write(asset, inspected, checkpoint=Mock())
    repository.transition("tenant", "project", "image", actor="synthetic", expected_revision=1, state="revoked")
    with pytest.raises(ValueError, match="conflict"):
        repository.transition(
            "tenant", "project", "image", actor="synthetic", expected_revision=1, state="active", stored_paths=paths
        )
    with pytest.raises(ValueError, match="not_active"):
        repository.get_active("tenant", "project", "image")
    with repository.engine.connect() as connection:
        assert set(connection.execute(select(ArtifactDB.__table__.c.status)).scalars()) == {"revoked"}


def test_existing_artifact_ids_cannot_be_overwritten_by_another_project(setup):
    repository, _, asset, _ = setup
    repository.reserve(asset, actor="synthetic")
    foreign = asset.model_dump(mode="json")
    foreign["image"]["project_id"] = foreign["preview"]["project_id"] = "foreign"
    with pytest.raises(ValueError, match="conflict"):
        repository.reserve(PersonaImageAsset.model_validate(foreign), actor="foreign")
    with repository.engine.connect() as connection:
        assert list(connection.execute(select(assets.c.project_id)).scalars()) == ["project"]


@pytest.mark.parametrize("tenant,project", [("other", "project"), ("tenant", "other")])
def test_catalog_does_not_resolve_foreign_scope(setup, tenant, project):
    repository, _, asset, _ = setup
    repository.reserve(asset, actor="synthetic")
    with pytest.raises(ValueError, match="unavailable"):
        repository.get_active(tenant, project, "image")


def test_catalog_payload_mutation_is_rejected(setup):
    repository, _, asset, _ = setup
    repository.reserve(asset, actor="synthetic")
    with repository.engine.begin() as connection:
        connection.execute(update(assets).values(payload="{}"))
    with pytest.raises(ValueError, match="integrity_failed"):
        repository.get_active("tenant", "project", "image")


def test_storage_is_immutable_and_checks_authority_between_files(setup):
    _, storage, asset, inspected = setup
    checkpoint = Mock(side_effect=[None, PermissionError("revoked")])
    with pytest.raises(PermissionError):
        storage.write(asset, inspected, checkpoint=checkpoint)
    assert (storage.store.base_dir / "image" / "v0001__image.png").exists()
    assert not (storage.store.base_dir / "preview").exists()
    storage.write(asset, inspected, checkpoint=Mock())
    assert storage.read(asset, preview=False, checkpoint=Mock()) == inspected.png


def application(setup):
    repository, storage, asset, inspected = setup
    principal = SimpleNamespace(tenant_id="tenant", subject_id="synthetic-user")
    policy, tasks = Mock(), Mock()
    policy.admit.return_value = PersonaAssetAdmission(
        tenant_id="tenant",
        project_id="project",
        source_sha256=asset.source_sha256,
        origin_kind="generated",
        origin_binding="synthetic-origin",
        license_binding="synthetic-license",
        policy_binding="synthetic-policy",
        policy_revision=1,
        classification="test_only",
    )
    tasks.execute.return_value = PersonaInspectionResult("synthetic-task", "synthetic-lease", inspected)
    service = PersonaAssetService(policy=policy, tasks=tasks, catalog=repository, storage=storage)
    source = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(source, format="PNG")
    kwargs = dict(
        content=source.getvalue(),
        media_type="image/png",
        origin_binding="synthetic-origin",
        license_binding="synthetic-license",
    )
    return service, principal, kwargs


def test_hub_service_delegates_inspection_and_never_converts_preview_rights_to_publish(setup):
    service, principal, kwargs = application(setup)
    asset = service.admit_image(principal, "project", **kwargs)
    assert asset.image.classification == "test_only" and asset.inspection_lease_id == "synthetic-lease"
    service.tasks.execute.assert_called_once()
    service.policy.require_completed_inspection.assert_called_once()
    assert service.read_image(principal, "project", asset.image.artifact_id) == setup[3].preview
    service.policy.require_asset.assert_called_with(principal, asset, "preview")
    service.policy.require_asset.side_effect = PermissionError("publication denied")
    with pytest.raises(PermissionError):
        service.read_image(principal, "project", asset.image.artifact_id, purpose="publish")
    assert service.revoke(principal, "project", asset.image.artifact_id, expected_revision=2) == 3
    with pytest.raises(ValueError, match="not_active"):
        service.catalog.get_active("tenant", "project", asset.image.artifact_id)
    with service.catalog.engine.connect() as connection:
        rows = list(connection.execute(select(events).order_by(events.c.revision)).mappings())
    assert [(row["revision"], row["state"], row["actor"]) for row in rows] == [
        (1, "pending", "synthetic-user"),
        (2, "active", "synthetic-user"),
        (3, "revoked", "synthetic-user"),
    ]


@pytest.mark.parametrize("stage", ["admit", "require_completed_inspection"])
def test_missing_policy_or_completed_hub_task_cannot_create_any_asset(setup, stage):
    service, principal, kwargs = application(setup)
    getattr(service.policy, stage).side_effect = PermissionError("unverified")
    with pytest.raises(PermissionError):
        service.admit_image(principal, "project", **kwargs)
    with service.catalog.engine.connect() as connection:
        assert not list(connection.execute(select(assets)))
    if stage == "admit":
        service.tasks.execute.assert_not_called()


@pytest.mark.parametrize("store_checkpoint", [3, 6])
def test_revocation_during_storage_or_after_activation_leaves_durable_tombstone(setup, store_checkpoint):
    service, principal, kwargs = application(setup)
    count = 0

    def require_current(_, __, purpose):
        nonlocal count
        if purpose == "store":
            count += 1
            if count == store_checkpoint:
                raise PermissionError("revoked")

    service.policy.require_current.side_effect = require_current
    with pytest.raises(PermissionError):
        service.admit_image(principal, "project", **kwargs)
    with service.catalog.engine.connect() as connection:
        row = connection.execute(select(assets)).mappings().one()
        assert row["state"] == "revoked"
        assert set(connection.execute(select(ArtifactDB.__table__.c.status)).scalars()) == {"revoked"}
    with pytest.raises(ValueError, match="not_active"):
        service.catalog.get_active("tenant", "project", row["artifact_id"])


def test_denied_lookup_cannot_probe_foreign_catalog_state(setup):
    service, principal, _ = application(setup)
    service.catalog = Mock()
    service.policy.require_lookup.side_effect = PermissionError("denied")
    with pytest.raises(PermissionError):
        service.read_image(principal, "foreign", "unknown")
    service.catalog.get_active.assert_not_called()


def test_consent_revoked_after_read_does_not_release_loaded_bytes(setup):
    service, principal, kwargs = application(setup)
    asset = service.admit_image(principal, "project", **kwargs)
    service.policy.require_asset.side_effect = [None, PermissionError("revoked")]
    with pytest.raises(PermissionError):
        service.read_image(principal, "project", asset.image.artifact_id)
