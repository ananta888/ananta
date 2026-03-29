import time

from flask import Blueprint, g, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.models import FollowupTaskCreateRequest, TaskAssignmentRequest, TaskCreateRequest, TaskUpdateRequest
from agent.repository import archived_task_repo, task_repo
from agent.routes.tasks.status import normalize_task_status
from agent.services.task_runtime_service import get_local_task_status
from agent.services.service_registry import get_core_services
from agent.utils import rate_limit, validate_request

management_bp = Blueprint("tasks_management", __name__)


def _parse_status_filters(raw: object) -> set[str]:
    return get_core_services().task_admin_service.parse_status_filters(raw)


def _task_matches_filters(task: dict, statuses: set[str], team_id: str, before_ts: float | None, task_ids: set[str]) -> bool:
    return get_core_services().task_admin_service.task_matches_filters(
        task,
        statuses=statuses,
        team_id=team_id,
        before_ts=before_ts,
        task_ids=task_ids,
    )


def _load_all_archived_tasks() -> list[dict]:
    return get_core_services().task_admin_service.load_all_archived_tasks()


def _build_task_tree(root_id: str, include_archived: bool, max_depth: int) -> dict | None:
    return get_core_services().task_admin_service.build_task_tree(
        root_id=root_id,
        include_archived=include_archived,
        max_depth=max_depth,
    )


def _actor_username() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "system")


def _intervene_task(tid: str, action: str) -> tuple[bool, str, dict]:
    return get_core_services().task_admin_service.intervene_task(task_id=tid, action=action, actor=_actor_username())


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
    status_filter = str(request.args.get("status") or "")
    agent_filter = request.args.get("agent")
    since_filter = request.args.get("since", type=float)
    until_filter = request.args.get("until", type=float)
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    return api_response(
        data=get_core_services().task_query_service.list_tasks(
            status_filter=status_filter,
            agent_filter=agent_filter,
            since_filter=since_filter,
            until_filter=until_filter,
            limit=limit,
            offset=offset,
        )
    )


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

    return api_response(
        data=get_core_services().task_query_service.timeline(
            team_id_filter=team_id_filter,
            agent_filter=agent_filter,
            status_filter=status_filter,
            error_only=error_only,
            since_filter=since_filter,
            limit=limit,
        )
    )


@management_bp.route("/tasks/archived", methods=["GET"])
@check_auth
def list_archived_tasks():
    """
    Archivierte Tasks auflisten
    """
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    return api_response(data=get_core_services().task_query_service.list_archived_tasks(limit=limit, offset=offset))


@management_bp.route("/tasks/<tid>/archive", methods=["POST"])
@check_auth
def archive_task_route(tid):
    """
    Task archivieren
    """
    if not get_core_services().task_admin_service.archive_task(task_id=tid):
        return api_response(status="error", message="not_found", code=404)
    return api_response(status="archived", data={"id": tid})


@management_bp.route("/tasks/archive/batch", methods=["POST"])
@check_auth
def archive_tasks_batch_route():
    data = request.get_json(silent=True) or {}
    statuses = _parse_status_filters(data.get("statuses"))
    team_id = str(data.get("team_id") or "").strip()
    raw_ids = data.get("task_ids") or []
    task_ids = {str(item).strip() for item in raw_ids if str(item).strip()}
    before_ts = data.get("before_timestamp")
    before_ts = float(before_ts) if before_ts is not None else None
    if not (statuses or team_id or task_ids or before_ts is not None):
        return api_response(status="error", message="archive_filter_required", code=400)

    archived_ids = get_core_services().task_admin_service.archive_tasks(
        statuses=statuses,
        team_id=team_id,
        before_ts=before_ts,
        task_ids=task_ids,
    )
    return api_response(data={"archived_count": len(archived_ids), "archived_ids": archived_ids})


