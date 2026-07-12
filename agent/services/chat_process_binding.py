"""Hub-side resolution of visual-process bindings for chat profiles/sessions."""
from __future__ import annotations

import copy
import json
import time
import uuid
from typing import Any

from sqlmodel import Session

from agent.database import engine
from agent.db_models.visual_process import VisualProcessGraphDB


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
    source = "session" if own is not None else "profile" if inherited is not None else "none"
    graph = load_graph(str((process_ref or {}).get("graph_id") or "")) if process_ref else None
    return {
        "process_ref": process_ref,
        "source": source,
        "graph": graph,
        "run": copy.deepcopy(session.get("process_run") or None),
    }


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
