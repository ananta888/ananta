"""Visual process graph persistence endpoints (v1 and definition-backed v2)."""

from __future__ import annotations

import json

from flask import jsonify
from sqlmodel import (
    Session,
    select,
)

from agent.auth import check_user_auth
from agent.database import engine
from agent.db_models.visual_process import VisualProcessGraphDB
from agent.routes.visual_process_blueprint import vp_bp
from agent.routes.visual_process_graph_support import (
    _archive_graph_revision,
    _definition_error,
    _definition_preconditions,
    _definition_save_response,
    _graph_principal,
    _owned_graph_model,
    _parse_graph,
)
from agent.services.chat_process_binding import (
    authorize_graph,
    public_graph,
)
from agent.services.visual_process_definition_service import (
    VisualProcessDefinitionConflict,
    VisualProcessDefinitionSecurityError,
    visual_process_definition_service,
)
from agent.visual_process.models import VisualProcessGraph

# ── Graph persistence (VPPERS-001) ────────────────────────────────────────────


@vp_bp.post("/graphs")
@check_user_auth
def save_graph():
    principal = _graph_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    graph = _owned_graph_model(graph, principal)
    expected_revision, expected_hash = _definition_preconditions(graph)
    with Session(engine) as session:
        existing = session.get(VisualProcessGraphDB, graph.id)
        if existing:
            try:
                previous = json.loads(existing.graph_json)
            except (TypeError, ValueError):
                return jsonify({"error": "not_found"}), 404
            authorized, migrated = authorize_graph(previous, principal)
            if not authorized:
                return jsonify({"error": "resource_id_unavailable", "error_code": "resource_id_unavailable"}), 409
            if migrated:
                existing.graph_json = json.dumps(previous)
            _archive_graph_revision(session, existing)
            try:
                write = visual_process_definition_service.replace(
                    session,
                    existing,
                    graph,
                    expected_revision=expected_revision,
                    expected_hash=expected_hash,
                    require_precondition=False,
                )
            except (VisualProcessDefinitionConflict, VisualProcessDefinitionSecurityError) as exc:
                session.rollback()
                return _definition_error(exc)
        else:
            try:
                write = visual_process_definition_service.create(session, graph)
            except VisualProcessDefinitionSecurityError as exc:
                session.rollback()
                return _definition_error(exc)
        session.commit()
    return _definition_save_response(write)


@vp_bp.get("/graphs")
@check_user_auth
def list_graphs():
    principal = _graph_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    with Session(engine) as session:
        rows = session.exec(select(VisualProcessGraphDB)).all()
        visible: list[tuple[VisualProcessGraphDB, dict]] = []
        changed = False
        for row in rows:
            try:
                graph_data = json.loads(row.graph_json)
            except (TypeError, ValueError):
                continue
            authorized, migrated = authorize_graph(graph_data, principal)
            if not authorized:
                continue
            if migrated:
                row.graph_json = json.dumps(graph_data)
                session.add(row)
                changed = True
            visible.append((row, graph_data))
        if changed:
            session.commit()
            for row, _ in visible:
                session.refresh(row)
    rows_sorted = sorted(visible, key=lambda item: item[0].updated_at, reverse=True)
    return jsonify(
        [
            {
                "id": row.id,
                "name": row.name,
                "description": row.description,
                "tags": [tag for tag in row.tags.split(",") if tag],
                "updated_at": row.updated_at,
                "created_at": row.created_at,
                "version": str(graph_data.get("version") or "1.0"),
                "graph_schema_version": row.graph_schema_version,
                "node_registry_version": row.node_registry_version,
                "definition_revision": row.definition_revision,
                "base_graph_hash": row.base_graph_hash,
                "origin": "revision" if "@" in row.id else "custom",
            }
            for row, graph_data in rows_sorted
        ]
    ), 200


@vp_bp.get("/graphs/<graph_id>")
@check_user_auth
def load_graph(graph_id: str):
    principal = _graph_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    with Session(engine) as session:
        row = session.get(VisualProcessGraphDB, graph_id)
        if not row:
            return jsonify({"error": "not_found"}), 404
        try:
            data = json.loads(row.graph_json)
        except Exception:
            return jsonify({"error": "corrupt_graph_json"}), 500
        authorized, migrated = authorize_graph(data, principal)
        if not authorized:
            return jsonify({"error": "not_found"}), 404
        if migrated:
            row.graph_json = json.dumps(data)
            session.add(row)
            session.commit()
        graph = VisualProcessGraph.model_validate(data).model_copy(
            update={
                "definition_revision": int(row.definition_revision or 1),
                "base_graph_hash": str(row.base_graph_hash or ""),
                "graph_schema_version": str(row.graph_schema_version or "1"),
                "node_registry_version": str(row.node_registry_version or "1"),
            }
        )
        if not graph.base_graph_hash:
            graph = graph.model_copy(update={"base_graph_hash": graph.definition_hash()})
        payload = graph.model_dump(exclude={"runtime_overlay"})
        if graph.runtime_overlay:
            payload["runtime_overlay"] = graph.runtime_overlay
    return jsonify(public_graph(payload)), 200