@management_bp.route("/tasks/archived/<tid>/restore", methods=["POST"])
@check_auth
def restore_task_route(tid):
    """
    Archivierten Task wiederherstellen
    """
    if not get_core_services().task_admin_service.restore_task(task_id=tid):
        return api_response(status="error", message="not_found", code=404)
    return api_response(status="restored", data={"id": tid})


@management_bp.route("/tasks/archived/<tid>", methods=["DELETE"])
@check_auth
def delete_archived_task_route(tid):
    result = get_core_services().task_query_service.delete_archived_task(task_id=tid)
    if not result:
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=result)


@management_bp.route("/tasks/archived/restore/batch", methods=["POST"])
@check_auth
def restore_tasks_batch_route():
    data = request.get_json(silent=True) or {}
    statuses = _parse_status_filters(data.get("statuses"))
    team_id = str(data.get("team_id") or "").strip()
    raw_ids = data.get("task_ids") or []
    task_ids = {str(item).strip() for item in raw_ids if str(item).strip()}
    before_ts = data.get("before_timestamp")
    before_ts = float(before_ts) if before_ts is not None else None
    if not (statuses or team_id or task_ids or before_ts is not None):
        return api_response(status="error", message="restore_filter_required", code=400)

    restored_ids = get_core_services().task_admin_service.restore_tasks(
        statuses=statuses,
        team_id=team_id,
        before_ts=before_ts,
        task_ids=task_ids,
    )
    return api_response(data={"restored_count": len(restored_ids), "restored_ids": restored_ids})


@management_bp.route("/tasks/archived/cleanup", methods=["POST"])
@check_auth
def cleanup_archived_tasks_route():
    """
    Batch-Cleanup fuer archivierte Tasks (hart loeschen).
    Filter: statuses, team_id, before_timestamp, older_than_seconds, task_ids
    """
    data = request.get_json(silent=True) or {}
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

    deleted_ids, errors = get_core_services().task_admin_service.cleanup_archived_tasks(
        statuses=statuses,
        team_id=team_id,
        before_ts=before_ts,
        task_ids=task_ids,
    )

    return api_response(data={"matched_count": len(deleted_ids) + len(errors), "deleted_count": len(deleted_ids), "deleted_ids": deleted_ids, "errors": errors})


@management_bp.route("/tasks/archive/retention/apply", methods=["POST"])
@check_auth
def archive_retention_apply_route():
    data = request.get_json(silent=True) or {}
    team_id = str(data.get("team_id") or "").strip()
    statuses = _parse_status_filters(data.get("statuses"))
    retain_seconds = float(data.get("retain_seconds") or 0)
    now = time.time()
    if retain_seconds <= 0:
        return api_response(status="error", message="retain_seconds_required", code=400)
    cutoff = now - retain_seconds
    deleted_ids = get_core_services().task_admin_service.apply_archive_retention(
        team_id=team_id,
        statuses=statuses,
        cutoff=cutoff,
    )
    return api_response(data={"deleted_count": len(deleted_ids), "deleted_ids": deleted_ids, "cutoff": cutoff})


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

    matched, archived_ids, deleted_ids, errors = get_core_services().task_admin_service.cleanup_active_tasks(
        mode=mode,
        statuses=statuses,
        team_id=team_id,
        before_ts=before_ts,
        task_ids=task_ids,
    )

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
    tree = get_core_services().task_query_service.task_tree(
        root_id=tid,
        include_archived=include_archived,
        max_depth=max_depth,
        task_admin_service=get_core_services().task_admin_service,
    )
    if not tree:
        return api_response(status="error", message="not_found", code=404)
    return api_response(data={"root_task_id": tid, "include_archived": include_archived, "tree": tree})


@management_bp.route("/tasks/hierarchy/view/<tid>", methods=["GET"])
@check_auth
def task_hierarchy_view(tid):
    include_archived = str(request.args.get("include_archived", "1")).strip().lower() in {"1", "true", "yes"}
    max_depth = max(1, min(int(request.args.get("max_depth", 10)), 50))
    data = get_core_services().task_query_service.task_hierarchy_view(
        root_id=tid,
        include_archived=include_archived,
        max_depth=max_depth,
        task_admin_service=get_core_services().task_admin_service,
    )
    if not data:
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=data)


