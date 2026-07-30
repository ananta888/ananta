"""Bounded Hub admission for direct text and notebook content."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol

from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from agent.config import settings
from agent.db_models.source_control import (
    SourceConnectionDB,
    SourceControlContentDB,
    SourceRevisionDB,
)
from agent.services.augment.augment_secret_scanner import (
    AugmentSecretScanner,
)
from ananta_contracts.source_control import (
    AdmissionState,
    ConnectionState,
    ConnectorType,
    Sensitivity,
    SourceConnection,
    SourceRevision,
)


_MEDIA_TYPES = frozenset({"text/plain", "text/markdown"})
_SOURCE_TYPES = frozenset({"direct_text", "notebook"})
_CELL_TYPES = frozenset({"markdown", "code"})
_OUTPUT_TYPES = frozenset({"stream", "text", "error"})
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class SourceControlContentAdmissionError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class ContentIdempotencyPort(Protocol):
    def claim(
        self, *, idempotency_key: str, plan_digest: str
    ) -> object: ...

    def complete(
        self,
        *,
        idempotency_key: str,
        plan_digest: str,
        result: Mapping[str, object],
    ) -> None: ...


@dataclass(frozen=True)
class _NormalizedContent:
    project_id: str
    source_type: str
    display_name: str
    sensitivity: Sensitivity
    media_type: str
    content: Mapping[str, object]
    canonical_json: str
    byte_size: int
    cell_count: int
    output_bytes: int
    manifest_digest: str
    revision_digest: str
    connection_identity_digest: str

    @property
    def connector_type(self) -> ConnectorType:
        return (
            ConnectorType.OPEN_NOTEBOOK
            if self.source_type == "notebook"
            else ConnectorType.LOCAL_DUMP
        )

    def preview(self) -> dict[str, object]:
        return {
            "source_type": self.source_type,
            "display_name": self.display_name,
            "media_type": self.media_type,
            "sensitivity": self.sensitivity.value,
            "byte_size": self.byte_size,
            "cell_count": self.cell_count,
            "output_bytes": self.output_bytes,
            "manifest_digest": self.manifest_digest,
            "revision_digest": self.revision_digest,
            "capabilities": {
                "immutable_revision": True,
                "raw_location_inputs_accepted": False,
                "browser_ids_accepted": False,
                "binary_content_accepted": False,
                "secret_content_accepted": False,
            },
        }


class SourceControlContentAdmissionService:
    def __init__(
        self,
        *,
        engine: Engine,
        idempotency: ContentIdempotencyPort,
        clock=time.time,
    ) -> None:
        self._engine = engine
        self._idempotency = idempotency
        self._clock = clock
        self._secrets = AugmentSecretScanner()

    def validate(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        del tenant_id, actor_id
        normalized = self._normalize(
            authoritative_project_id=project_id,
            payload=payload,
            expected_dry_run=True,
        )
        return {"valid": True, "preview": normalized.preview()}

    def admit(
        self,
        *,
        tenant_id: str,
        project_id: str,
        actor_id: str,
        payload: Mapping[str, object],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        normalized = self._normalize(
            authoritative_project_id=project_id,
            payload=payload,
            expected_dry_run=False,
        )
        request_digest = _digest(
            {
                "operation": "content_admission",
                "tenant_id": tenant_id,
                "project_id": project_id,
                "actor_id": actor_id,
                "content": normalized.canonical_json,
            }
        )
        operation_key = _operation_key(
            tenant_id=tenant_id,
            project_id=project_id,
            idempotency_key=idempotency_key,
        )
        claim = self._idempotency.claim(
            idempotency_key=operation_key,
            plan_digest=request_digest,
        )
        state = str(getattr(claim, "state", ""))
        if state == "completed":
            return dict(getattr(claim, "result", None) or {})

        connection, revision = self._contracts(
            tenant_id=tenant_id,
            actor_id=actor_id,
            content=normalized,
        )
        if state == "in_progress":
            recovered = self._recover(
                connection=connection,
                revision=revision,
                content=normalized,
            )
            if recovered is None:
                raise SourceControlContentAdmissionError(
                    "content_admission_in_progress", status_code=409
                )
            self._idempotency.complete(
                idempotency_key=operation_key,
                plan_digest=request_digest,
                result=recovered,
            )
            return recovered

        result = self._persist(
            connection=connection,
            revision=revision,
            content=normalized,
        )
        self._idempotency.complete(
            idempotency_key=operation_key,
            plan_digest=request_digest,
            result=result,
        )
        return result

    def _normalize(
        self,
        *,
        authoritative_project_id: str,
        payload: Mapping[str, object],
        expected_dry_run: bool,
    ) -> _NormalizedContent:
        source_type = str(payload.get("source_type") or "").strip()
        common = {
            "project_id",
            "source_type",
            "display_name",
            "sensitivity",
            "dry_run",
        }
        expected = (
            common | {"content", "media_type"}
            if source_type == "direct_text"
            else common | {"notebook"}
        )
        if (
            source_type not in _SOURCE_TYPES
            or set(payload) != expected
            or payload.get("dry_run") is not expected_dry_run
        ):
            raise SourceControlContentAdmissionError(
                "content_admission_request_invalid"
            )
        if str(payload.get("project_id") or "") != authoritative_project_id:
            raise SourceControlContentAdmissionError(
                "source_control_project_scope_mismatch", status_code=403
            )
        display_name = str(payload.get("display_name") or "").strip()
        if not 1 <= len(display_name) <= 200:
            raise SourceControlContentAdmissionError(
                "content_display_name_invalid"
            )
        try:
            sensitivity = Sensitivity(str(payload.get("sensitivity") or ""))
        except ValueError as exc:
            raise SourceControlContentAdmissionError(
                "content_sensitivity_invalid"
            ) from exc
        if source_type == "direct_text":
            (
                normalized_content,
                media_type,
                cell_count,
                output_bytes,
            ) = self._direct_text(payload)
        else:
            (
                normalized_content,
                media_type,
                cell_count,
                output_bytes,
            ) = self._notebook(payload)
        canonical_json = json.dumps(
            normalized_content,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        encoded = canonical_json.encode("utf-8")
        maximum = int(
            getattr(settings, "codecompass_max_file_bytes", 16 * 1024 * 1024)
        )
        if not encoded or len(encoded) > maximum:
            raise SourceControlContentAdmissionError(
                "content_size_budget_exceeded"
            )
        manifest_digest = hashlib.sha256(encoded).hexdigest()
        revision_digest = _digest(
            {
                "source_type": source_type,
                "manifest_digest": manifest_digest,
                "media_type": media_type,
            }
        )
        return _NormalizedContent(
            project_id=authoritative_project_id,
            source_type=source_type,
            display_name=display_name,
            sensitivity=sensitivity,
            media_type=media_type,
            content=normalized_content,
            canonical_json=canonical_json,
            byte_size=len(encoded),
            cell_count=cell_count,
            output_bytes=output_bytes,
            manifest_digest=manifest_digest,
            revision_digest=revision_digest,
            connection_identity_digest=_digest(
                {
                    "source_type": source_type,
                    "manifest_digest": manifest_digest,
                }
            ),
        )

    def _direct_text(
        self, payload: Mapping[str, object]
    ) -> tuple[Mapping[str, object], str, int, int]:
        media_type = str(payload.get("media_type") or "").strip().lower()
        content = payload.get("content")
        if (
            media_type not in _MEDIA_TYPES
            or not isinstance(content, str)
            or not content.strip()
        ):
            raise SourceControlContentAdmissionError(
                "direct_text_content_invalid"
            )
        self._require_safe_text(content)
        return (
            {"content": content, "media_type": media_type},
            media_type,
            0,
            0,
        )

    def _notebook(
        self, payload: Mapping[str, object]
    ) -> tuple[Mapping[str, object], str, int, int]:
        notebook = payload.get("notebook")
        if not isinstance(notebook, Mapping) or set(notebook) != {"cells"}:
            raise SourceControlContentAdmissionError(
                "notebook_document_invalid"
            )
        cells = notebook.get("cells")
        maximum_cells = int(
            getattr(settings, "codecompass_max_notebook_cells", 1_000)
        )
        if (
            not isinstance(cells, list)
            or not 1 <= len(cells) <= maximum_cells
        ):
            raise SourceControlContentAdmissionError(
                "notebook_cell_budget_exceeded"
            )
        maximum_cell_chars = int(
            getattr(
                settings,
                "codecompass_max_notebook_cell_chars",
                1_000_000,
            )
        )
        maximum_output_bytes = int(
            getattr(
                settings,
                "codecompass_max_notebook_output_bytes",
                8 * 1024 * 1024,
            )
        )
        normalized_cells: list[dict[str, object]] = []
        output_bytes = 0
        for raw_cell in cells:
            if (
                not isinstance(raw_cell, Mapping)
                or set(raw_cell) != {"cell_type", "source", "outputs"}
            ):
                raise SourceControlContentAdmissionError(
                    "notebook_cell_invalid"
                )
            cell_type = str(raw_cell.get("cell_type") or "")
            source = raw_cell.get("source")
            outputs = raw_cell.get("outputs")
            if (
                cell_type not in _CELL_TYPES
                or not isinstance(source, str)
                or len(source) > maximum_cell_chars
                or not isinstance(outputs, list)
                or (cell_type == "markdown" and outputs)
            ):
                raise SourceControlContentAdmissionError(
                    "notebook_cell_invalid"
                )
            self._require_safe_text(source)
            normalized_outputs: list[dict[str, str]] = []
            for raw_output in outputs:
                if (
                    not isinstance(raw_output, Mapping)
                    or set(raw_output) != {"output_type", "text"}
                ):
                    raise SourceControlContentAdmissionError(
                        "notebook_binary_output_forbidden"
                    )
                output_type = str(raw_output.get("output_type") or "")
                text = raw_output.get("text")
                if output_type not in _OUTPUT_TYPES or not isinstance(
                    text, str
                ):
                    raise SourceControlContentAdmissionError(
                        "notebook_output_invalid"
                    )
                self._require_safe_text(text)
                output_bytes += len(text.encode("utf-8"))
                if output_bytes > maximum_output_bytes:
                    raise SourceControlContentAdmissionError(
                        "notebook_output_budget_exceeded"
                    )
                normalized_outputs.append(
                    {"output_type": output_type, "text": text}
                )
            normalized_cells.append(
                {
                    "cell_type": cell_type,
                    "source": source,
                    "outputs": normalized_outputs,
                }
            )
        return (
            {"cells": normalized_cells},
            "application/x-ipynb+json",
            len(normalized_cells),
            output_bytes,
        )

    def _require_safe_text(self, value: str) -> None:
        if _CONTROL_CHARACTERS.search(value):
            raise SourceControlContentAdmissionError(
                "binary_content_forbidden"
            )
        result = self._secrets.scan_and_redact_text(value)
        if not result.clean:
            raise SourceControlContentAdmissionError(
                "secret_content_forbidden"
            )

    def _contracts(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        content: _NormalizedContent,
    ) -> tuple[SourceConnection, SourceRevision]:
        captured_at = datetime.fromtimestamp(
            float(self._clock()), tz=timezone.utc
        )
        connection = SourceConnection.create(
            tenant_id=tenant_id,
            project_id=content.project_id,
            owner_id=actor_id,
            connector_type=content.connector_type,
            connection_identity_digest=content.connection_identity_digest,
            display_name=content.display_name,
            sensitivity=content.sensitivity,
            state=ConnectionState.ACTIVE,
            created_at=captured_at,
        )
        revision = SourceRevision.create(
            connection_id=connection.connection_id,
            tenant_id=connection.tenant_id,
            project_id=connection.project_id,
            owner_id=connection.owner_id,
            connector_type=connection.connector_type,
            sensitivity=connection.sensitivity,
            revision_token=f"sha256:{content.revision_digest}",
            revision_digest=content.revision_digest,
            content_manifest_id=f"manifest_{content.manifest_digest}",
            content_manifest_digest=content.manifest_digest,
            admission_state=AdmissionState.ADMITTED,
            captured_at=captured_at,
        )
        return connection, revision

    def _persist(
        self,
        *,
        connection: SourceConnection,
        revision: SourceRevision,
        content: _NormalizedContent,
    ) -> dict[str, object]:
        now = float(self._clock())
        with Session(self._engine) as db:
            existing_connection = db.get(
                SourceConnectionDB, connection.connection_id
            )
            if existing_connection is None:
                db.add(
                    SourceConnectionDB(
                        connection_id=connection.connection_id,
                        tenant_id=connection.tenant_id,
                        project_id=connection.project_id,
                        owner_id=connection.owner_id,
                        connector_type=connection.connector_type.value,
                        connection_identity_digest=(
                            connection.connection_identity_digest
                        ),
                        display_name=connection.display_name,
                        sensitivity=connection.sensitivity.value,
                        state=connection.state.value,
                        lock_version=1,
                        created_at_epoch=connection.created_at.timestamp(),
                        updated_at_epoch=now,
                    )
                )
            elif not self._connection_matches(
                existing_connection, connection
            ):
                raise SourceControlContentAdmissionError(
                    "content_connection_conflict", status_code=409
                )
            existing_revision = db.get(
                SourceRevisionDB, revision.source_revision_id
            )
            if existing_revision is None:
                db.add(
                    SourceRevisionDB(
                        source_revision_id=revision.source_revision_id,
                        connection_id=revision.connection_id,
                        tenant_id=revision.tenant_id,
                        project_id=revision.project_id,
                        owner_id=revision.owner_id,
                        connector_type=revision.connector_type.value,
                        sensitivity=revision.sensitivity.value,
                        revision_token=revision.revision_token,
                        revision_digest=revision.revision_digest,
                        content_manifest_id=revision.content_manifest_id,
                        content_manifest_digest=(
                            revision.content_manifest_digest
                        ),
                        admission_state=revision.admission_state.value,
                        captured_at_epoch=revision.captured_at.timestamp(),
                    )
                )
            elif not self._revision_matches(existing_revision, revision):
                raise SourceControlContentAdmissionError(
                    "content_revision_conflict", status_code=409
                )
            existing_content = db.get(
                SourceControlContentDB, revision.source_revision_id
            )
            if existing_content is None:
                db.add(
                    SourceControlContentDB(
                        source_revision_id=revision.source_revision_id,
                        connection_id=connection.connection_id,
                        tenant_id=connection.tenant_id,
                        project_id=connection.project_id,
                        owner_id=connection.owner_id,
                        content_kind=content.source_type,
                        display_name=content.display_name,
                        media_type=content.media_type,
                        manifest_digest=content.manifest_digest,
                        byte_size=content.byte_size,
                        cell_count=content.cell_count,
                        output_bytes=content.output_bytes,
                        normalized_content_json=content.canonical_json,
                        created_at_epoch=now,
                    )
                )
            elif (
                existing_content.manifest_digest
                != content.manifest_digest
                or existing_content.normalized_content_json
                != content.canonical_json
            ):
                raise SourceControlContentAdmissionError(
                    "content_payload_conflict", status_code=409
                )
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise SourceControlContentAdmissionError(
                    "content_admission_conflict", status_code=409
                ) from None
        return self._result(
            connection=connection, revision=revision, content=content
        )

    def _recover(
        self,
        *,
        connection: SourceConnection,
        revision: SourceRevision,
        content: _NormalizedContent,
    ) -> dict[str, object] | None:
        with Session(self._engine) as db:
            connection_row = db.get(
                SourceConnectionDB, connection.connection_id
            )
            revision_row = db.get(
                SourceRevisionDB, revision.source_revision_id
            )
            content_row = db.get(
                SourceControlContentDB, revision.source_revision_id
            )
            if (
                connection_row is None
                or revision_row is None
                or content_row is None
                or not self._connection_matches(
                    connection_row, connection
                )
                or not self._revision_matches(revision_row, revision)
                or content_row.manifest_digest != content.manifest_digest
                or content_row.normalized_content_json
                != content.canonical_json
            ):
                return None
        return self._result(
            connection=connection, revision=revision, content=content
        )

    @staticmethod
    def _connection_matches(
        row: SourceConnectionDB, value: SourceConnection
    ) -> bool:
        return (
            row.tenant_id == value.tenant_id
            and row.project_id == value.project_id
            and row.owner_id == value.owner_id
            and row.connector_type == value.connector_type.value
            and row.connection_identity_digest
            == value.connection_identity_digest
        )

    @staticmethod
    def _revision_matches(
        row: SourceRevisionDB, value: SourceRevision
    ) -> bool:
        return (
            row.connection_id == value.connection_id
            and row.tenant_id == value.tenant_id
            and row.project_id == value.project_id
            and row.owner_id == value.owner_id
            and row.revision_digest == value.revision_digest
            and row.content_manifest_digest
            == value.content_manifest_digest
            and row.admission_state == "admitted"
        )

    @staticmethod
    def _result(
        *,
        connection: SourceConnection,
        revision: SourceRevision,
        content: _NormalizedContent,
    ) -> dict[str, object]:
        return {
            "connection": connection.to_wire(),
            "revision": revision.to_wire(),
            "content": content.preview(),
        }


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _operation_key(
    *, tenant_id: str, project_id: str, idempotency_key: str
) -> str:
    return "content:" + hashlib.sha256(
        f"{tenant_id}\0{project_id}\0{idempotency_key}".encode("utf-8")
    ).hexdigest()


__all__ = [
    "SourceControlContentAdmissionError",
    "SourceControlContentAdmissionService",
]
