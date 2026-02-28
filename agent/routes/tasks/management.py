import time
import uuid

from flask import Blueprint, g, request

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.db_models import TaskDB
from agent.metrics import TASK_RECEIVED
from agent.models import FollowupTaskCreateRequest, TaskAssignmentRequest, TaskCreateRequest, TaskUpdateRequest
from agent.repository import archived_task_repo, task_repo
from agent.routes.tasks.dependency_policy import followup_exists, normalize_depends_on, validate_dependencies_and_cycles
from agent.routes.tasks.status import expand_task_status_query_values, normalize_task_status
from agent.routes.tasks.timeline_utils import is_error_timeline_event, task_timeline_events
from agent.routes.tasks.utils import _get_local_task_status, _update_local_task_status
from agent.utils import rate_limit, validate_request

management_bp = Blueprint("tasks_management", __name__)


def _parse_status_filters(raw: object) -> set[str]:
    if raw is None:
        return set()
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
    elif isinstance(raw, list):
        parts = [str(p).strip() for p in raw if str(p).strip()]
    else:
        parts = []
    return {normalize_task_status(p, default="") for p in parts if normalize_task_status(p, default="")}


def _task_matches_filters(task: dict, statuses: set[str], team_id: str, before_ts: float | None, task_ids: set[str]) -> bool:
    if statuses:
        if normalize_task_status(task.get("status"), default="") not in statuses:
            return False
    if team_id and (task.get("team_id") or "") != team_id:
        return False
    if before_ts is not None and float(task.get("updated_at") or task.get("created_at") or 0.0) >= before_ts:
        return False
    if task_ids and (task.get("id") or "") not in task_ids:
        return False
    return True


def _load_all_archived_tasks() -> list[dict]:
    all_archived: list[dict] = []
    limit = 500
    offset = 0
    while True:
        chunk = archived_task_repo.get_all(limit=limit, offset=offset)
        if not chunk:
            break
        all_archived.extend([item.model_dump() for item in chunk])
        if len(chunk) < limit:
            break
        offset += limit
    return all_archived


def _build_task_tree(root_id: str, include_archived: bool, max_depth: int) -> dict | None:
    active_items = [t.model_dump() for t in task_repo.get_all()]
    archived_items = _load_all_archived_tasks() if include_archived else []
    by_id: dict[str, dict] = {}
    children_by_parent: dict[str, list[str]] = {}

    for item in archived_items:
        item["_source"] = "archived"
        by_id[item["id"]] = item
    for item in active_items:
        item["_source"] = "active"
        by_id[item["id"]] = item

    for tid, item in by_id.items():
        parent_id = str(item.get("parent_task_id") or "").strip()
        if not parent_id:
            continue
        children_by_parent.setdefault(parent_id, []).append(tid)

    if root_id not in by_id:
        return None

    def _node(task_id: str, depth: int, lineage: set[str]) -> dict:
        task = dict(by_id[task_id])
        child_ids = children_by_parent.get(task_id, [])
        out = {
            "task": task,
            "depth": depth,
            "children": [],
            "children_count": len(child_ids),
        }
        if depth >= max_depth:
            out["truncated"] = True
            return out
        for child_id in child_ids:
            if child_id in lineage:
                out["children"].append({"task_id": child_id, "cycle_detected": True})
                continue
            out["children"].append(_node(child_id, depth + 1, lineage | {child_id}))
        return out

    return _node(root_id, 0, {root_id})


def _actor_username() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "system")


