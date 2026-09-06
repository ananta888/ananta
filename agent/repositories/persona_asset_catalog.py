"""Immutable two-part persona catalog with injected image/video format adapters."""

import hashlib
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from agent.db_models import ArtifactDB, ArtifactVersionDB


class PersonaCatalogFormat(Protocol):
    def primary(self, asset): ...
    def parts(self, asset): ...
    def decode(self, payload): ...


class SqlPersonaAssetCatalog:
    def __init__(self, engine, *, metadata, assets, events, format: PersonaCatalogFormat):
        self.engine, self.metadata = engine, metadata
        self.assets, self.events, self.format = assets, events, format

    def initialize(self):
        self.metadata.create_all(self.engine)

    def _where(self, tenant, project, artifact):
        return (
            self.assets.c.tenant_id == tenant,
            self.assets.c.project_id == project,
            self.assets.c.artifact_id == artifact,
        )

    def _event(self, connection, asset, revision, state, actor):
        if (
            not isinstance(actor, str)
            or not 0 < len(actor) <= 255
            or any(ord(char) < 32 or ord(char) == 127 for char in actor)
        ):
            raise ValueError("persona_asset_actor_invalid")
        connection.execute(
            insert(self.events).values(
                tenant_id=self.format.primary(asset).tenant_id,
                project_id=self.format.primary(asset).project_id,
                artifact_id=self.format.primary(asset).artifact_id,
                revision=revision,
                state=state,
                actor=actor,
            )
        )

    def scan_active_ids(self, tenant, project, *, after, limit):
        if type(limit) is not int or not 1 <= limit <= 65:
            raise ValueError("persona_image_scan_limit_invalid")
        with self.engine.connect() as connection:
            return tuple(
                connection.execute(
                    select(self.assets.c.artifact_id)
                    .where(
                        self.assets.c.tenant_id == tenant,
                        self.assets.c.project_id == project,
                        self.assets.c.state == "active",
                        self.assets.c.artifact_id > after,
                    )
                    .order_by(self.assets.c.artifact_id)
                    .limit(limit)
                ).scalars()
            )

    def reserve(self, asset, *, actor: str):
        payload = asset.model_dump_json()
        if len(payload.encode()) > 16_384:
            raise ValueError("persona_asset_metadata_too_large")
        asset = self.format.decode(payload)
        try:
            with self.engine.begin() as connection:
                self._event(connection, asset, 1, "pending", actor)
                connection.execute(
                    insert(self.assets).values(
                        tenant_id=self.format.primary(asset).tenant_id,
                        project_id=self.format.primary(asset).project_id,
                        artifact_id=self.format.primary(asset).artifact_id,
                        revision=1,
                        state="pending",
                        payload=payload,
                        payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
                    )
                )
                for part in self.format.parts(asset):
                    reference, kind, size = part.reference, part.kind, part.size
                    version_id = str(uuid.uuid4())
                    metadata = {
                        "system_artifact_kind": kind,
                        "tenant_id": reference.tenant_id,
                        "project_id": reference.project_id,
                        "persona_asset_id": self.format.primary(asset).artifact_id,
                    }
                    connection.execute(
                        insert(ArtifactDB.__table__).values(
                            id=reference.artifact_id,
                            latest_version_id=version_id,
                            latest_sha256=reference.sha256,
                            latest_media_type=part.media_type,
                            latest_filename=part.filename,
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
                            original_filename=part.filename,
                            media_type=part.media_type,
                            size_bytes=size,
                            sha256=reference.sha256,
                            version_metadata=metadata,
                        )
                    )
        except IntegrityError:
            raise ValueError("persona_asset_reservation_conflict") from None

    def _read(self, connection, tenant, project, artifact):
        row = connection.execute(select(self.assets).where(*self._where(tenant, project, artifact))).mappings().first()
        if row is None:
            raise ValueError("persona_asset_unavailable")
        if (
            len(row["payload"].encode()) > 16_384
            or hashlib.sha256(row["payload"].encode()).hexdigest() != row["payload_sha256"]
        ):
            raise ValueError("persona_asset_integrity_failed")
        value = self.format.decode(row["payload"])
        if (
            self.format.primary(value).tenant_id,
            self.format.primary(value).project_id,
            self.format.primary(value).artifact_id,
        ) != (tenant, project, artifact):
            raise ValueError("persona_asset_integrity_failed")
        return row, value

    def get_active(self, tenant, project, artifact):
        with self.engine.connect() as connection:
            row, value = self._read(connection, tenant, project, artifact)
        if row["state"] != "active":
            raise ValueError("persona_asset_not_active")
        return value, row["revision"]

    def get_retired(self, tenant, project, artifact):
        with self.engine.connect() as connection:
            row, value = self._read(connection, tenant, project, artifact)
        if row["state"] not in ("failed", "revoked", "purging", "purged"):
            raise ValueError("persona_asset_not_retired")
        return value, row["revision"], row["state"]

    @contextmanager
    def storage_guard(self, tenant, project, artifact, *, expected_revision, state):
        """Fence immutable writes against erasure across Hub processes, not just threads."""
        if state not in ("pending", "purging") or type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("persona_asset_storage_guard_invalid")
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(self.assets)
                .where(
                    *self._where(tenant, project, artifact),
                    self.assets.c.revision == expected_revision,
                    self.assets.c.state == state,
                )
                .values(revision=expected_revision)
            )
            if changed.rowcount != 1:
                raise ValueError("persona_asset_storage_guard_conflict")
            self._read(connection, tenant, project, artifact)
            yield

    def transition(self, tenant, project, artifact, *, expected_revision, state, actor, stored_paths=None):
        predecessors = {
            "active": ("pending",),
            "failed": ("pending",),
            "revoked": ("pending", "active"),
            "purging": ("failed", "revoked"),
            "purged": ("purging",),
        }
        if type(expected_revision) is not int or not 1 <= expected_revision < 2**53 - 1 or state not in predecessors:
            raise ValueError("persona_asset_transition_invalid")
        with self.engine.begin() as connection:
            row, asset = self._read(connection, tenant, project, artifact)
            if row["revision"] != expected_revision or row["state"] not in predecessors[state]:
                raise ValueError("persona_asset_transition_conflict")
            references = tuple(part.reference.artifact_id for part in self.format.parts(asset))
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
                update(self.assets)
                .where(
                    *self._where(tenant, project, artifact),
                    self.assets.c.revision == expected_revision,
                    self.assets.c.state == row["state"],
                )
                .values(revision=expected_revision + 1, state=state)
            )
            if changed.rowcount != 1:
                raise ValueError("persona_asset_transition_conflict")
            self._event(connection, asset, expected_revision + 1, state, actor)
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
