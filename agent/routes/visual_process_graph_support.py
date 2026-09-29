"""Visual process graph parsing, ownership, revision archiving and definition-save responses."""

from __future__ import annotations

import json

from flask import (
    jsonify,
    request,
)
from sqlmodel import Session

from agent.auth import get_request_auth_context
from agent.db_models.visual_process import VisualProcessGraphDB
from agent.services.chat_process_binding import bind_graph_owner
from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.visual_process_definition_service import (
    VisualProcessDefinitionConflict,
    VisualProcessDefinitionSecurityError,
)
from agent.visual_process.models import VisualProcessGraph
from agent.visual_process.validator import VisualProcessValidator


def _visual_process_module():
    """Resolve monkeypatch seams through the public ``agent.routes.visual_process`` module at call time.

    Tests patch collaborators such as service getters on ``agent.routes.visual_process``;
    looking them up lazily keeps those patches effective for code that
    now lives in sibling modules (and avoids an import-time cycle).
    """
    import importlib

    return importlib.import_module("agent.routes.visual_process")


_validator = VisualProcessValidator()


def _graph_principal() -> ChatSessionPrincipal | None:
    identity = dict(get_request_auth_context() or {})
    subject = identity.get("sub") or identity.get("username")
    tenant_id = identity.get("tenant_id") or identity.get("tenant") or identity.get("organization_id") or subject
    try:
        return ChatSessionPrincipal.from_values(tenant_id, subject)
    except ValueError:
        return None


def _owned_graph_model(
    graph: VisualProcessGraph,
    principal: ChatSessionPrincipal,
) -> VisualProcessGraph:
    return VisualProcessGraph.model_validate(bind_graph_owner(graph.model_dump(), principal))


def _archive_graph_revision(db: Session, row: VisualProcessGraphDB) -> None:
    try:
        data = json.loads(row.graph_json)
    except (TypeError, ValueError):
        return
    version = str(data.get("version") or "1.0")
    revision_id = f"{row.id}@{version}"
    if db.get(VisualProcessGraphDB, revision_id) is None:
        db.add(
            VisualProcessGraphDB(
                id=revision_id,
                name=row.name,
                description=row.description,
                tags=row.tags,
                graph_json=row.graph_json,
                definition_revision=row.definition_revision,
                base_graph_hash=row.base_graph_hash,
                graph_schema_version=row.graph_schema_version,
                node_registry_version=row.node_registry_version,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )


def _parse_graph() -> tuple[VisualProcessGraph | None, dict | None]:
    body = request.get_json(silent=True) or {}
    graph_data = body.get("graph") or body
    try:
        return VisualProcessGraph.model_validate(graph_data), None
    except Exception as exc:
        return None, {"error": "invalid_graph", "detail": str(exc)}


def _definition_preconditions(graph: VisualProcessGraph) -> tuple[int | None, str | None]:
    body = request.get_json(silent=True) or {}
    expected_raw = body.get("expected_revision")
    if expected_raw is None and isinstance(body.get("graph"), dict):
        expected_raw = body["graph"].get("definition_revision")
    if expected_raw is None and graph.definition_revision > 0:
        expected_raw = graph.definition_revision
    expected_revision: int | None
    try:
        expected_revision = int(expected_raw) if expected_raw is not None else None
    except (TypeError, ValueError):
        expected_revision = None

    expected_hash = str(body.get("base_graph_hash") or graph.base_graph_hash or "").strip() or None
    if isinstance(body.get("graph"), dict):
        expected_hash = str(body["graph"].get("base_graph_hash") or expected_hash or "").strip() or None
    if_match = str(request.headers.get("If-Match") or "").strip()
    if if_match:
        if if_match.startswith("W/"):
            if_match = if_match[2:]
        expected_hash = if_match.strip('"') or expected_hash
    return expected_revision, expected_hash


def _definition_error(exc: Exception):
    if isinstance(exc, VisualProcessDefinitionConflict):
        return jsonify(exc.as_dict()), 409
    if isinstance(exc, VisualProcessDefinitionSecurityError):
        status = 428 if exc.reason_code == "definition_precondition_required" else 422
        return jsonify({"error": exc.reason_code, "error_code": exc.reason_code, "path": exc.path}), status
    raise exc


def _definition_save_response(write, *, saved: bool = True):
    return jsonify(
        {
            "id": write.graph.id,
            "version": write.graph.version,
            "graph_schema_version": write.graph.graph_schema_version,
            "node_registry_version": write.graph.node_registry_version,
            "definition_revision": write.definition_revision,
            "base_graph_hash": write.base_graph_hash,
            "saved": saved,
            "changed": write.changed,
        }
    ), 200
