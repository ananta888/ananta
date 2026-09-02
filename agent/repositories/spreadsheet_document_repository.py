"""Transactional SQL repository for immutable spreadsheet document versions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent.db_models.spreadsheet_studio import (
    SpreadsheetDocumentDB,
    SpreadsheetDocumentVersionDB,
    SpreadsheetProposalResultDB,
)
from agent.ports.spreadsheet import SpreadsheetStoreConflict
from ananta_contracts.spreadsheet_studio import canonical_digest, canonical_json, require_id


class SqlSpreadsheetDocumentRepository:
    """Persist versions and proposal decisions atomically in the Hub database."""

    durable = True
    production_component = True

    def __init__(self, *, db_engine) -> None:
        self._engine = db_engine

    def create_document(self, tenant_id: str, document: Mapping[str, Any]) -> dict[str, Any]:
        tenant = require_id(tenant_id, "tenant_id")
        document_id = require_id(document.get("document_id"), "document_id")
        owner_id = require_id(document.get("owner_id"), "owner_id")
        value = {**dict(document), "tenant_id": tenant, "version": 1}
        now = datetime.now(timezone.utc)
        try:
            with Session(bind=self._engine, expire_on_commit=False) as session, session.begin():
                session.add(
                    SpreadsheetDocumentDB(
                        tenant_id=tenant,
                        document_id=document_id,
                        owner_id=owner_id,
                        current_version=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.add(self._version(value, parent_version=None, created_at=now))
        except IntegrityError as exc:
            raise SpreadsheetStoreConflict("spreadsheet_document_exists") from exc
        return value

    def get_document(self, tenant_id: str, document_id: str) -> dict[str, Any]:
        tenant, document = self._scope(tenant_id, document_id)
        with Session(bind=self._engine) as session:
            record = session.get(SpreadsheetDocumentDB, (tenant, document))
            if record is None:
                raise KeyError("spreadsheet_document_not_found")
            version = session.get(
                SpreadsheetDocumentVersionDB,
                (tenant, document, record.current_version),
            )
        if version is None:
            raise RuntimeError("spreadsheet_document_version_integrity_failed")
        return self._payload(version)

    def get_version(self, tenant_id: str, document_id: str, version: int) -> dict[str, Any]:
        tenant, document = self._scope(tenant_id, document_id)
        number = self._version_number(version)
        with Session(bind=self._engine) as session:
            document_record = session.get(SpreadsheetDocumentDB, (tenant, document))
            if document_record is None:
                raise KeyError("spreadsheet_document_not_found")
            record = session.get(SpreadsheetDocumentVersionDB, (tenant, document, number))
        if record is None:
            raise KeyError("spreadsheet_document_version_not_found")
        return self._payload(record)

    def list_documents(self, tenant_id: str, *, limit: int = 100) -> dict[str, Any]:
        bounded = self._limit(limit)
        tenant = require_id(tenant_id, "tenant_id")
        with Session(bind=self._engine) as session:
            documents = list(
                session.execute(
                    select(SpreadsheetDocumentDB)
                    .where(SpreadsheetDocumentDB.tenant_id == tenant)
                    .order_by(SpreadsheetDocumentDB.document_id)
                    .limit(bounded)
                ).scalars()
            )
            records = [
                session.get(
                    SpreadsheetDocumentVersionDB,
                    (tenant, document.document_id, document.current_version),
                )
                for document in documents
            ]
        if any(record is None for record in records):
            raise RuntimeError("spreadsheet_document_version_integrity_failed")
        return {"items": [self._payload(record) for record in records if record is not None], "limit": bounded}

    def list_versions(self, tenant_id: str, document_id: str, *, limit: int = 100) -> dict[str, Any]:
        bounded = self._limit(limit)
        tenant, document = self._scope(tenant_id, document_id)
        with Session(bind=self._engine) as session:
            if session.get(SpreadsheetDocumentDB, (tenant, document)) is None:
                raise KeyError("spreadsheet_document_not_found")
            records = list(
                session.execute(
                    select(SpreadsheetDocumentVersionDB)
                    .where(
                        SpreadsheetDocumentVersionDB.tenant_id == tenant,
                        SpreadsheetDocumentVersionDB.document_id == document,
                    )
                    .order_by(SpreadsheetDocumentVersionDB.version.desc())
                    .limit(bounded)
                ).scalars()
            )
        return {"items": [self._payload(record) for record in records], "limit": bounded}

    def get_proposal(self, tenant_id: str, proposal_id: str) -> dict[str, Any] | None:
        key = (require_id(tenant_id, "tenant_id"), require_id(proposal_id, "proposal_id"))
        with Session(bind=self._engine) as session:
            record = session.get(SpreadsheetProposalResultDB, key)
        return self._proposal_payload(record) if record is not None else None

    def referenced_artifact_digests(self) -> set[str]:
        """Return only content digests referenced by immutable document versions."""

        with Session(bind=self._engine) as session:
            payloads = list(session.execute(select(SpreadsheetDocumentVersionDB.payload_json)).scalars())
        digests: set[str] = set()
        for encoded in payloads:
            payload = json.loads(encoded)
            for field in ("source_artifact", "published_artifact", "candidate_artifact"):
                artifact = payload.get(field)
                digest = artifact.get("sha256") if isinstance(artifact, dict) else None
                if isinstance(digest, str) and len(digest) == 64:
                    digests.add(digest)
        return digests

    def finalize_proposal(
        self,
        tenant_id: str,
        proposal_id: str,
        result: Mapping[str, Any],
        *,
        document_id: str,
        expected_version: int,
        promoted_document: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        tenant, document = self._scope(tenant_id, document_id)
        proposal = require_id(proposal_id, "proposal_id")
        expected = self._version_number(expected_version)
        value = dict(result)
        try:
            with Session(bind=self._engine, expire_on_commit=False) as session, session.begin():
                existing = session.get(SpreadsheetProposalResultDB, (tenant, proposal))
                if existing is not None:
                    previous = self._proposal_payload(existing)
                    if previous.get("proposal_digest") != value.get("proposal_digest"):
                        raise SpreadsheetStoreConflict("spreadsheet_proposal_replay_conflict")
                    return {**previous, "replayed": True}
                document_record = session.execute(
                    select(SpreadsheetDocumentDB)
                    .where(
                        SpreadsheetDocumentDB.tenant_id == tenant,
                        SpreadsheetDocumentDB.document_id == document,
                    )
                    .with_for_update()
                ).scalar_one_or_none()
                if document_record is None:
                    raise KeyError("spreadsheet_document_not_found")
                if document_record.current_version != expected:
                    raise SpreadsheetStoreConflict("spreadsheet_document_version_conflict")
                if promoted_document is not None:
                    next_version = expected + 1
                    published = {
                        **dict(promoted_document),
                        "tenant_id": tenant,
                        "document_id": document,
                        "version": next_version,
                    }
                    session.add(self._version(published, parent_version=expected))
                    changed = session.execute(
                        update(SpreadsheetDocumentDB)
                        .where(
                            SpreadsheetDocumentDB.tenant_id == tenant,
                            SpreadsheetDocumentDB.document_id == document,
                            SpreadsheetDocumentDB.current_version == expected,
                        )
                        .values(current_version=next_version, updated_at=datetime.now(timezone.utc))
                    ).rowcount
                    if changed != 1:
                        raise SpreadsheetStoreConflict("spreadsheet_document_version_conflict")
                    value["promoted_version"] = next_version
                session.add(
                    SpreadsheetProposalResultDB(
                        tenant_id=tenant,
                        proposal_id=proposal,
                        document_id=document,
                        base_version=expected,
                        proposal_digest=str(value.get("proposal_digest") or ""),
                        result_digest=canonical_digest(value),
                        payload_json=canonical_json(value),
                    )
                )
        except IntegrityError as exc:
            raise SpreadsheetStoreConflict("spreadsheet_proposal_concurrent_mutation") from exc
        return {**value, "replayed": False}

    @staticmethod
    def _version(
        value: Mapping[str, Any],
        *,
        parent_version: int | None,
        created_at: datetime | None = None,
    ) -> SpreadsheetDocumentVersionDB:
        payload = dict(value)
        return SpreadsheetDocumentVersionDB(
            tenant_id=str(payload["tenant_id"]),
            document_id=str(payload["document_id"]),
            version=int(payload["version"]),
            parent_version=parent_version,
            state=str(payload.get("state") or "published"),
            snapshot_digest=str(payload.get("snapshot_digest") or ""),
            payload_digest=canonical_digest(payload),
            payload_json=canonical_json(payload),
            created_at=created_at or datetime.now(timezone.utc),
        )

    @staticmethod
    def _payload(record: SpreadsheetDocumentVersionDB) -> dict[str, Any]:
        value = json.loads(record.payload_json)
        if canonical_digest(value) != record.payload_digest:
            raise RuntimeError("spreadsheet_document_payload_integrity_failed")
        if value.get("snapshot_digest") != record.snapshot_digest:
            raise RuntimeError("spreadsheet_snapshot_projection_integrity_failed")
        return value

    @staticmethod
    def _proposal_payload(record: SpreadsheetProposalResultDB) -> dict[str, Any]:
        value = json.loads(record.payload_json)
        if canonical_digest(value) != record.result_digest:
            raise RuntimeError("spreadsheet_proposal_payload_integrity_failed")
        return value

    @staticmethod
    def _scope(tenant_id: str, document_id: str) -> tuple[str, str]:
        return require_id(tenant_id, "tenant_id"), require_id(document_id, "document_id")

    @staticmethod
    def _limit(limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("spreadsheet_document_list_limit_invalid")
        return limit

    @staticmethod
    def _version_number(version: int) -> int:
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("spreadsheet_document_version_invalid")
        return version


__all__ = ["SqlSpreadsheetDocumentRepository"]