def _intervene_task(tid: str, action: str) -> tuple[bool, str, dict]:
    task = task_repo.get_by_id(tid)
    if not task:
        return False, "not_found", {}

    current = normalize_task_status(task.status, default="")
    new_status = current
    details: dict = {"action": action, "previous_status": current}

    if action == "pause":
        if current in {"completed", "failed", "cancelled"}:
            return False, "invalid_transition", {"current_status": current}
        new_status = "paused"
    elif action == "resume":
        if current != "paused":
            return False, "invalid_transition", {"current_status": current}
        new_status = "assigned" if task.assigned_agent_url else "todo"
    elif action == "cancel":
        if current in {"completed", "failed", "cancelled"}:
            return False, "invalid_transition", {"current_status": current}
        new_status = "cancelled"
    elif action == "retry":
        if current not in {"failed", "cancelled"}:
            return False, "invalid_transition", {"current_status": current}
        new_status = "assigned" if task.assigned_agent_url else "todo"
    else:
        return False, "invalid_action", {}

    actor = _actor_username()
    update_kwargs: dict = {}
    if action == "retry":
        update_kwargs["last_exit_code"] = None
    _update_local_task_status(
        tid,
        new_status,
        event_type="task_intervention",
        event_actor=actor,
        event_details={**details, "new_status": new_status},
        **update_kwargs,
    )
    log_audit(
        "task_intervention",
        {
            "task_id": tid,
            "action": action,
            "actor": actor,
            "previous_status": current,
            "new_status": new_status,
        },
    )
    return True, "ok", {"id": tid, "action": action, "status": new_status}


@management_bp.route("/tasks", methods=["GET"])
@check_auth
def list_tasks():
    """
    Alle Tasks auflisten (paginiert)
    ---
    responses:
      200:
        description: Liste der Tasks
    """
    status_filter = normalize_task_status(request.args.get("status"), default="")
    agent_filter = request.args.get("agent")
    since_filter = request.args.get("since", type=float)
    until_filter = request.args.get("until", type=float)
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)

    status_values = expand_task_status_query_values(status_filter)
    tasks = task_repo.get_paged(
        limit=limit,
        offset=offset,
        status=None,
        status_values=status_values or None,
        agent=agent_filter,
        since=since_filter,
        until=until_filter,
    )
    task_list = [t.model_dump() for t in tasks]

    return api_response(data=task_list)


@management_bp.route("/tasks/timeline", methods=["GET"])
@check_auth
def tasks_timeline():
    """
    Aggregierte Task-Timeline inkl. Entscheidungs-/Handoff-Spuren.
    Filter: team_id, agent, status, error_only, since, limit.
    """
    team_id_filter = request.args.get("team_id")
    agent_filter = request.args.get("agent")
    status_filter = request.args.get("status")
    error_only = request.args.get("error_only", "").lower() in {"1", "true", "yes"}
    since_filter = request.args.get("since", type=float)
    limit = max(1, min(request.args.get("limit", 200, type=int), 2000))

    events: list[dict] = []
    for t in task_repo.get_all():
        task = t.model_dump()
        if team_id_filter and (task.get("team_id") or "") != team_id_filter:
            continue
        if status_filter and normalize_task_status(task.get("status"), default="") != status_filter:
            continue
        task_events = task_timeline_events(task)
        for ev in task_events:
            ts = ev.get("timestamp") or 0
            if since_filter and ts < since_filter:
                continue
            if agent_filter and ev.get("actor") != agent_filter:
                continue
            if error_only:
                if not is_error_timeline_event(ev):
                    continue
            events.append(ev)

    events.sort(key=lambda item: item.get("timestamp") or 0, reverse=True)
    return api_response(data={"items": events[:limit], "total": len(events)})


@management_bp.route("/tasks/archived", methods=["GET"])
@check_auth
def list_archived_tasks():
    """
    Archivierte Tasks auflisten
    """
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    tasks = archived_task_repo.get_all(limit=limit, offset=offset)
    return api_response(data=[t.model_dump() for t in tasks])


@management_bp.route("/tasks/<tid>/archive", methods=["POST"])
@check_auth
def archive_task_route(tid):
    """
    Task archivieren
    """
    from agent.db_models import ArchivedTaskDB

    task = task_repo.get_by_id(tid)
    if not task:
        return api_response(status="error", message="not_found", code=404)

    # In Archiv kopieren
    archived = ArchivedTaskDB(**task.model_dump())
    archived_task_repo.save(archived)

    # Aus aktiver Tabelle löschen
    task_repo.delete(tid)

    return api_response(status="archived", data={"id": tid})


