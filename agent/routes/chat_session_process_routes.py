"""Chat session workflow-process binding, run and gate-signal endpoints."""

from __future__ import annotations

import time

from flask import (
    jsonify,
    request,
)

from agent.auth import check_user_auth
from agent.routes.chat_blueprint import chat_bp
from agent.routes.chat_route_access import (
    _chat_workflow_principal,
    _chat_workflow_run_is_owned_by,
    _log,
    _owned_session,
    _public_process_payload,
    _serialized_chat_mutation,
)
from agent.routes.chat_route_dependencies import chat_route_dependencies
from agent.routes.chat_route_persistence import (
    _load_chat,
    _profile_by_id,
    _save_chat,
)
from agent.services.chat_process_binding import (
    clone_graph,
)
from agent.services.chat_process_binding import (
    public_graph as public_process_graph,
)
from agent.services.chat_session_security import (
    GateCommand,
    chat_session_mutation_lock,
    find_gate_action,
    mark_stale_gate_action_for_manual_reconciliation,
)


@chat_bp.get("/sessions/<session_id>/process")
@check_user_auth
def get_effective_session_process(session_id: str):
    dependencies = chat_route_dependencies()
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    with chat_session_mutation_lock:
        chat = _load_chat(principal=principal)
        session, migrated = _owned_session(chat, session_id, principal)
        if migrated:
            _save_chat(chat, principal=principal)
    if session is None:
        return jsonify({"error": "session_not_found"}), 404
    profile = _profile_by_id(str(session.get("profile_id") or "general"), principal)
    result = dependencies.resolve_effective_process(
        session,
        profile,
        tenant_id=principal.tenant_id,
        subject_id=principal.subject_id,
    )
    if result["process_ref"] and result["graph"] is None:
        return jsonify({**result, "error": "process_graph_not_found"}), 404
    return jsonify(_public_process_payload(result))


@chat_bp.post("/sessions/<session_id>/process/clone")
@check_user_auth
@_serialized_chat_mutation
def clone_effective_session_process(session_id: str):
    dependencies = chat_route_dependencies()
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    chat = _load_chat(principal=principal)
    session, _ = _owned_session(chat, session_id, principal)
    if session is None:
        return jsonify({"error": "session_not_found"}), 404
    profile = _profile_by_id(str(session.get("profile_id") or "general"), principal)
    effective = dependencies.resolve_effective_process(
        session,
        profile,
        tenant_id=principal.tenant_id,
        subject_id=principal.subject_id,
    )
    graph_id = str((effective.get("process_ref") or {}).get("graph_id") or "")
    if not graph_id:
        return jsonify({"error": "process_not_configured"}), 409
    try:
        graph = clone_graph(
            graph_id,
            owner_session_id=session_id,
            tenant_id=principal.tenant_id,
            subject_id=principal.subject_id,
        )
    except LookupError:
        return jsonify({"error": "process_graph_not_found"}), 404
    session["process_ref"] = {"graph_id": graph["id"], "version": str(graph.get("version") or "1.0")}
    _save_chat(chat, principal=principal)
    return jsonify(
        {
            "process_ref": session["process_ref"],
            "graph": public_process_graph(graph),
            "source": "session_override",
        }
    ), 201


@chat_bp.get("/sessions/<session_id>/process/runs")
@check_user_auth
def list_session_process_runs(session_id: str):
    dependencies = chat_route_dependencies()
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    with chat_session_mutation_lock:
        chat = _load_chat(principal=principal)
        session, migrated = _owned_session(chat, session_id, principal)
        if migrated:
            _save_chat(chat, principal=principal)
    if session is None:
        return jsonify({"error": "session_not_found", "error_code": "session_not_found"}), 404
    runs = sorted(
        (
            item
            for item in session.get("process_runs") or []
            if isinstance(item, dict) and _chat_workflow_run_is_owned_by(item, principal)
        ),
        key=lambda item: item.get("started_at", 0),
        reverse=True,
    )
    summaries = []
    for item in runs:
        summary = {key: value for key, value in item.items() if key != "graph_snapshot"}
        summary["status"] = dependencies.runtime_overlay(item)["overall_status"]
        summaries.append(summary)
    return jsonify(summaries)


