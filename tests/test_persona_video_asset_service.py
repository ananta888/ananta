"""Real private catalog/policy/Hub receipts with explicit structural video decoder."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select, update

from agent.db_models import ArtifactDB, ArtifactVersionDB
from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from agent.repositories.persona_video_assets import assets, create_video_asset_catalog, events
from agent.services.artifact_store import ArtifactStore
from agent.services.artifact_visibility_policy import is_artifact_visible_on_generic_surfaces
from agent.services.persona_policy_domains import PersonaImagePolicyDomain
from agent.services.persona_video_asset_service import PersonaVideoAssetService
from agent.services.persona_video_storage import PersonaVideoStorage
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_video_tasks import video_task as video_task


@pytest.fixture
def video_assets(request, tmp_path):
    case = request.getfixturevalue("video_task")
    for model in (ArtifactDB, ArtifactVersionDB):
        model.__table__.create(case.base.engine)
    catalog = create_video_asset_catalog(case.base.engine)
    catalog.initialize()
    storage = PersonaVideoStorage(ArtifactStore(tmp_path / "private-video"))
    service = PersonaVideoAssetService(policy=case.policy, tasks=case.tasks, catalog=catalog, storage=storage)
    return SimpleNamespace(case=case, service=service, catalog=catalog, storage=storage)


def admit(fixture):
    case = fixture.case
    return fixture.service.admit_video(
        case.principal,
        "project",
        content=case.content,
        media_type="video/mp4",
        origin_binding=case.permission.source.source_id,
        license_binding=case.permission.license.source_id,
    )


def test_video_admission_stores_only_completed_receipt_and_revokes_both_parts(video_assets):
    fixture = video_assets
    case, service = fixture.case, fixture.service
    asset = admit(fixture)
    assert fixture.catalog.get_active("tenant", "project", asset.video.artifact_id) == (asset, 2)
    case.receipts.require_asset(case.principal, asset)
    assert service.read_video(case.principal, "project", asset.video.artifact_id) == case.inspected.preview
    assert (
        service.read_video(case.principal, "project", asset.video.artifact_id, purpose="publish")
        == case.inspected.video
    )
    with case.base.engine.connect() as connection:
        rows = list(connection.execute(select(ArtifactDB.__table__)).mappings())
        assert len(rows) == 2 and all(not is_artifact_visible_on_generic_surfaces(row) for row in rows)
    with pytest.raises(ValueError, match="conflict"):
        service.revoke(case.principal, "project", asset.video.artifact_id, expected_revision=1)
    assert service.revoke(case.principal, "project", asset.video.artifact_id, expected_revision=2) == 3
    with pytest.raises(ValueError):
        service.read_video(case.principal, "project", asset.video.artifact_id)
    with case.base.engine.connect() as connection:
        assert set(connection.execute(select(ArtifactDB.__table__.c.status)).scalars()) == {"revoked"}
        assert list(connection.execute(select(events.c.state).order_by(events.c.revision)).scalars()) == [
            "pending",
            "active",
            "revoked",
        ]


def test_preview_permission_never_becomes_clip_publication_permission(video_assets):
    fixture = video_assets
    case = fixture.case
    permission = case.permission.model_copy(update={"revision": 2, "purposes": ("inspect", "store", "preview")})
    case.policy.install(case.principal, permission, expected_revision=1)
    asset = admit(fixture)
    assert fixture.service.read_video(case.principal, "project", asset.video.artifact_id) == case.inspected.preview
    with pytest.raises(PermissionError, match="use_denied"):
        fixture.service.read_video(case.principal, "project", asset.video.artifact_id, purpose="publish")


def test_changed_registry_receipt_prevents_even_pending_video_reservation(video_assets):
    fixture = video_assets
    original = fixture.case.tasks.execute

    def execute(*args, **kwargs):
        result = original(*args, **kwargs)
        with fixture.case.base.engine.begin() as connection:
            connection.execute(update(HubRunEvidenceIdentityDB).values(result_digest="f" * 64))
        return result

    fixture.case.tasks.execute = execute
    with pytest.raises(ValueError):
        admit(fixture)
    with fixture.case.base.engine.connect() as connection:
        assert not list(connection.execute(select(assets)))
        assert not list(connection.execute(select(ArtifactDB.__table__)))


@pytest.mark.parametrize("stage", ["after_first_file", "after_activation"])
def test_interrupted_video_store_retains_durable_revoked_bundle(video_assets, stage):
    fixture = video_assets
    case = fixture.case
    if stage == "after_first_file":
        original = case.policy.require_current
        calls = 0

        def current(principal, admission, purpose):
            nonlocal calls
            original(principal, admission, purpose)
            if purpose == "store":
                calls += 1
                if calls == 4:
                    raise PermissionError("explicit revoked-policy boundary double")

        case.policy.require_current = current
    else:
        original_transition = fixture.catalog.transition

        def transition(*args, **kwargs):
            revision = original_transition(*args, **kwargs)
            if kwargs["state"] == "active":
                case.policy.revoke_policy(
                    case.principal, "project", case.permission.source.source_id, expected_revision=1
                )
            return revision

        fixture.catalog.transition = transition
    with pytest.raises((ValueError, PermissionError)):
        admit(fixture)
    with case.base.engine.connect() as connection:
        row = connection.execute(select(assets)).mappings().one()
        assert row["state"] == "revoked"
        assert set(connection.execute(select(ArtifactDB.__table__.c.status)).scalars()) == {"revoked"}
    with pytest.raises(ValueError):
        fixture.catalog.get_active("tenant", "project", row["artifact_id"])
    stored = list(fixture.storage.store.base_dir.rglob("v0001__*"))
    assert len(stored) == (1 if stage == "after_first_file" else 2)


def test_revocation_after_loading_bytes_prevents_video_release(video_assets):
    fixture = video_assets
    case = fixture.case
    asset = admit(fixture)
    original = fixture.storage.store.load_immutable_bytes

    def load(**kwargs):
        content = original(**kwargs)
        case.policy.revoke_policy(case.principal, "project", case.permission.source.source_id, expected_revision=1)
        return content

    fixture.storage.store.load_immutable_bytes = load
    with pytest.raises((PermissionError, ValueError)):
        fixture.service.read_video(case.principal, "project", asset.video.artifact_id, purpose="publish")


@pytest.mark.parametrize("purpose", ["preview", "publish", "download"])
def test_denied_lookup_never_queries_video_catalog(video_assets, purpose):
    fixture = video_assets
    fixture.service.catalog = Mock()
    fixture.case.policy.access.require.side_effect = PermissionError("membership revoked")
    with pytest.raises((ValueError, PermissionError)):
        fixture.service.read_video(fixture.case.principal, "project", "unknown", purpose=purpose)
    fixture.service.catalog.get_active.assert_not_called()


def test_image_policy_cannot_back_video_admission(video_assets):
    fixture = video_assets
    fixture.case.policy.domain = PersonaImagePolicyDomain()
    with pytest.raises(PermissionError, match="media_kind_mismatch"):
        admit(fixture)
    fixture.case.worker.execute.assert_not_called()


@pytest.mark.parametrize("media_type", ["image/png", "video/webm"])
def test_video_service_rejects_other_media_before_task_creation(video_assets, media_type):
    fixture = video_assets
    case = fixture.case
    with pytest.raises(ValueError, match="input_invalid"):
        fixture.service.admit_video(
            case.principal,
            "project",
            content=case.content,
            media_type=media_type,
            origin_binding=case.permission.source.source_id,
            license_binding=case.permission.license.source_id,
        )
    case.worker.execute.assert_not_called()
