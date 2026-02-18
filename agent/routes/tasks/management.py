import uuid
import logging
from flask import Blueprint, request, g
from agent.auth import check_auth
from agent.common.errors import api_response
from agent.repository import task_repo, archived_task_repo
from agent.db_models import TaskDB
from agent.utils import validate_request, rate_limit
from agent.models import TaskDelegationRequest, TaskCreateRequest, TaskUpdateRequest, TaskAssignmentRequest
from agent.models import FollowupTaskCreateRequest
from agent.routes.tasks.utils import _update_local_task_status, _forward_to_worker, _get_local_task_status
from agent.routes.tasks.status import normalize_task_status, expand_task_status_query_values
from agent.common.api_envelope import unwrap_api_envelope
from agent.metrics import TASK_RECEIVED
from agent.config import settings
import time

management_bp = Blueprint("tasks_management", __name__)


def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _followup_exists(parent_task_id: str, description: str) -> bool:
    norm = _normalize_text(description)
    if not norm:
        return False
    for t in task_repo.get_all():
        if t.parent_task_id != parent_task_id:
            continue
        if _normalize_text(t.description or "") == norm:
            return True
    return False


def _normalize_depends_on(depends_on: list[str] | None, tid: str | None = None) -> list[str]:
    vals = []
    for item in (depends_on or []):
        if not item:
            continue
        dep = str(item).strip()
        if not dep:
            continue
        if tid and dep == tid:
            continue
        if dep not in vals:
            vals.append(dep)
    return vals


def _effective_dependencies(task: dict) -> list[str]:
    deps = _normalize_depends_on(task.get("depends_on"), tid=task.get("id"))
    parent = task.get("parent_task_id")
    if parent and parent not in deps and parent != task.get("id"):
        deps.append(parent)
    return deps


def _has_cycle(graph: dict[str, list[str]]) -> bool:
    state: dict[str, int] = {}

    def _dfs(node: str) -> bool:
        color = state.get(node, 0)
        if color == 1:
            return True
        if color == 2:
            return False
        state[node] = 1
        for nxt in graph.get(node, []):
            if nxt in graph and _dfs(nxt):
                return True
        state[node] = 2
        return False

    return any(_dfs(n) for n in graph if state.get(n, 0) == 0)


def _validate_dependencies_and_cycles(tid: str, depends_on: list[str]) -> tuple[bool, str]:
    by_id = {t.id: t for t in task_repo.get_all()}
    missing = [d for d in depends_on if d not in by_id]
    if missing:
        return False, f"missing_dependencies:{','.join(missing)}"

    graph: dict[str, list[str]] = {}
    for task in by_id.values():
        task_dict = task.model_dump()
        graph[task.id] = _effective_dependencies(task_dict)
    graph[tid] = _normalize_depends_on(depends_on, tid=tid)
    if _has_cycle(graph):
        return False, "dependency_cycle_detected"
    return True, ""


def _task_timeline_events(task: dict) -> list[dict]:
    tid = task.get("id")
    team_id = task.get("team_id")
    status = task.get("status")
    events: list[dict] = [
        {
            "event_type": "task_created",
            "task_id": tid,
            "team_id": team_id,
            "task_status": status,
            "timestamp": task.get("created_at"),
            "actor": task.get("assigned_agent_url") or "system",
            "details": {
                "title": task.get("title"),
                "description": task.get("description"),
                "parent_task_id": task.get("parent_task_id"),
            },
        }
    ]

    for item in task.get("history", []) or []:
        if not isinstance(item, dict):
            continue
        event_type = item.get("event_type") or "task_activity"
        events.append(
            {
                "event_type": event_type,
                "task_id": tid,
                "team_id": team_id,
                "task_status": status,
                "timestamp": item.get("timestamp") or task.get("updated_at"),
                "actor": item.get("delegated_to") or task.get("assigned_agent_url") or "system",
                "details": item,
            }
        )

    proposal = task.get("last_proposal") or {}
    if proposal:
        events.append(
            {
                "event_type": "proposal_snapshot",
                "task_id": tid,
                "team_id": team_id,
                "task_status": status,
                "timestamp": task.get("updated_at"),
                "actor": task.get("assigned_agent_url") or "system",
                "details": proposal,
            }
        )

    if task.get("last_output") or task.get("last_exit_code") is not None:
        events.append(
            {
                "event_type": "execution_result",
                "task_id": tid,
                "team_id": team_id,
                "task_status": status,
                "timestamp": task.get("updated_at"),
                "actor": task.get("assigned_agent_url") or "system",
                "details": {
                    "exit_code": task.get("last_exit_code"),
                    "output_preview": (task.get("last_output") or "")[:220],
                    "quality_gate_failed": "[quality_gate] failed:" in (task.get("last_output") or ""),
                },
            }
        )

    if task.get("parent_task_id"):
        events.append(
            {
                "event_type": "task_handoff",
                "task_id": tid,
                "team_id": team_id,
                "task_status": status,
                "timestamp": task.get("created_at"),
                "actor": "system",
                "details": {"parent_task_id": task.get("parent_task_id"), "reason": "followup_or_delegation"},
            }
        )

    return events