@management_bp.route("/tasks/archived/<tid>/restore", methods=["POST"])
@check_auth
def restore_task_route(tid):
    """
    Archivierten Task wiederherstellen
    """
    archived = archived_task_repo.get_by_id(tid)
    if not archived:
        return api_response(status="error", message="not_found", code=404)

    # In aktive Tabelle kopieren
    task = TaskDB(**archived.model_dump())
    # Status auf einen aktiven Status setzen, falls er auf 'archived' steht
    if task.status == "archived":
        task.status = "todo"

    task_repo.save(task)

    # Aus Archiv löschen
    archived_task_repo.delete(tid)

    return api_response(status="restored", data={"id": tid})


@management_bp.route("/tasks/cleanup", methods=["POST"])
@check_auth
def cleanup_tasks_route():
    """
    Batch-Cleanup fuer aktive Tasks:
    - mode=archive: passende Tasks ins Archiv verschieben
    - mode=delete: passende Tasks hart loeschen
    Filter: statuses, team_id, before_timestamp, older_than_seconds, task_ids
    """
    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode") or "archive").strip().lower()
    if mode not in {"archive", "delete"}:
        return api_response(status="error", message="invalid_mode", code=400)

    statuses = _parse_status_filters(data.get("statuses"))
    team_id = str(data.get("team_id") or "").strip()
    older_than_seconds = data.get("older_than_seconds")
    before_timestamp = data.get("before_timestamp")
    before_ts = None
    if before_timestamp is not None:
        before_ts = float(before_timestamp)
    elif older_than_seconds is not None:
        before_ts = time.time() - float(older_than_seconds)

    raw_ids = data.get("task_ids") or []
    task_ids = {str(item).strip() for item in raw_ids if str(item).strip()}

    if not (statuses or team_id or before_ts is not None or task_ids):
        return api_response(status="error", message="cleanup_filter_required", code=400)

    from agent.db_models import ArchivedTaskDB

    matched = []
    for task in task_repo.get_all():
        item = task.model_dump()
        if _task_matches_filters(item, statuses, team_id, before_ts, task_ids):
            matched.append(item)

    archived_ids: list[str] = []
    deleted_ids: list[str] = []
    errors: list[dict] = []

    for item in matched:
        tid = item.get("id")
        try:
            if mode == "archive":
                archived_task_repo.save(ArchivedTaskDB(**item))
                task_repo.delete(tid)
                archived_ids.append(tid)
            else:
                task_repo.delete(tid)
                deleted_ids.append(tid)
        except Exception as e:
            errors.append({"id": tid, "error": str(e)})

    return api_response(
        data={
            "mode": mode,
            "matched_count": len(matched),
            "archived_count": len(archived_ids),
            "deleted_count": len(deleted_ids),
            "archived_ids": archived_ids,
            "deleted_ids": deleted_ids,
            "errors": errors,
        }
    )


@management_bp.route("/tasks/<tid>/tree", methods=["GET"])
@check_auth
def task_tree_route(tid):
    """
    Rekursiver Ableitungsbaum fuer einen Root-Task.
    Query:
      - include_archived=1|0
      - max_depth (default 10, max 50)
    """
    include_archived = str(request.args.get("include_archived", "1")).strip().lower() in {"1", "true", "yes"}
    max_depth = max(1, min(int(request.args.get("max_depth", 10)), 50))
    tree = _build_task_tree(tid, include_archived=include_archived, max_depth=max_depth)
    if not tree:
        return api_response(status="error", message="not_found", code=404)
    return api_response(data={"root_task_id": tid, "include_archived": include_archived, "tree": tree})


