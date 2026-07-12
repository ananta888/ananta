"""Hub-side resolution of visual-process bindings for chat profiles/sessions."""
from __future__ import annotations

import copy
import json
import time
import uuid
import hashlib
from typing import Any

from sqlmodel import Session

from agent.database import engine
from agent.db_models.visual_process import VisualProcessGraphDB
from agent.services.workflow_backend_factory import get_workflow_backend
from agent.services.workflow_backend import WorkflowSignal
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


def load_graph(graph_id: str) -> dict[str, Any] | None:
    with Session(engine) as db:
        row = db.get(VisualProcessGraphDB, graph_id)
    if row is None:
        return None
    return json.loads(row.graph_json)


def resolve_effective_process(session: dict[str, Any], profile: dict[str, Any] | None) -> dict[str, Any]:
    own = session.get("process_ref")
    inherited = (profile or {}).get("process_ref")
    process_ref = own if own is not None else inherited
    source = "session_override" if own is not None else "profile" if inherited is not None else "global"
    graph = load_graph(str((process_ref or {}).get("graph_id") or "")) if process_ref else None
    return {
        "process_ref": process_ref,
        "source": source,
        "graph": graph,
        "run": copy.deepcopy(session.get("process_run") or None),
    }


def graph_snapshot_hash(graph: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(graph, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def start_session_process(*, session_id: str, graph: dict[str, Any], message_id: str = "") -> dict[str, Any]:
    definition = VisualProcessGraph.model_validate(graph)
    validation = VisualProcessValidator().validate(definition)
    if not validation.valid:
        raise ValueError("invalid_process_definition")
    snapshot_hash = graph_snapshot_hash(graph)
    request = graph_to_workflow_request(
        definition,
        workflow_type="chat_session_process",
        policy_scope={"source": "chat_session", "session_id": session_id},
        requested_by="chat_session_hub",
    )
    status = get_workflow_backend().start_workflow(request)
    return {
        "workflow_id": status.get("workflow_id"),
        "run_id": status.get("workflow_id"),
        "process_id": definition.id,
        "process_version": definition.version,
        "snapshot_hash": snapshot_hash,
        "status": status.get("status"),
        "message_id": message_id,
        "started_at": time.time(),
    }


def runtime_overlay(run: dict[str, Any]) -> dict[str, Any]:
    workflow_id = str(run.get("workflow_id") or "")
    status = get_workflow_backend().get_workflow_status(workflow_id)
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
    return {
        "run_id": str(run.get("run_id") or workflow_id),
        "workflow_id": workflow_id,
        "process_id": run.get("process_id"),
        "process_version": run.get("process_version"),
        "snapshot_hash": run.get("snapshot_hash"),
        "overall_status": status.get("status", "unknown"),
        "current_step_ids": [key for key, value in steps.items() if value["status"] in {"running", "awaiting_approval"}],
        "step_states": steps,
        "steps": steps,
        "started_at": run.get("started_at"),
        "finished_at": status.get("finished_at"),
        "updated_at": time.time(),
        "error": status.get("error"),
    }


def signal_session_gate(*, run: dict[str, Any], step_id: str, decision: str, actor: str) -> dict[str, Any]:
    if decision not in {"approve", "reject"}:
        raise ValueError("invalid_gate_decision")
    overlay = runtime_overlay(run)
    step = overlay["steps"].get(step_id)
    if not step or step.get("status") != "awaiting_approval":
        raise ValueError("gate_not_awaiting_approval")
    return get_workflow_backend().signal_workflow(
        str(run["workflow_id"]),
        WorkflowSignal(name=decision, payload={"step_id": step_id}, actor=actor),
    )


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