@management_bp.route("/tasks/derivation/backfill", methods=["POST"])
@check_auth
def task_derivation_backfill_route():
    return api_response(data=get_core_services().task_management_service.derivation_backfill())


@management_bp.route("/tasks", methods=["POST"])
@check_auth
@rate_limit(limit=20, window=60, namespace="tasks_create")
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
    source = str((request.get_json(silent=True) or {}).get("source") or "ui").strip().lower()
    created_by = str((request.get_json(silent=True) or {}).get("created_by") or "unknown").strip()
    result = get_core_services().task_management_service.create_task(data=data, source=source, created_by=created_by)
    if result.get("error"):
        return api_response(status="error", message=result["error"], code=result.get("code", 400))
    return api_response(data=result["data"], code=result.get("code", 201))


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
    task = get_local_task_status(tid)
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
    result = get_core_services().task_management_service.patch_task(task_id=tid, data=data)
    if result.get("error"):
        return api_response(status="error", message=result["error"], code=result.get("code", 400))
    return api_response(data=result["data"])


@management_bp.route("/tasks/<tid>/review", methods=["POST"])
@check_auth
def review_task_proposal(tid):
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip().lower()
    comment = str(payload.get("comment") or "").strip() or None
    if action not in {"approve", "reject"}:
        return api_response(status="error", message="invalid_review_action", code=400)

    result = get_core_services().task_management_service.review_task_proposal(task_id=tid, action=action, comment=comment)
    if result.get("error"):
        return api_response(status="error", message=result["error"], code=result.get("code", 400))
    return api_response(data=result["data"])


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
    if not data.agent_url:
        return api_response(status="error", message="agent_url_required", code=400)
    result = get_core_services().task_management_service.assign_task(task_id=tid, data=data)
    if result.get("error"):
        return api_response(status="error", message=result["error"], data=result.get("data"), code=result.get("code", 400))
    return api_response(data=result["data"])


@management_bp.route("/tasks/<tid>/assign/auto", methods=["POST"])
@check_auth
def auto_assign_task(tid):
    payload = request.get_json(silent=True) or {}
    result = get_core_services().task_management_service.auto_assign_task(
        task_id=tid,
        payload=payload,
        agent_registry_service=get_core_services().agent_registry_service,
        worker_contract_service=get_core_services().worker_contract_service,
    )
    if result.get("error"):
        return api_response(status="error", message=result["error"], data=result.get("data"), code=result.get("code", 400))
    return api_response(data=result["data"])


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
    result = get_core_services().task_management_service.unassign_task(task_id=tid)
    if result.get("error"):
        return api_response(status="error", message=result["error"], code=result.get("code", 400))
    return api_response(data=result["data"])


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
    result = get_core_services().task_management_service.subtask_callback(task_id=tid, payload=request.get_json() or {})
    if result.get("error"):
        return api_response(status="error", message=result["error"], code=result.get("code", 400))
    return api_response(data=result["data"])


@management_bp.route("/tasks/<tid>/followups", methods=["POST"])
@check_auth
@validate_request(FollowupTaskCreateRequest)
def create_followups(tid):
    """
    Erzeugt Folgeaufgaben fuer einen bestehenden Task (mit einfacher Duplikatvermeidung).
    Child-Tasks werden standardmaessig als blocked erstellt und vom Autopilot freigegeben,
    sobald der Parent auf completed wechselt.
    """
    data: FollowupTaskCreateRequest = g.validated_data
    result = get_core_services().task_management_service.create_followups(task_id=tid, data=data)
    if result.get("error"):
        return api_response(status="error", message=result["error"], code=result.get("code", 400))
    return api_response(data=result["data"])