@chat_bp.post("/sessions/<session_id>/process/runs")
@check_user_auth
@_serialized_chat_mutation
def start_session_process_run(session_id: str):
    dependencies = chat_route_dependencies()
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    chat = _load_chat(principal=principal)
    session, _ = _owned_session(chat, session_id, principal)
    if session is None:
        return jsonify({"error": "session_not_found", "error_code": "session_not_found"}), 404
    effective = dependencies.resolve_effective_process(
        session,
        _profile_by_id(str(session.get("profile_id") or "general"), principal),
        tenant_id=principal.tenant_id,
        subject_id=principal.subject_id,
    )
    if effective.get("graph") is None:
        return jsonify({"error": "process_not_configured", "error_code": "process_not_configured"}), 409
    body = request.get_json(silent=True) or {}
    try:
        run = dependencies.start_session_process(
            session_id=session_id,
            graph=effective["graph"],
            message_id=str(body.get("message_id") or ""),
            tenant_id=principal.tenant_id,
            subject_id=principal.subject_id,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc), "error_code": str(exc)}), 422
    runs = list(session.get("process_runs") or [])
    runs.append(run)
    session["process_runs"] = runs[-20:]
    _save_chat(chat, principal=principal)
    return jsonify(_public_process_payload(run)), 201


@chat_bp.get("/sessions/<session_id>/process/runs/<run_id>")
@check_user_auth
def get_session_process_run(session_id: str, run_id: str):
    dependencies = chat_route_dependencies()
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    with chat_session_mutation_lock:
        chat = _load_chat(principal=principal)
        session, migrated = _owned_session(chat, session_id, principal)
        if migrated:
            _save_chat(chat, principal=principal)
    if session is None:
        return jsonify({"error": "session_not_found", "error_code": "session_not_found"}), 404
    run = next(
        (
            item
            for item in session.get("process_runs") or []
            if isinstance(item, dict)
            and str(item.get("run_id")) == run_id
            and _chat_workflow_run_is_owned_by(item, principal)
        ),
        None,
    )
    if run is None:
        return jsonify({"error": "process_run_not_found", "error_code": "process_run_not_found"}), 404
    return jsonify(_public_process_payload(dependencies.runtime_overlay(run)))


