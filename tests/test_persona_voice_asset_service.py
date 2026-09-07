"""Real private voice storage and Hub receipts; test-only worker transport remains explicit."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import select, update

from agent.db_models import ArtifactDB, ArtifactVersionDB
from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from agent.repositories.persona_voice_assets import assets, create_voice_asset_catalog
from agent.services.artifact_store import ArtifactStore
from agent.services.artifact_visibility_policy import is_artifact_visible_on_generic_surfaces
from agent.services.persona_policy_domains import PersonaImagePolicyDomain
from agent.services.persona_voice_asset_service import create_voice_asset_service
from agent.services.persona_voice_storage import PersonaVoiceStorage
from ananta_contracts.persona_voice import MEDIA_TYPE
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_voice_tasks import voice_task as voice_task

pytestmark = pytest.mark.timeout(45)


@pytest.fixture
def voice_assets(request, tmp_path):
    case = request.getfixturevalue("voice_task")
    for model in (ArtifactDB, ArtifactVersionDB):
        model.__table__.create(case.base.engine)
    catalog = create_voice_asset_catalog(case.base.engine)
    catalog.initialize()
    storage = PersonaVoiceStorage(ArtifactStore(tmp_path / "private-voice"))
    service = create_voice_asset_service(policy=case.policy, tasks=case.tasks, catalog=catalog, storage=storage)
    return SimpleNamespace(case=case, service=service, catalog=catalog, storage=storage)


def admit(fixture):
    case = fixture.case
    return fixture.service.admit(
        case.principal,
        "project",
        content=case.content,
        media_type=MEDIA_TYPE,
        origin_binding=case.permission.source.source_id,
        license_binding=case.permission.license.source_id,
        consent_binding=case.permission.consent.source_id,
    )


def test_completed_voice_receipt_stores_only_descriptor_and_rechecks_revocation(voice_assets):
    fixture, case = voice_assets, voice_assets.case
    asset = admit(fixture)
    identifier = asset.voice.artifact_id
    assert fixture.catalog.get_active("tenant", "project", identifier) == (asset, 2)
    case.receipts.require_asset(case.principal, asset)
    for purpose in ("preview", "publish"):
        assert fixture.service.read(case.principal, "project", identifier, purpose=purpose) == case.content
    with case.base.engine.connect() as connection:
        row = connection.execute(select(ArtifactDB.__table__)).mappings().one()
        assert row["latest_filename"] == "voice.json" and not is_artifact_visible_on_generic_surfaces(row)
    assert len(list(fixture.storage.store.base_dir.rglob("v0001__*"))) == 1
    assert fixture.service.revoke(case.principal, "project", identifier, expected_revision=2) == 3
    with pytest.raises(ValueError, match="not_active"):
        fixture.service.read(case.principal, "project", identifier)


def test_preview_permission_is_not_voice_publication_permission(voice_assets):
    case = voice_assets.case
    policy = case.permission.model_copy(update={"revision": 2, "purposes": ("inspect", "store", "preview")})
    case.policy.install(case.principal, policy, expected_revision=1)
    asset = admit(voice_assets)
    assert voice_assets.service.read(case.principal, "project", asset.voice.artifact_id) == case.content
    with pytest.raises(PermissionError, match="use_denied"):
        voice_assets.service.read(case.principal, "project", asset.voice.artifact_id, purpose="publish")


@pytest.mark.parametrize("phase", ["before_store", "after_load"])
def test_changed_registry_receipt_or_policy_prevents_release(voice_assets, phase):
    fixture, case = voice_assets, voice_assets.case
    if phase == "before_store":
        original = case.tasks.execute

        def execute(*args, **kwargs):
            result = original(*args, **kwargs)
            with case.base.engine.begin() as connection:
                connection.execute(update(HubRunEvidenceIdentityDB).values(result_digest="f" * 64))
            return result

        case.tasks.execute = execute
        with pytest.raises(ValueError):
            admit(fixture)
        with case.base.engine.connect() as connection:
            assert not list(connection.execute(select(assets)))
    else:
        asset = admit(fixture)
        original = fixture.storage.store.load_immutable_bytes

        def load(**kwargs):
            content = original(**kwargs)
            case.policy.revoke_policy(case.principal, "project", case.permission.source.source_id, expected_revision=1)
            return content

        fixture.storage.store.load_immutable_bytes = load
        with pytest.raises((ValueError, PermissionError)):
            fixture.service.read(case.principal, "project", asset.voice.artifact_id)


def test_revocation_after_activation_leaves_durable_retired_descriptor(voice_assets):
    fixture, case = voice_assets, voice_assets.case
    original = fixture.catalog.transition

    def transition(*args, **kwargs):
        revision = original(*args, **kwargs)
        if kwargs["state"] == "active":
            case.policy.revoke_policy(case.principal, "project", case.permission.source.source_id, expected_revision=1)
        return revision

    fixture.catalog.transition = transition
    with pytest.raises(ValueError):
        admit(fixture)
    with case.base.engine.connect() as connection:
        assert connection.execute(select(assets.c.state)).scalar_one() == "revoked"
        assert connection.execute(select(ArtifactDB.__table__.c.status)).scalar_one() == "revoked"


def test_image_or_missing_kind_policy_never_reaches_voice_worker(voice_assets):
    fixture, case = voice_assets, voice_assets.case
    case.policy.domain = PersonaImagePolicyDomain()
    with pytest.raises(PermissionError, match="media_kind_mismatch"):
        admit(fixture)
    fixture.service.policy = SimpleNamespace(require_current=Mock())
    with pytest.raises(PermissionError, match="voice_policy_kind_required"):
        admit(fixture)
    case.worker.execute.assert_not_called()
