"""Hub-side resolution of visual-process bindings for chat profiles/sessions."""

from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from typing import Any

from sqlmodel import Session

from agent.database import engine
from agent.db_models.visual_process import VisualProcessGraphDB
from agent.services.workflow_backend import WorkflowSignal
from agent.services.workflow_control_composition import get_workflow_backend_control_facade
from agent.services.workflow_route_authorization_service import WorkflowRoutePrincipal
from agent.visual_process.blueprint_mapper import graph_to_workflow_request
from agent.visual_process.models import VisualProcessGraph
from agent.visual_process.validator import VisualProcessValidator


def normalize_process_ref(raw: Any) -> dict[str, str] | None:
    if raw in (None, ""):
        return None
    if not isinstance(raw, dict):
        raise ValueError("process_ref_must_be_object_or_null")
    graph_id = str(raw.get("graph_id") or "").strip()
    if not graph_id:
        raise ValueError("process_graph_id_required")
    return {
        "graph_id": graph_id,
        "version": str(raw.get("version") or "latest").strip(),
    }


def process_ref_from_fields(data: dict[str, Any]) -> dict[str, str] | None:
    if "process_ref" in data:
        return normalize_process_ref(data.get("process_ref"))
    definition_id = str(data.get("process_definition_id") or "").strip()
    if not definition_id:
        return None
    return normalize_process_ref(
        {
            "graph_id": definition_id,
            "version": data.get("process_version") or data.get("process_version_policy") or "latest",
        }
    )


def load_graph(graph_id: str, version: str = "latest") -> dict[str, Any] | None:
    lookup_id = graph_id if not version or version == "latest" else f"{graph_id}@{version}"
    with Session(engine) as db:
        row = db.get(VisualProcessGraphDB, lookup_id)
        if row is None and lookup_id != graph_id:
            current = db.get(VisualProcessGraphDB, graph_id)
            if current is not None:
                try:
                    current_data = json.loads(current.graph_json)
                    row = current if str(current_data.get("version") or "1.0") == version else None
                except (TypeError, ValueError):
                    row = None
    if row is None:
        return None
    return json.loads(row.graph_json)


def resolve_effective_process(session: dict[str, Any], profile: dict[str, Any] | None) -> dict[str, Any]:
    own = session.get("process_ref") or process_ref_from_fields(dict(session.get("settings_delta") or {}))
    inherited = (profile or {}).get("process_ref") or process_ref_from_fields(
        dict((profile or {}).get("settings") or {})
    )
    process_ref = own if own is not None else inherited
    source = "session_override" if own is not None else "profile" if inherited is not None else "global"
    graph = (
        load_graph(str((process_ref or {}).get("graph_id") or ""), str((process_ref or {}).get("version") or "latest"))
        if process_ref
        else None
    )
    return {
        "process_ref": process_ref,
        "source": source,
        "graph": graph,
        "run": copy.deepcopy(session.get("process_run") or None),
    }


