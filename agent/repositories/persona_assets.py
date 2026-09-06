"""Persona catalog state changes; all public access requires a Hub policy service."""

import hashlib
import uuid
from pathlib import Path

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, insert, select, update
from sqlalchemy.exc import IntegrityError

from agent.db_models import ArtifactDB, ArtifactVersionDB
from agent.models.persona_assets import PersonaImageAsset

_metadata = MetaData()
assets = Table(
    "persona_image_assets",
    _metadata,
    Column("tenant_id", String(160), primary_key=True),
    Column("project_id", String(160), primary_key=True),
    Column("artifact_id", String(160), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("state", String(16), nullable=False),
    Column("payload", Text, nullable=False),
    Column("payload_sha256", String(64), nullable=False),
)
events = Table(
    "persona_image_asset_events",
    _metadata,
    Column("tenant_id", String(160), primary_key=True),
    Column("project_id", String(160), primary_key=True),
    Column("artifact_id", String(160), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("actor", String(255), nullable=False),
    Column("state", String(16), nullable=False),
)


def _where(tenant, project, artifact):
    return (assets.c.tenant_id == tenant, assets.c.project_id == project, assets.c.artifact_id == artifact)


def _event(connection, asset, revision, state, actor):
    if (
        not isinstance(actor, str)
        or not 0 < len(actor) <= 255
        or any(ord(char) < 32 or ord(char) == 127 for char in actor)
    ):
        raise ValueError("persona_asset_actor_invalid")
    connection.execute(
        insert(events).values(
            tenant_id=asset.image.tenant_id,
            project_id=asset.image.project_id,
            artifact_id=asset.image.artifact_id,
            revision=revision,
            state=state,
            actor=actor,
        )
    )


class SqlPersonaAssets:
    def __init__(self, engine):
        self.engine = engine

    def initialize(self):
        _metadata.create_all(self.engine)

    def reserve(self, asset: PersonaImageAsset, *, actor: str):
        payload = asset.model_dump_json()
        if len(payload.encode()) > 16_384:
            raise ValueError("persona_asset_metadata_too_large")
        try:
            with self.engine.begin() as connection:
                _event(connection, asset, 1, "pending", actor)
                connection.execute(
                    insert(assets).values(
                        tenant_id=asset.image.tenant_id,
                        project_id=asset.image.project_id,
                        artifact_id=asset.image.artifact_id,
                        revision=1,
                        state="pending",
                        payload=payload,
                        payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
                    )
                )
                for reference, kind, size in (
                    (asset.image, "persona_media_image", asset.image_size),
                    (asset.preview, "persona_media_preview", asset.preview_size),
                ):
                    version_id = str(uuid.uuid4())
                    metadata = {
                        "system_artifact_kind": kind,
                        "tenant_id": reference.tenant_id,
                        "project_id": reference.project_id,
                        "persona_asset_id": asset.image.artifact_id,
                    }
                    connection.execute(
                        insert(ArtifactDB.__table__).values(
                            id=reference.artifact_id,
                            latest_version_id=version_id,
                            latest_sha256=reference.sha256,
                            latest_media_type="image/png",
                            latest_filename="image.png",
                            size_bytes=size,
                            status="pending",
                            created_by=actor,
                            artifact_metadata=metadata,
                        )
                    )
                    connection.execute(
                        insert(ArtifactVersionDB.__table__).values(
                            id=version_id,
                            artifact_id=reference.artifact_id,
                            version_number=1,
                            storage_path="",
                            original_filename="image.png",
                            media_type="image/png",
                            size_bytes=size,
                            sha256=reference.sha256,
                            version_metadata=metadata,
                        )
                    )
        except IntegrityError:
            raise ValueError("persona_asset_reservation_conflict") from None

    def _read(self, connection, tenant, project, artifact):
        row = connection.execute(select(assets).where(*_where(tenant, project, artifact))).mappings().first()
        if row is None:
            raise ValueError("persona_asset_unavailable")
        if (
            len(row["payload"].encode()) > 16_384
            or hashlib.sha256(row["payload"].encode()).hexdigest() != row["payload_sha256"]
        ):
            raise ValueError("persona_asset_integrity_failed")
        value = PersonaImageAsset.model_validate_json(row["payload"])
        if (value.image.tenant_id, value.image.project_id, value.image.artifact_id) != (tenant, project, artifact):
            raise ValueError("persona_asset_integrity_failed")
        return row, value

    def get_active(self, tenant, project, artifact):
        with self.engine.connect() as connection:
            row, value = self._read(connection, tenant, project, artifact)
        if row["state"] != "active":
            raise ValueError("persona_asset_not_active")
        return value, row["revision"]

    def transition(self, tenant, project, artifact, *, expected_revision, state, actor, stored_paths=None):
        if type(expected_revision) is not int or expected_revision < 1 or state not in ("active", "failed", "revoked"):
            raise ValueError("persona_asset_transition_invalid")
        with self.engine.begin() as connection:
            row, asset = self._read(connection, tenant, project, artifact)
            if row["revision"] != expected_revision or row["state"] not in (
                ("pending",) if state != "revoked" else ("pending", "active")
            ):
                raise ValueError("persona_asset_transition_conflict")
            references = (asset.image.artifact_id, asset.preview.artifact_id)
            if state == "active" and (
                not isinstance(stored_paths, dict)
                or set(stored_paths) != set(references)
                or any(
                    not isinstance(path, str) or len(path) > 4096 or "\x00" in path or not Path(path).is_absolute()
                    for path in stored_paths.values()
                )
            ):
                raise ValueError("persona_asset_storage_required")
            changed = connection.execute(
                update(assets)
                .where(
                    *_where(tenant, project, artifact),
                    assets.c.revision == expected_revision,
                    assets.c.state == row["state"],
                )
                .values(revision=expected_revision + 1, state=state)
            )
            if changed.rowcount != 1:
                raise ValueError("persona_asset_transition_conflict")
            _event(connection, asset, expected_revision + 1, state, actor)
            changed = connection.execute(
                update(ArtifactDB.__table__)
                .where(ArtifactDB.__table__.c.id.in_(references))
                .values(status="stored" if state == "active" else state)
            )
            if changed.rowcount != 2:
                raise ValueError("persona_asset_catalog_incomplete")
            if state == "active":
                for reference in references:
                    changed = connection.execute(
                        update(ArtifactVersionDB.__table__)
                        .where(
                            ArtifactVersionDB.__table__.c.artifact_id == reference,
                            ArtifactVersionDB.__table__.c.version_number == 1,
                        )
                        .values(storage_path=stored_paths[reference])
                    )
                    if changed.rowcount != 1:
                        raise ValueError("persona_asset_catalog_incomplete")
        return expected_revision + 1