@chat_bp.post("/sessions/<session_id>/process/runs/<run_id>/gate")
@check_user_auth
@_serialized_chat_mutation
def signal_session_process_run_gate(session_id: str, run_id: str):
    dependencies = chat_route_dependencies()
    principal = _chat_workflow_principal()
    if principal is None:
        return jsonify({"error": "forbidden", "error_code": "forbidden"}), 403
    chat = _load_chat(principal=principal)
    session, _ = _owned_session(chat, session_id, principal)
    if session is None:
        return jsonify({"error": "session_not_found", "error_code": "session_not_found"}), 404
    run = next(
        (
            item
            for item in session.get("process_runs") or []
            if isinstance(item, dict)
            and str(item.get("run_id")) == run_id
            and _chat_workflow_run_is_owned_by(item, principal)
        ),
        None,
    )
    if run is None:
        return jsonify({"error": "process_run_not_found", "error_code": "process_run_not_found"}), 404
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "invalid_request_body", "error_code": "invalid_request_body"}), 400
    raw_idempotency_key = request.headers.get("Idempotency-Key")
    if raw_idempotency_key is None:
        raw_idempotency_key = body.get("idempotency_key")
    workflow_id = str(run.get("workflow_id") or "")
    persisted_run_id = str(run.get("run_id") or workflow_id)
    try:
        command = GateCommand.from_values(
            idempotency_key=raw_idempotency_key,
            principal=principal,
            session_id=session_id,
            workflow_id=workflow_id,
            run_id=persisted_run_id,
            step_id=body.get("step_id"),
            decision=body.get("decision"),
        )
    except ValueError as exc:
        reason_code = str(exc)
        return jsonify({"error": reason_code, "error_code": reason_code}), 400
    actions = list(session.get("process_gate_actions") or [])
    previous = find_gate_action(actions, command)
    if previous is not None:
        if previous.get("request_hash") != command.request_hash:
            return jsonify({"error": "idempotency_key_reused", "error_code": "idempotency_key_reused"}), 409
        if mark_stale_gate_action_for_manual_reconciliation(previous, now=time.time()):
            _save_chat(chat, principal=principal)
        state = previous.get("state")
        if state == "applied":
            return jsonify({"status": "already_applied", "action": previous}), 200
        reason_code = (
            "idempotency_request_in_progress"
            if state == "pending"
            else str(previous.get("error_code") or "gate_manual_reconcile_required")
        )
        return jsonify({"error": reason_code, "error_code": reason_code}), 409

    action = command.action(state="pending", created_at=time.time())
    actions.append(action)
    # This is an at-most-once ledger, not a display history. It must remain
    # non-evicting while the owning chat exists.
    session["process_gate_actions"] = actions
    # Reserve the exact principal/run/payload fingerprint before the external
    # workflow signal. A crash can leave a fail-closed pending reservation but
    # can never make a concurrent request signal the gate twice.
    if not _save_chat(chat, principal=principal):
        return jsonify(
            {
                "error": "gate_idempotency_persistence_failed",
                "error_code": "gate_idempotency_persistence_failed",
            }
        ), 503
    try:
        result = dependencies.signal_session_gate(
            run=run,
            step_id=command.step_id,
            decision=command.decision,
            actor=principal.subject_id,
        )
    except ValueError as exc:
        action["state"] = "rejected"
        action["error_code"] = str(exc)
        action["updated_at"] = time.time()
        if not _save_chat(chat, principal=principal):
            action["state"] = "manual_reconcile_required"
            action["error_code"] = "gate_signal_outcome_unknown"
            _save_chat(chat, principal=principal)
            return jsonify(
                {"error": "gate_manual_reconcile_required", "error_code": "gate_manual_reconcile_required"}
            ), 503
        return jsonify({"error": str(exc), "error_code": str(exc)}), 409
    except Exception:  # noqa: BLE001 - preserve a fail-closed replay record
        action["state"] = "failed"
        action["error_code"] = "gate_signal_failed"
        action["updated_at"] = time.time()
        if not _save_chat(chat, principal=principal):
            action["state"] = "manual_reconcile_required"
            action["error_code"] = "gate_signal_outcome_unknown"
            _save_chat(chat, principal=principal)
        _log.exception("chat process gate signal failed workflow_id=%s", workflow_id)
        return jsonify({"error": "gate_signal_failed", "error_code": "gate_signal_failed"}), 503
    action["state"] = "applied"
    action["updated_at"] = time.time()
    action["result_status"] = str(result.get("status") or "") if isinstance(result, dict) else ""
    if not _save_chat(chat, principal=principal):
        action["state"] = "manual_reconcile_required"
        action["error_code"] = "gate_signal_outcome_unknown"
        action["updated_at"] = time.time()
        _save_chat(chat, principal=principal)
        return jsonify(
            {"error": "gate_manual_reconcile_required", "error_code": "gate_manual_reconcile_required"}
        ), 503
    _log.info(
        "chat_process_gate actor=%s workflow_id=%s step_id=%s decision=%s idempotency_key_ref=%s",
        action["actor"],
        workflow_id,
        action["step_id"],
        action["decision"],
        command.idempotency_key_ref,
    )
    return jsonify(result)