def graph_snapshot_hash(graph: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(graph, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def start_session_process(
    *,
    session_id: str,
    graph: dict[str, Any],
    message_id: str = "",
    tenant_id: str = "",
    subject_id: str = "",
) -> dict[str, Any]:
    definition = VisualProcessGraph.model_validate(graph)
    validation = VisualProcessValidator().validate(definition)
    if not validation.valid:
        raise ValueError("invalid_process_definition")
    snapshot_hash = graph_snapshot_hash(graph)
    request = graph_to_workflow_request(
        definition,
        workflow_type="chat_session_process",
        policy_scope={"source": "chat_session", "session_id": session_id},
        requested_by=str(subject_id or "chat_session_hub"),
    )
    principal = WorkflowRoutePrincipal(
        tenant_id=str(tenant_id or f"chat-session:{session_id}"),
        subject=str(subject_id or "chat_session_hub"),
    )
    status = get_workflow_backend_control_facade().bind(principal).start_workflow(request)
    return {
        "workflow_id": status.get("workflow_id"),
        "run_id": status.get("workflow_id"),
        "process_id": definition.id,
        "process_version": definition.version,
        "snapshot_hash": snapshot_hash,
        "graph_snapshot": copy.deepcopy(graph),
        "status": status.get("status"),
        "message_id": message_id,
        "control_principal": {
            "tenant_id": principal.tenant_id,
            "subject_id": principal.subject,
        },
        "started_at": time.time(),
    }


def runtime_overlay(run: dict[str, Any]) -> dict[str, Any]:
    workflow_id = str(run.get("workflow_id") or "")
    status = _controlled_backend(run).get_workflow_status(workflow_id)
    steps: dict[str, dict[str, Any]] = {}
    state_aliases = {"done": "succeeded", "success": "succeeded", "canceled": "cancelled"}
    for item in status.get("steps") or []:
        if not isinstance(item, dict) or not item.get("step_id"):
            continue
        raw_state = str(item.get("run_state") or item.get("status") or "pending")
        state = state_aliases.get(raw_state, raw_state)
        if state not in {"pending", "running", "awaiting_approval", "succeeded", "failed", "skipped", "cancelled"}:
            state = "pending"
        clean = {key: value for key, value in item.items() if "secret" not in key and "credential" not in key}
        clean.update({"step_id": str(item["step_id"]), "status": state})
        steps[str(item["step_id"])] = clean
    missing_runtime = status.get("status") == "not_found"
    return {
        "run_id": str(run.get("run_id") or workflow_id),
        "workflow_id": workflow_id,
        "process_id": run.get("process_id"),
        "process_version": run.get("process_version"),
        "snapshot_hash": run.get("snapshot_hash"),
        "overall_status": "degraded" if missing_runtime else status.get("status", "unknown"),
        "current_step_ids": [
            key for key, value in steps.items() if value["status"] in {"running", "awaiting_approval"}
        ],
        "step_states": steps,
        "steps": steps,
        "started_at": run.get("started_at"),
        "finished_at": status.get("finished_at"),
        "updated_at": time.time(),
        "error": "runtime_status_not_retained" if missing_runtime else status.get("error"),
        "graph_snapshot": copy.deepcopy(run.get("graph_snapshot")),
        "message_id": run.get("message_id"),
    }


def signal_session_gate(*, run: dict[str, Any], step_id: str, decision: str, actor: str) -> dict[str, Any]:
    if decision not in {"approve", "reject"}:
        raise ValueError("invalid_gate_decision")
    overlay = runtime_overlay(run)
    step = overlay["steps"].get(step_id)
    if not step or step.get("status") != "awaiting_approval":
        raise ValueError("gate_not_awaiting_approval")
    return _controlled_backend(run).signal_workflow(
        str(run["workflow_id"]),
        WorkflowSignal(
            name=decision,
            payload={"step_id": step_id, "requested_actor": str(actor)[:160]},
            actor=actor,
        ),
    )


def _controlled_backend(run: dict[str, Any]):
    raw_principal = run.get("control_principal")
    principal = dict(raw_principal) if isinstance(raw_principal, dict) else {}
    workflow_id = str(run.get("workflow_id") or run.get("run_id") or "")
    route_principal = WorkflowRoutePrincipal(
        tenant_id=str(principal.get("tenant_id") or f"chat-workflow:{workflow_id}"),
        subject=str(principal.get("subject_id") or "chat_session_hub"),
    )
    return get_workflow_backend_control_facade().bind(route_principal)


def clone_graph(graph_id: str, *, owner_session_id: str) -> dict[str, Any]:
    graph = load_graph(graph_id)
    if graph is None:
        raise LookupError("process_graph_not_found")
    clone = copy.deepcopy(graph)
    clone_id = f"vp-session-{uuid.uuid4().hex[:10]}"
    clone["id"] = clone_id
    clone["name"] = f"{clone.get('name') or graph_id} · {owner_session_id}"
    metadata = dict(clone.get("metadata") or {})
    metadata.update({"cloned_from": graph_id, "owner_session_id": owner_session_id})
    clone["metadata"] = metadata
    now = time.time()
    row = VisualProcessGraphDB(
        id=clone_id,
        name=str(clone["name"]),
        description=str(clone.get("description") or ""),
        tags=",".join(clone.get("tags") or []),
        graph_json=json.dumps(clone),
        created_at=now,
        updated_at=now,
    )
    with Session(engine) as db:
        db.add(row)
        db.commit()
    return clone