def _is_error_timeline_event(event: dict) -> bool:
    event_type = str(event.get("event_type") or "").lower()
    details = event.get("details") or {}
    if event_type in {
        "tool_guardrail_blocked",
        "autopilot_security_policy_blocked",
        "autopilot_worker_failed",
        "quality_gate_failed",
    }:
        return True

    if isinstance(details, dict):
        if details.get("blocked_reasons"):
            return True
        exit_code = details.get("exit_code")
        if exit_code not in (None, 0):
            return True
        text = __import__("json").dumps(details, ensure_ascii=False).lower()
        if ("failed" in text) or ("error" in text):
            return True
    return False


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
        task_events = _task_timeline_events(task)
        for ev in task_events:
            ts = ev.get("timestamp") or 0
            if since_filter and ts < since_filter:
                continue
            if agent_filter and ev.get("actor") != agent_filter:
                continue
            if error_only:
                if not _is_error_timeline_event(ev):
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
    safe_data["depends_on"] = _normalize_depends_on(safe_data.get("depends_on"), tid=tid)
    ok, reason = _validate_dependencies_and_cycles(tid, safe_data.get("depends_on") or [])
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
        update_data["depends_on"] = _normalize_depends_on(update_data.get("depends_on"), tid=tid)
        ok, reason = _validate_dependencies_and_cycles(tid, update_data.get("depends_on") or [])
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


@management_bp.route("/tasks/<tid>/delegate", methods=["POST"])
@check_auth
@validate_request(TaskDelegationRequest)
def delegate_task(tid):
    """
    Task an anderen Agenten delegieren
    ---
    parameters:
      - name: tid
        in: path
        type: string
        required: true
      - in: body
        name: body
        schema:
          $ref: '#/definitions/TaskDelegationRequest'
    responses:
      200:
        description: Delegiert
    """
    data: TaskDelegationRequest = g.validated_data
    parent_task = _get_local_task_status(tid)
    if not parent_task:
        return api_response(status="error", message="parent_task_not_found", code=404)

    subtask_id = f"sub-{uuid.uuid4()}"

    my_url = settings.agent_url or f"http://localhost:{settings.port}"
    callback_url = f"{my_url.rstrip('/')}/tasks/{tid}/subtask-callback"

    delegation_payload = {
        "id": subtask_id,
        "description": data.subtask_description,
        "parent_task_id": tid,
        "priority": data.priority,
        "callback_url": callback_url,
        "callback_token": settings.api_token,
    }

    try:
        res = _forward_to_worker(data.agent_url, "/tasks", delegation_payload, token=data.agent_token)
        res = unwrap_api_envelope(res)

        subtasks = parent_task.get("subtasks", [])
        subtasks.append(
            {
                "id": subtask_id,
                "agent_url": data.agent_url,
                "description": data.subtask_description,
                "status": "created",
            }
        )
        _update_local_task_status(
            tid,
            parent_task.get("status", "in_progress"),
            subtasks=subtasks,
            event_type="task_delegated",
            event_actor="system",
            event_details={"delegated_to": data.agent_url, "subtask_id": subtask_id},
        )

        return api_response(
            data={"status": "delegated", "subtask_id": subtask_id, "agent_url": data.agent_url, "response": res}
        )
    except Exception as e:
        logging.error(f"Delegation an {data.agent_url} fehlgeschlagen: {e}")
        return api_response(status="error", message="delegation_failed", data={"details": str(e)}, code=502)


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
        if _followup_exists(tid, desc):
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


def _active_lease_for_task(task: dict) -> dict | None:
    now = time.time()
    for item in reversed(task.get("history", []) or []):
        if not isinstance(item, dict):
            continue
        if item.get("event_type") != "task_claimed":
            continue
        details = item.get("details") or {}
        lease_until = float(details.get("lease_until") or 0)
        if lease_until > now:
            return details
    return None