@management_bp.route("/tasks", methods=["POST"])
@check_auth
@rate_limit(limit=20, window=60)
@validate_request(TaskCreateRequest)
def create_task():
    """
    Neuen Task erstellen
    ---
    parameters:
      - in: body
        name: body
        schema:
          properties:
            id:
              type: string
            description:
              type: string
    responses:
      201:
        description: Task erstellt
    """
    data: TaskCreateRequest = g.validated_data
    tid = data.id or str(uuid.uuid4())
    status = normalize_task_status(data.status, default="created")

    # Konvertiere zu dict und filtere None-Werte
    safe_data = {k: v for k, v in data.model_dump().items() if v is not None and k not in ["id", "status"]}
    safe_data["depends_on"] = normalize_depends_on(safe_data.get("depends_on"), tid=tid)
    ok, reason = validate_dependencies_and_cycles(tid, safe_data.get("depends_on") or [])
    if not ok:
        return api_response(status="error", message=reason, code=400)

    source = str((request.get_json(silent=True) or {}).get("source") or "ui").strip().lower()
    created_by = str((request.get_json(silent=True) or {}).get("created_by") or "unknown").strip()
    _update_local_task_status(
        tid,
        status,
        event_type="task_ingested",
        event_actor=created_by or "unknown",
        event_details={"source": source, "channel": "central_task_management"},
        **safe_data,
    )
    TASK_RECEIVED.inc()
    return api_response(data={"id": tid, "status": "created"}, code=201)


@management_bp.route("/tasks/<tid>", methods=["GET"])
@check_auth
def get_task(tid):
    """
    Task-Details abrufen
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
    responses:
      200:
        description: Task-Details
      404:
        description: Nicht gefunden
    """
    task = _get_local_task_status(tid)
    if not task:
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=task)


@management_bp.route("/tasks/<tid>", methods=["PATCH"])
@check_auth
@validate_request(TaskUpdateRequest)
def patch_task(tid):
    """
    Task aktualisieren
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
      - in: body
        name: body
        schema:
          properties:
            status:
              type: string
    responses:
      200:
        description: Task aktualisiert
    """
    data: TaskUpdateRequest = g.validated_data
    update_data = {k: v for k, v in data.model_dump().items() if v is not None}
    status = normalize_task_status(update_data.pop("status", None), default="updated")
    if "depends_on" in update_data:
        update_data["depends_on"] = normalize_depends_on(update_data.get("depends_on"), tid=tid)
        ok, reason = validate_dependencies_and_cycles(tid, update_data.get("depends_on") or [])
        if not ok:
            return api_response(status="error", message=reason, code=400)

    _update_local_task_status(tid, status, **update_data)
    return api_response(data={"id": tid, "status": "updated"})


@management_bp.route("/tasks/<tid>/assign", methods=["POST"])
@check_auth
@validate_request(TaskAssignmentRequest)
def assign_task(tid):
    """
    Task einem Agenten zuweisen
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
      - in: body
        name: body
        schema:
          properties:
            agent_url:
              type: string
    responses:
      200:
        description: Zugewiesen
    """
    data: TaskAssignmentRequest = g.validated_data

    _update_local_task_status(
        tid,
        "assigned",
        assigned_agent_url=data.agent_url,
        assigned_agent_token=data.token,
        event_type="task_assigned",
        event_actor="system",
        event_details={"agent_url": data.agent_url},
    )
    return api_response(data={"status": "assigned", "agent_url": data.agent_url})


@management_bp.route("/tasks/<tid>/unassign", methods=["POST"])
@check_auth
def unassign_task(tid):
    """
    Zuweisung aufheben
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
    responses:
      200:
        description: Zuweisung aufgehoben
    """
    task = _get_local_task_status(tid)
    if not task:
        return api_response(status="error", message="not_found", code=404)

    _update_local_task_status(tid, "todo", assigned_agent_url=None, assigned_agent_token=None, assigned_to=None)
    return api_response(data={"status": "todo", "unassigned": True})


@management_bp.route("/tasks/<tid>/pause", methods=["POST"])
@check_auth
def pause_task(tid):
    ok, msg, data = _intervene_task(tid, "pause")
    if not ok:
        code = 404 if msg == "not_found" else 400
        return api_response(status="error", message=msg, data=data or None, code=code)
    return api_response(data=data)