@vp_bp.put("/graphs/<graph_id>")
@check_user_auth
def update_graph(graph_id: str):
    principal = _graph_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    if graph.id != graph_id:
        return jsonify({"error": "graph_id_mismatch", "error_code": "graph_id_mismatch"}), 400
    graph = _owned_graph_model(graph, principal)
    expected_revision, expected_hash = _definition_preconditions(graph)
    with Session(engine) as session:
        row = session.get(VisualProcessGraphDB, graph_id)
        if not row:
            return jsonify({"error": "not_found"}), 404
        try:
            previous = json.loads(row.graph_json)
        except (TypeError, ValueError):
            return jsonify({"error": "not_found"}), 404
        authorized, migrated = authorize_graph(previous, principal)
        if not authorized:
            return jsonify({"error": "not_found"}), 404
        if migrated:
            row.graph_json = json.dumps(previous)
        _archive_graph_revision(session, row)
        try:
            write = visual_process_definition_service.replace(
                session,
                row,
                graph,
                expected_revision=expected_revision,
                expected_hash=expected_hash,
                require_precondition=False,
            )
        except (VisualProcessDefinitionConflict, VisualProcessDefinitionSecurityError) as exc:
            session.rollback()
            return _definition_error(exc)
        session.commit()
    return _definition_save_response(write)


@vp_bp.post("/v2/graphs")
@check_user_auth
def save_graph_v2():
    """Create or replace a definition; replacements require revision and ETag."""

    return _save_graph_v2_impl(None)


@vp_bp.put("/v2/graphs/<graph_id>")
@check_user_auth
def update_graph_v2(graph_id: str):
    return _save_graph_v2_impl(graph_id)


def _save_graph_v2_impl(graph_id: str | None):
    principal = _graph_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    graph, err = _parse_graph()
    if err:
        return jsonify(err), 400
    assert graph is not None
    if graph_id is not None and graph.id != graph_id:
        return jsonify({"error": "graph_id_mismatch", "error_code": "graph_id_mismatch"}), 400
    graph = _owned_graph_model(graph, principal)
    expected_revision, expected_hash = _definition_preconditions(graph)
    with Session(engine) as session:
        row = session.get(VisualProcessGraphDB, graph.id)
        if row is None:
            if graph_id is not None:
                return jsonify({"error": "not_found", "error_code": "not_found"}), 404
            try:
                write = visual_process_definition_service.create(session, graph)
            except VisualProcessDefinitionSecurityError as exc:
                session.rollback()
                return _definition_error(exc)
        else:
            try:
                previous = json.loads(row.graph_json)
            except (TypeError, ValueError):
                return jsonify({"error": "corrupt_graph_json", "error_code": "corrupt_graph_json"}), 500
            authorized, migrated = authorize_graph(previous, principal)
            if not authorized:
                status = 409 if graph_id is None else 404
                code = "resource_id_unavailable" if graph_id is None else "not_found"
                return jsonify({"error": code, "error_code": code}), status
            if migrated:
                row.graph_json = json.dumps(previous)
            _archive_graph_revision(session, row)
            try:
                write = visual_process_definition_service.replace(
                    session,
                    row,
                    graph,
                    expected_revision=expected_revision,
                    expected_hash=expected_hash,
                    require_precondition=True,
                )
            except (VisualProcessDefinitionConflict, VisualProcessDefinitionSecurityError) as exc:
                session.rollback()
                return _definition_error(exc)
        session.commit()
    response, status = _definition_save_response(write)
    response.headers["ETag"] = f'"{write.base_graph_hash}"'
    return response, status


@vp_bp.delete("/graphs/<graph_id>")
@check_user_auth
def delete_graph(graph_id: str):
    principal = _graph_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    with Session(engine) as session:
        row = session.get(VisualProcessGraphDB, graph_id)
        if not row:
            return jsonify({"error": "not_found"}), 404
        try:
            data = json.loads(row.graph_json)
        except (TypeError, ValueError):
            return jsonify({"error": "not_found"}), 404
        if not authorize_graph(data, principal)[0]:
            return jsonify({"error": "not_found"}), 404
        session.delete(row)
        session.commit()
    return "", 204
