"""Persistence boundary for immutable visual-process definitions.

The service owns serialization, content hashes and database compare-and-swap.
Routes remain responsible for authentication/authorization. Authoritative
runtime execution state remains outside this design-time store; a legacy
``runtime_overlay`` is retained only as a compatibility envelope and remains
excluded from definition hashes.
"""

from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import update
from sqlmodel import Session

from agent.db_models.visual_process import VisualProcessGraphDB
from agent.visual_process.models import VisualProcessGraph


class VisualProcessDefinitionConflict(RuntimeError):
    def __init__(
        self,
        *,
        graph_id: str,
        expected_revision: int | None,
        actual_revision: int,
        expected_hash: str | None,
        actual_hash: str,
    ) -> None:
        super().__init__("visual_process_definition_conflict")
        self.graph_id = graph_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": "visual_process_definition_conflict",
            "error_code": "visual_process_definition_conflict",
            "graph_id": self.graph_id,
            "expected_revision": self.expected_revision,
            "actual_revision": self.actual_revision,
            "expected_base_graph_hash": self.expected_hash,
            "actual_base_graph_hash": self.actual_hash,
        }


class VisualProcessDefinitionSecurityError(ValueError):
    def __init__(self, reason_code: str, path: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.path = path


@dataclass(frozen=True)
class VisualProcessDefinitionWrite:
    graph: VisualProcessGraph
    definition_revision: int
    base_graph_hash: str
    changed: bool

    def response_graph(self) -> dict[str, Any]:
        payload = self.graph.model_dump(exclude={"runtime_overlay"})
        payload["definition_revision"] = self.definition_revision
        payload["base_graph_hash"] = self.base_graph_hash
        if self.graph.runtime_overlay:
            payload["runtime_overlay"] = copy.deepcopy(self.graph.runtime_overlay)
        return payload


class VisualProcessDefinitionService:
    """Store definitions through one atomic, testable CAS seam."""

    _SECRET_KEYS = frozenset(
        {
            "api_key",
            "apikey",
            "access_token",
            "refresh_token",
            "password",
            "credential",
            "client_secret",
            "private_key",
        }
    )

    @staticmethod
    def _storage_payload(
        graph: VisualProcessGraph,
        *,
        revision: int,
        base_graph_hash: str,
    ) -> dict[str, Any]:
        payload = graph.definition_payload(public=False)
        payload["definition_revision"] = revision
        payload["base_graph_hash"] = base_graph_hash
        if graph.runtime_overlay:
            payload["runtime_overlay"] = copy.deepcopy(graph.runtime_overlay)
        return payload

    @staticmethod
    def _row_graph(row: VisualProcessGraphDB) -> VisualProcessGraph:
        graph = VisualProcessGraph.model_validate(json.loads(row.graph_json))
        revision = max(1, int(row.definition_revision or graph.definition_revision or 1))
        base_hash = str(row.base_graph_hash or "") or graph.definition_hash()
        return graph.model_copy(update={"definition_revision": revision, "base_graph_hash": base_hash})

    @classmethod
    def validate_writable_definition(cls, graph: VisualProcessGraph) -> None:
        """Reject inline credentials while permitting opaque ``*_secret_ref`` values."""

        def walk(value: Any, path: str) -> None:
            if isinstance(value, dict):
                for raw_key, item in value.items():
                    key = str(raw_key)
                    normalized = key.lower().replace("-", "_")
                    child = f"{path}/{key}" if path else f"/{key}"
                    if normalized in cls._SECRET_KEYS and not normalized.endswith("_secret_ref"):
                        raise VisualProcessDefinitionSecurityError("inline_secret_forbidden", child)
                    walk(item, child)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, f"{path}/{index}")

        walk(graph.definition_payload(public=True), "")

    def create(
        self,
        db: Session,
        graph: VisualProcessGraph,
        *,
        now: float | None = None,
    ) -> VisualProcessDefinitionWrite:
        self.validate_writable_definition(graph)
        timestamp = float(now if now is not None else time.time())
        revision = 1
        base_hash = graph.definition_hash()
        payload = self._storage_payload(graph, revision=revision, base_graph_hash=base_hash)
        row = VisualProcessGraphDB(
            id=graph.id,
            name=graph.name,
            description=graph.description,
            tags=",".join(graph.tags),
            graph_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            definition_revision=revision,
            base_graph_hash=base_hash,
            graph_schema_version=graph.graph_schema_version,
            node_registry_version=graph.node_registry_version,
            created_at=timestamp,
            updated_at=timestamp,
        )
        db.add(row)
        db.flush()
        stored = graph.model_copy(update={"definition_revision": revision, "base_graph_hash": base_hash})
        return VisualProcessDefinitionWrite(stored, revision, base_hash, True)

    def load(self, row: VisualProcessGraphDB) -> VisualProcessDefinitionWrite:
        graph = self._row_graph(row)
        return VisualProcessDefinitionWrite(
            graph=graph,
            definition_revision=graph.definition_revision,
            base_graph_hash=graph.base_graph_hash,
            changed=False,
        )

    def replace(
        self,
        db: Session,
        row: VisualProcessGraphDB,
        graph: VisualProcessGraph,
        *,
        expected_revision: int | None,
        expected_hash: str | None,
        require_precondition: bool,
        now: float | None = None,
    ) -> VisualProcessDefinitionWrite:
        self.validate_writable_definition(graph)
        current = self._row_graph(row)
        actual_revision = current.definition_revision
        actual_hash = current.base_graph_hash
        if require_precondition and (expected_revision is None or not expected_hash):
            raise VisualProcessDefinitionSecurityError("definition_precondition_required", "/")
        if expected_revision is not None and int(expected_revision) != actual_revision:
            raise VisualProcessDefinitionConflict(
                graph_id=row.id,
                expected_revision=expected_revision,
                actual_revision=actual_revision,
                expected_hash=expected_hash,
                actual_hash=actual_hash,
            )
        if expected_hash and expected_hash != actual_hash:
            raise VisualProcessDefinitionConflict(
                graph_id=row.id,
                expected_revision=expected_revision,
                actual_revision=actual_revision,
                expected_hash=expected_hash,
                actual_hash=actual_hash,
            )

        # The client cannot choose the compatibility content version.  A real
        # mutation advances it from the currently persisted definition.
        candidate = graph.model_copy(
            update={
                "version": current.version,
                "definition_revision": actual_revision,
                "base_graph_hash": actual_hash,
            }
        )
        incoming_hash = candidate.definition_hash()
        if incoming_hash == actual_hash:
            return VisualProcessDefinitionWrite(current, actual_revision, actual_hash, False)

        candidate = candidate.model_copy(update={"version": _next_content_version(current.version)})
        new_hash = candidate.definition_hash()
        new_revision = actual_revision + 1
        payload = self._storage_payload(candidate, revision=new_revision, base_graph_hash=new_hash)
        timestamp = float(now if now is not None else time.time())
        statement = (
            update(VisualProcessGraphDB)
            .where(
                VisualProcessGraphDB.id == row.id,
                VisualProcessGraphDB.definition_revision == actual_revision,
            )
            .values(
                name=candidate.name,
                description=candidate.description,
                tags=",".join(candidate.tags),
                graph_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                definition_revision=new_revision,
                base_graph_hash=new_hash,
                graph_schema_version=candidate.graph_schema_version,
                node_registry_version=candidate.node_registry_version,
                updated_at=timestamp,
            )
        )
        result = db.exec(statement)
        if int(result.rowcount or 0) != 1:
            db.rollback()
            latest = db.get(VisualProcessGraphDB, row.id)
            latest_graph = self._row_graph(latest) if latest is not None else current
            raise VisualProcessDefinitionConflict(
                graph_id=row.id,
                expected_revision=expected_revision,
                actual_revision=latest_graph.definition_revision,
                expected_hash=expected_hash,
                actual_hash=latest_graph.base_graph_hash,
            )
        stored = candidate.model_copy(update={"definition_revision": new_revision, "base_graph_hash": new_hash})
        return VisualProcessDefinitionWrite(stored, new_revision, new_hash, True)


def _next_content_version(version: str) -> str:
    parts = str(version or "1.0").split(".")
    try:
        return f"{int(parts[0])}.{int(parts[1] if len(parts) > 1 else 0) + 1}"
    except ValueError:
        return f"{version}.1"


visual_process_definition_service = VisualProcessDefinitionService()


__all__ = [
    "VisualProcessDefinitionConflict",
    "VisualProcessDefinitionSecurityError",
    "VisualProcessDefinitionService",
    "VisualProcessDefinitionWrite",
    "visual_process_definition_service",
]