@management_bp.route("/tasks/<tid>/resume", methods=["POST"])
@check_auth
def resume_task(tid):
    ok, msg, data = _intervene_task(tid, "resume")
    if not ok:
        code = 404 if msg == "not_found" else 400
        return api_response(status="error", message=msg, data=data or None, code=code)
    return api_response(data=data)


@management_bp.route("/tasks/<tid>/cancel", methods=["POST"])
@check_auth
def cancel_task(tid):
    ok, msg, data = _intervene_task(tid, "cancel")
    if not ok:
        code = 404 if msg == "not_found" else 400
        return api_response(status="error", message=msg, data=data or None, code=code)
    return api_response(data=data)


@management_bp.route("/tasks/<tid>/retry", methods=["POST"])
@check_auth
def retry_task(tid):
    ok, msg, data = _intervene_task(tid, "retry")
    if not ok:
        code = 404 if msg == "not_found" else 400
        return api_response(status="error", message=msg, data=data or None, code=code)
    return api_response(data=data)


@management_bp.route("/tasks/<tid>/subtask-callback", methods=["POST"])
@check_auth
def subtask_callback(tid):
    data = request.get_json()
    subtask_id = data.get("id")
    new_status = data.get("status")

    if not subtask_id or not new_status:
        return api_response(status="error", message="invalid_payload", code=400)

    parent_task = _get_local_task_status(tid)
    if not parent_task:
        return api_response(status="error", message="parent_task_not_found", code=404)

    subtasks = parent_task.get("subtasks", [])
    updated = False
    for st in subtasks:
        if st.get("id") == subtask_id:
            st["status"] = new_status
            if "last_output" in data:
                st["last_output"] = data["last_output"]
            if "last_exit_code" in data:
                st["last_exit_code"] = data["last_exit_code"]
            updated = True
            break

    if updated:
        _update_local_task_status(tid, parent_task.get("status", "in_progress"), subtasks=subtasks)
        return api_response(data={"status": "updated"})
    else:
        return api_response(status="error", message="subtask_not_found", code=404)


@management_bp.route("/tasks/<tid>/followups", methods=["POST"])
@check_auth
@validate_request(FollowupTaskCreateRequest)
def create_followups(tid):
    """
    Erzeugt Folgeaufgaben fuer einen bestehenden Task (mit einfacher Duplikatvermeidung).
    Child-Tasks werden standardmaessig als blocked erstellt und vom Autopilot freigegeben,
    sobald der Parent auf completed wechselt.
    """
    parent_task = _get_local_task_status(tid)
    if not parent_task:
        return api_response(status="error", message="parent_task_not_found", code=404)

    data: FollowupTaskCreateRequest = g.validated_data
    created: list[dict] = []
    skipped: list[dict] = []
    parent_done = normalize_task_status(parent_task.get("status")) == "completed"

    for item in data.items:
        desc = (item.description or "").strip()
        if not desc:
            skipped.append({"reason": "empty_description"})
            continue
        if followup_exists(tid, desc):
            skipped.append({"description": desc, "reason": "duplicate"})
            continue

        subtask_id = f"sub-{uuid.uuid4()}"
        status = "todo" if parent_done else "blocked"
        create_payload = {
            "id": subtask_id,
            "description": desc,
            "priority": item.priority or "Medium",
            "parent_task_id": tid,
        }

        _update_local_task_status(subtask_id, status, **create_payload)
        if item.agent_url:
            _update_local_task_status(
                subtask_id,
                "assigned" if status != "blocked" else "blocked",
                assigned_agent_url=item.agent_url,
                assigned_agent_token=item.agent_token,
            )

        created.append(
            {
                "id": subtask_id,
                "status": status,
                "parent_task_id": tid,
                "description": desc,
                "assigned_agent_url": item.agent_url,
            }
        )

    return api_response(data={"parent_task_id": tid, "created": created, "skipped": skipped})
