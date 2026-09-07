"""Private MP4/preview rows share proven SQL fencing, not image asset identities."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select, update

from agent.db_models import ArtifactDB, ArtifactVersionDB
from agent.repositories.persona_assets import SqlPersonaAssets
from agent.repositories.persona_video_assets import assets, create_video_asset_catalog, events
from agent.services.artifact_visibility_policy import is_artifact_visible_on_generic_surfaces
from tests.test_persona_video_storage import video_storage as video_storage


@pytest.fixture
def video_catalog(request, tmp_path):
    storage, asset, value = request.getfixturevalue("video_storage")
    engine = create_engine(f"sqlite:///{tmp_path / 'video-catalog.db'}")
    ArtifactDB.__table__.create(engine)
    ArtifactVersionDB.__table__.create(engine)
    catalog = create_video_asset_catalog(engine)
    catalog.initialize()
    yield catalog, storage, asset, value
    engine.dispose()


def test_video_and_preview_are_private_before_and_after_activation(video_catalog):
    catalog, storage, asset, value = video_catalog
    catalog.reserve(asset, actor="actor")
    with pytest.raises(ValueError, match="not_active"):
        catalog.get_active("tenant", "project", "clip")
    for state in ("pending", "stored"):
        with catalog.engine.connect() as connection:
            rows = connection.execute(select(ArtifactDB.__table__)).mappings().all()
            assert len(rows) == 2 and all(row["status"] == state for row in rows)
            assert all(not is_artifact_visible_on_generic_surfaces(row) for row in rows)
            assert {(row["latest_filename"], row["latest_media_type"]) for row in rows} == {
                ("clip.mp4", "video/mp4"),
                ("preview.png", "image/png"),
            }
        if state == "pending":
            with catalog.storage_guard("tenant", "project", "clip", expected_revision=1, state="pending"):
                paths = storage.write(asset, value, checkpoint=Mock())
            assert (
                catalog.transition(
                    "tenant", "project", "clip", expected_revision=1, state="active", actor="actor", stored_paths=paths
                )
                == 2
            )
    assert catalog.get_active("tenant", "project", "clip") == (asset, 2)
    assert catalog.scan_active_ids("tenant", "project", after="", limit=2) == ("clip",)
    with catalog.engine.connect() as connection:
        history = connection.execute(select(events).order_by(events.c.revision)).mappings().all()
        assert [(row["revision"], row["state"], row["actor"]) for row in history] == [
            (1, "pending", "actor"),
            (2, "active", "actor"),
        ]


def test_image_catalog_cannot_parse_or_resolve_video_assets(video_catalog):
    catalog, _, asset, _ = video_catalog
    images = SqlPersonaAssets(catalog.engine)
    images.initialize()
    with pytest.raises(ValueError):
        images.reserve(asset, actor="actor")
    catalog.reserve(asset, actor="actor")
    with pytest.raises(ValueError, match="unavailable"):
        images.get_active("tenant", "project", "clip")
    with pytest.raises(ValueError, match="unavailable"):
        catalog.get_active("tenant", "foreign", "clip")


def test_revocation_consumes_revision_and_prevents_late_activation(video_catalog):
    catalog, storage, asset, value = video_catalog
    catalog.reserve(asset, actor="actor")
    paths = storage.write(asset, value, checkpoint=Mock())
    assert catalog.transition("tenant", "project", "clip", expected_revision=1, state="revoked", actor="actor") == 2
    with pytest.raises(ValueError, match="conflict"):
        catalog.transition(
            "tenant", "project", "clip", expected_revision=1, state="active", actor="actor", stored_paths=paths
        )
    assert catalog.get_retired("tenant", "project", "clip") == (asset, 2, "revoked")
    assert catalog.scan_active_ids("tenant", "project", after="", limit=2) == ()


def test_catalog_write_fence_excludes_concurrent_revocation(video_catalog):
    catalog, _, asset, _ = video_catalog
    catalog.reserve(asset, actor="actor")
    attempted, revoked = Event(), Event()

    def revoke():
        attempted.set()
        catalog.transition("tenant", "project", "clip", expected_revision=1, state="revoked", actor="actor")
        revoked.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with catalog.storage_guard("tenant", "project", "clip", expected_revision=1, state="pending"):
            future = executor.submit(revoke)
            assert attempted.wait(2) and not revoked.wait(0.05)
        future.result(timeout=3)
    assert revoked.is_set()
    with pytest.raises(ValueError, match="guard_conflict"):
        with catalog.storage_guard("tenant", "project", "clip", expected_revision=1, state="pending"):
            pytest.fail("revoked writer entered")


def test_stored_metadata_corruption_fails_closed(video_catalog):
    catalog, _, asset, _ = video_catalog
    catalog.reserve(asset, actor="actor")
    with catalog.engine.begin() as connection:
        connection.execute(update(assets).values(payload="{}"))
    with pytest.raises(ValueError, match="integrity_failed"):
        catalog.get_active("tenant", "project", "clip")


@pytest.mark.parametrize("model", [ArtifactDB, ArtifactVersionDB], ids=["artifact", "version"])
def test_missing_artifact_member_rolls_back_activation_and_audit(video_catalog, model):
    catalog, storage, asset, value = video_catalog
    catalog.reserve(asset, actor="actor")
    paths = storage.write(asset, value, checkpoint=Mock())
    with catalog.engine.begin() as connection:
        key = model.__table__.c.id if model is ArtifactDB else model.__table__.c.artifact_id
        connection.execute(model.__table__.delete().where(key == "clip-preview"))
    with pytest.raises(ValueError, match="catalog_incomplete"):
        catalog.transition(
            "tenant", "project", "clip", expected_revision=1, state="active", actor="actor", stored_paths=paths
        )
    with catalog.engine.connect() as connection:
        assert connection.execute(select(assets.c.state)).scalar_one() == "pending"
        assert list(connection.execute(select(events.c.state)).scalars()) == ["pending"]
