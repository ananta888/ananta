"""One-part metadata catalog with Registry-reserved test identities, not completed runs."""

import pytest
from sqlalchemy import select

from agent.db_models import ArtifactDB, ArtifactVersionDB
from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from agent.models.persona_media import MediaAssetRef
from agent.models.persona_voice_assets import PersonaVoiceAsset, PersonaVoiceInspectionBinding
from agent.repositories.persona_assets import SqlPersonaAssets
from agent.repositories.persona_video_assets import create_video_asset_catalog
from agent.repositories.persona_voice_assets import assets, create_voice_asset_catalog, events
from agent.services.artifact_visibility_policy import is_artifact_visible_on_generic_surfaces
from tests.test_persona_asset_policy import configured as configured
from tests.test_persona_voice_policy import admit
from tests.test_persona_voice_policy import voice_policy as voice_policy


@pytest.fixture
def voice_catalog(request, tmp_path):
    service, principal, policy, _, _, engine, value = request.getfixturevalue("voice_policy")
    for model in (ArtifactDB, ArtifactVersionDB, HubRunEvidenceIdentityDB):
        model.__table__.create(engine)
    service.install(principal, policy, expected_revision=0)
    admission = admit(service, principal, policy, value)
    run = service.sources.reserve_run(
        tenant_id="tenant",
        project_id="project",
        task_id="structural-voice-task",
        assignment_id="structural-voice-assignment",
        dispatch_lease_id="structural-voice-lease",
        repository_revision="1" * 40,
        input_digest=value.source_sha256,
        execution_profile_digest="a" * 64,
        environment_digest="b" * 64,
        source_ids=(policy.source.source_id, policy.license.source_id, policy.consent.source_id),
        evidence_scope="test",
        synthetic=True,
        idempotency_key="structural-voice",
    )
    asset = PersonaVoiceAsset(
        voice=MediaAssetRef(
            tenant_id="tenant",
            project_id="project",
            artifact_id="voice-descriptor",
            revision=1,
            sha256=value.source_sha256,
            kind="voice",
            classification="test_only",
        ),
        voice_id=value.voice_id,
        descriptor_size=len(value.descriptor),
        admission=admission,
        inspection=PersonaVoiceInspectionBinding(
            task_id=run.task_id,
            lease_id=run.dispatch_lease_id,
            run_id=run.run_id,
            assignment_id=run.assignment_id,
            run_binding_digest=run.binding_digest,
        ),
    )
    catalog = create_voice_asset_catalog(engine)
    catalog.initialize()
    # An absolute test-only storage projection, not a claim that bytes are stored.
    return catalog, asset, {asset.voice.artifact_id: str(tmp_path / "voice.json")}


def activate(catalog, paths, revision=1):
    return catalog.transition(
        "tenant",
        "project",
        "voice-descriptor",
        expected_revision=revision,
        state="active",
        actor="owner",
        stored_paths=paths,
    )


def test_exact_single_private_member_activates_and_revokes_atomically(voice_catalog):
    catalog, asset, paths = voice_catalog
    catalog.reserve(asset, actor="owner")
    with pytest.raises(ValueError, match="not_active"):
        catalog.get_active("tenant", "project", "voice-descriptor")
    assert activate(catalog, paths) == 2
    assert catalog.get_active("tenant", "project", "voice-descriptor") == (asset, 2)
    with catalog.engine.connect() as connection:
        row = connection.execute(select(ArtifactDB.__table__)).mappings().one()
        assert row["status"] == "stored" and row["latest_filename"] == "voice.json"
        assert not is_artifact_visible_on_generic_surfaces(row)
        assert list(connection.execute(select(events.c.state).order_by(events.c.revision)).scalars()) == [
            "pending",
            "active",
        ]
    assert (
        catalog.transition("tenant", "project", "voice-descriptor", expected_revision=2, state="revoked", actor="owner")
        == 3
    )
    assert catalog.get_retired("tenant", "project", "voice-descriptor") == (asset, 3, "revoked")
    with pytest.raises(ValueError, match="conflict"):
        activate(catalog, paths)


@pytest.mark.parametrize("table", [ArtifactDB.__table__, ArtifactVersionDB.__table__], ids=["artifact", "version"])
def test_missing_single_member_rolls_back_activation_and_audit(voice_catalog, table):
    catalog, asset, paths = voice_catalog
    catalog.reserve(asset, actor="owner")
    with catalog.engine.begin() as connection:
        connection.execute(table.delete())
    with pytest.raises(ValueError, match="catalog_incomplete"):
        activate(catalog, paths)
    with catalog.engine.connect() as connection:
        assert connection.execute(select(assets.c.state)).scalar_one() == "pending"
        assert list(connection.execute(select(events.c.state)).scalars()) == ["pending"]


@pytest.mark.parametrize("paths", [{}, {"preview": "/tmp/extra"}, {"voice-descriptor": "relative"}])
def test_voice_catalog_requires_exact_storage_projection(voice_catalog, paths):
    catalog, asset, _ = voice_catalog
    catalog.reserve(asset, actor="owner")
    with pytest.raises(ValueError, match="storage_required"):
        activate(catalog, paths)


def test_voice_rows_never_resolve_through_image_or_video_catalogs(voice_catalog):
    catalog, asset, paths = voice_catalog
    catalog.reserve(asset, actor="owner")
    activate(catalog, paths)
    for other in (SqlPersonaAssets(catalog.engine), create_video_asset_catalog(catalog.engine)):
        other.initialize()
        with pytest.raises(ValueError):
            other.reserve(asset, actor="owner")
        with pytest.raises(ValueError, match="unavailable"):
            other.get_active("tenant", "project", "voice-descriptor")
    with pytest.raises(ValueError, match="unavailable"):
        catalog.get_active("tenant", "foreign", "voice-descriptor")


@pytest.mark.parametrize(
    "path,value",
    [
        (("voice", "kind"), "image"),
        (("voice", "revision"), True),
        (("voice", "revision"), 2),
        (("voice", "tenant_id"), "foreign"),
        (("voice", "sha256"), "0" * 64),
        (("admission", "classification"), "production"),
        (("admission", "source_sha256"), "0" * 64),
        (("admission", "origin_kind"), "upload"),
        (("inspection", "run_id"), None),
        (("voice_id",), "unknown"),
        (("descriptor_size",), True),
        (("model_path",), "/tmp/caller-model"),
    ],
)
def test_voice_metadata_cannot_mutate_scope_pins_receipt_or_model(voice_catalog, path, value):
    _, asset, _ = voice_catalog
    payload = asset.model_dump(mode="json")
    target = payload if len(path) == 1 else payload[path[0]]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        PersonaVoiceAsset.model_validate(payload)


def test_empty_or_duplicate_format_parts_cannot_bypass_completeness(voice_catalog):
    catalog, asset, paths = voice_catalog
    catalog.reserve(asset, actor="owner")
    parts = catalog.format.parts(asset)
    for replacement in ((), parts + parts):
        catalog.format.parts = lambda _: replacement
        with pytest.raises(ValueError, match="parts_invalid"):
            activate(catalog, paths)