@management_bp.route("/tasks/orchestration/ingest", methods=["POST"])
@check_auth
def ingest_task():
    payload = request.get_json(silent=True) or {}
    description = str(payload.get("description") or "").strip()
    if not description:
        return api_response(status="error", message="description_required", code=400)
    tid = str(payload.get("id") or f"tsk-{uuid.uuid4()}")
    source = str(payload.get("source") or "ui").strip().lower()
    created_by = str(payload.get("created_by") or "unknown").strip()
    priority = str(payload.get("priority") or "medium")
    _update_local_task_status(
        tid,
        normalize_task_status(str(payload.get("status") or "todo"), default="todo"),
        title=str(payload.get("title") or "")[:200] or None,
        description=description,
        priority=priority,
        event_type="task_ingested",
        event_actor=created_by or "unknown",
        event_details={"source": source, "channel": "central_task_management"},
    )
    return api_response(data={"id": tid, "ingested": True, "source": source})


@management_bp.route("/tasks/orchestration/claim", methods=["POST"])
@check_auth
def claim_task():
    payload = request.get_json(silent=True) or {}
    tid = str(payload.get("task_id") or "").strip()
    agent_url = str(payload.get("agent_url") or "").strip()
    idempotency_key = str(payload.get("idempotency_key") or "").strip()
    if not tid or not agent_url:
        return api_response(status="error", message="task_id_and_agent_url_required", code=400)
    task = _get_local_task_status(tid)
    if not task:
        return api_response(status="error", message="not_found", code=404)
    lease = _active_lease_for_task(task)
    if lease and lease.get("agent_url") != agent_url:
        return api_response(status="error", message="task_already_leased", data={"lease": lease}, code=409)
    lease_seconds = max(10, min(int(payload.get("lease_seconds") or 120), 3600))
    lease_until = time.time() + lease_seconds
    _update_local_task_status(
        tid,
        "assigned",
        assigned_agent_url=agent_url,
        event_type="task_claimed",
        event_actor=agent_url,
        event_details={"agent_url": agent_url, "lease_until": lease_until, "idempotency_key": idempotency_key},
    )
    return api_response(data={"task_id": tid, "claimed": True, "lease_until": lease_until})


@management_bp.route("/tasks/orchestration/complete", methods=["POST"])
@check_auth
def complete_task():
    payload = request.get_json(silent=True) or {}
    tid = str(payload.get("task_id") or "").strip()
    if not tid:
        return api_response(status="error", message="task_id_required", code=400)
    task = _get_local_task_status(tid)
    if not task:
        return api_response(status="error", message="not_found", code=404)
    gate = payload.get("gate_results") or {}
    all_passed = bool(gate.get("passed", False))
    final_status = "completed" if all_passed else "failed"
    _update_local_task_status(
        tid,
        final_status,
        last_output=str(payload.get("output") or ""),
        last_exit_code=0 if all_passed else 1,
        event_type="task_completed_with_gates",
        event_actor=str(payload.get("actor") or "system"),
        event_details={"gate_results": gate, "trace_id": payload.get("trace_id")},
    )
    return api_response(data={"task_id": tid, "status": final_status, "gates_passed": all_passed})


@management_bp.route("/tasks/orchestration/read-model", methods=["GET"])
@check_auth
def orchestration_read_model():
    tasks = [t.model_dump() for t in task_repo.get_all()]
    queue = {"todo": 0, "assigned": 0, "in_progress": 0, "blocked": 0, "completed": 0, "failed": 0}
    by_agent: dict[str, int] = {}
    by_source: dict[str, int] = {"ui": 0, "agent": 0, "system": 0, "unknown": 0}
    leases: list[dict] = []
    for task in tasks:
        status = normalize_task_status(task.get("status"), default="todo")
        queue[status] = int(queue.get(status, 0)) + 1
        agent = task.get("assigned_agent_url")
        if agent:
            by_agent[agent] = int(by_agent.get(agent, 0)) + 1
        history = task.get("history") or []
        if history:
            first_ingest = next((h for h in history if isinstance(h, dict) and h.get("event_type") == "task_ingested"), None)
            source = str(((first_ingest or {}).get("details") or {}).get("source") or "unknown").lower()
            by_source[source if source in by_source else "unknown"] += 1
        lease = _active_lease_for_task(task)
        if lease:
            leases.append({"task_id": task.get("id"), **lease})
    recent = sorted(tasks, key=lambda t: float(t.get("updated_at") or 0), reverse=True)[:40]
    return api_response(
        data={
            "queue": queue,
            "by_agent": by_agent,
            "by_source": by_source,
            "active_leases": leases,
            "recent_tasks": [
                {
                    "id": t.get("id"),
                    "title": t.get("title"),
                    "status": t.get("status"),
                    "priority": t.get("priority"),
                    "assigned_agent_url": t.get("assigned_agent_url"),
                    "updated_at": t.get("updated_at"),
                }
                for t in recent
            ],
            "ts": time.time(),
        }
    )
