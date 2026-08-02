from functools import wraps

from flask import Blueprint, g, request

import agent.routes.tasks.archive_admin as _archive_admin_routes
import agent.routes.tasks.vector_index_dispatch_admission as _vector_admission_route
from agent.auth import (
    admin_required,
    check_auth,
    check_strict_auth,
)
from agent.common.errors import api_response
from agent.common.logging import get_correlation_id
from agent.models import FollowupTaskCreateRequest, TaskAssignmentRequest, TaskCreateRequest, TaskUpdateRequest
from agent.routes.tasks.vector_admin_boundary import (
    guard_vector_control_mutation,
)
from agent.routes.tasks.vector_admin_boundary import (
    request_vector_authorization as _vector_authorization,
)
from agent.services.commit_metadata_inferrer import get_commit_metadata_inferrer
from agent.services.context_bundle_ingress_policy import (
    find_reserved_context_bundle_marker,
    reserved_context_bundle_ingress_error,
)
from agent.services.execution_audit_service import get_execution_audit_service
from agent.services.governance_read_model_service import get_governance_read_model_service
from agent.services.instruction_layer_service import get_instruction_layer_service
from agent.services.repository_registry import get_repository_registry
from agent.services.request_cancellation_service import get_request_cancellation_service
from agent.services.retrieval_vector_scope_ingress_policy import (
    find_reserved_retrieval_vector_scope_marker,
    reserved_retrieval_vector_scope_ingress_error,
)
from agent.services.service_registry import get_core_services
from agent.services.vector_index_task_ingress_policy import (
    find_reserved_vector_index_marker,
    reserved_vector_index_ingress_error,
)
from agent.utils import rate_limit, validate_request

management_bp = Blueprint("tasks_management", __name__)
_archive_admin_routes.register_archive_admin_routes(management_bp)
_vector_admission_route.register_vector_index_dispatch_admission_route(
    management_bp
)

# Preserve names exported by the original monolithic route module.
list_archived_tasks = _archive_admin_routes.list_archived_tasks
archive_task_route = _archive_admin_routes.archive_task_route
archive_tasks_batch_route = (
    _archive_admin_routes.archive_tasks_batch_route
)
restore_task_route = _archive_admin_routes.restore_task_route
delete_archived_task_route = (
    _archive_admin_routes.delete_archived_task_route
)
restore_tasks_batch_route = (
    _archive_admin_routes.restore_tasks_batch_route
)
cleanup_archived_tasks_route = (
    _archive_admin_routes.cleanup_archived_tasks_route
)
archive_retention_apply_route = (
    _archive_admin_routes.archive_retention_apply_route
)
cleanup_tasks_route = _archive_admin_routes.cleanup_tasks_route
vector_index_dispatch_admission = (
    _vector_admission_route.vector_index_dispatch_admission
)
task_repo = get_repository_registry().task_repo


def _task_callback_auth(fn):
    """Accept only the narrow assignment-bound Worker result capability."""

    @wraps(fn)
    def wrapped(tid, *args, **kwargs):
        auth_header = str(request.headers.get("Authorization") or "")
        token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
        payload = request.get_json(silent=True) or {}
        assignment_id = str(payload.get("id") or payload.get("assignment_id") or "")
        if token.startswith("wrc1."):
            try:
                from agent.services.worker_result_capability_service import (
                    WorkerResultCapabilityService,
                )

                g.worker_result_capability = WorkerResultCapabilityService().verify(
                    token,
                    source_task_id=str(tid or ""),
                    assignment_id=assignment_id,
                )
            except ValueError:
                return api_response(status="error", message="unauthorized", code=401)
            return fn(tid, *args, **kwargs)
        return api_response(
            status="error",
            message="worker_result_capability_required",
            code=401,
        )

    return wrapped


def _actor_username() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "system")


def _intervene_task(tid: str, action: str) -> tuple[bool, str, dict]:
    return get_core_services().task_admin_service.intervene_task(
        task_id=tid,
        action=action,
        actor=_actor_username(),
        vector_authorization=_vector_authorization(),
    )


def _parse_bool_query(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


@management_bp.route("/goals/<gid>/governance", methods=["GET"])
@check_auth
def goal_governance(gid: str):
    """
    Zentrales Governance-Read-Model fuer ein Goal (GRM-020).
    Zuschnitt fuer Nicht-Admins (GRM-022).
    """
    user = getattr(g, "user", {}) or {}
    role = user.get("role", "user")
    is_admin = role == "admin"

    summary = get_governance_read_model_service().get_summary(gid, include_details=is_admin)
    if not summary:
        return api_response(status="error", message="not_found", code=404)

    return api_response(data=summary)


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
    payload = request.get_json(silent=True) or {}
    reserved_marker = find_reserved_vector_index_marker(payload)
    if reserved_marker:
        result = reserved_vector_index_ingress_error(reserved_marker)
        return api_response(
            status="error",
            message=result["error"],
            data=result["data"],
            code=result["code"],
        )
    reserved_scope_marker = (
        find_reserved_retrieval_vector_scope_marker(payload)
    )
    if reserved_scope_marker:
        result = reserved_retrieval_vector_scope_ingress_error(
            reserved_scope_marker
        )
        return api_response(
            status="error",
            message=result["error"],
            data=result["data"],
            code=result["code"],
        )
    reserved_context_marker = find_reserved_context_bundle_marker(payload)
    if reserved_context_marker:
        result = reserved_context_bundle_ingress_error(reserved_context_marker)
        return api_response(
            status="error",
            message=result["error"],
            data=result["data"],
            code=result["code"],
        )
    data: TaskCreateRequest = g.validated_data
    source = str(payload.get("source") or "ui").strip().lower()
    created_by = str(payload.get("created_by") or "unknown").strip()
    if data.commit_metadata is None and str(data.task_kind or "").strip().lower() in ("coding", "ops", ""):
        data = data.model_copy(update={
            "commit_metadata": get_commit_metadata_inferrer().infer(
                description=str(data.description or ""),
                task_kind=data.task_kind,
                title=str(data.title or ""),
            )
        })
    result = get_core_services().task_management_service.create_task(data=data, source=source, created_by=created_by)
    if result.get("error"):
        return api_response(
            status="error",
            message=result["error"],
            data=result.get("data"),
            code=result.get("code", 400),
        )
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
    task = get_core_services().task_runtime_service.get_local_task_status(tid)
    if not task:
        return api_response(status="error", message="not_found", code=404)
    task = dict(task)
    task["instruction_layers"] = get_instruction_layer_service().task_selection_summary(task)
    return api_response(data=task)


@management_bp.route("/tasks/<tid>/workspace/files", methods=["GET"])
@check_auth
@admin_required
def task_workspace_files_route(tid):
    tracked_only = _parse_bool_query(request.args.get("tracked_only"), default=True)
    try:
        max_entries = int(request.args.get("max_entries", 2000))
    except (TypeError, ValueError):
        max_entries = 2000
    max_entries = max(1, min(max_entries, 10000))

    payload = get_core_services().task_query_service.task_workspace_files(
        task_id=tid,
        tracked_only=tracked_only,
        max_entries=max_entries,
    )
    if not payload:
        return api_response(status="error", message="not_found", code=404)
    return api_response(data=payload)


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
    payload = request.get_json(silent=True) or {}
    reserved_marker = find_reserved_vector_index_marker(payload)
    if reserved_marker:
        result = reserved_vector_index_ingress_error(reserved_marker)
        return api_response(
            status="error",
            message=result["error"],
            data=result["data"],
            code=result["code"],
        )
    reserved_scope_marker = (
        find_reserved_retrieval_vector_scope_marker(payload)
    )
    if reserved_scope_marker:
        result = reserved_retrieval_vector_scope_ingress_error(
            reserved_scope_marker
        )
        return api_response(
            status="error",
            message=result["error"],
            data=result["data"],
            code=result["code"],
        )
    reserved_context_marker = find_reserved_context_bundle_marker(payload)
    if reserved_context_marker:
        result = reserved_context_bundle_ingress_error(reserved_context_marker)
        return api_response(
            status="error",
            message=result["error"],
            data=result["data"],
            code=result["code"],
        )
    data: TaskUpdateRequest = g.validated_data
    result = get_core_services().task_management_service.patch_task(task_id=tid, data=data)
    if result.get("error"):
        return api_response(
            status="error",
            message=result["error"],
            data=result.get("data"),
            code=result.get("code", 400),
        )
    return api_response(data=result["data"])


@management_bp.route("/tasks/<tid>/review", methods=["POST"])
@check_auth
def review_task_proposal(tid):
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip().lower()
    comment = str(payload.get("comment") or "").strip() or None
    if action not in {"approve", "reject"}:
        return api_response(status="error", message="invalid_review_action", code=400)

    result = get_core_services().task_management_service.review_task_proposal(
        task_id=tid,
        action=action,
        comment=comment,
        vector_authorization=_vector_authorization(),
    )
    if result.get("error"):
        return api_response(
            status="error",
            message=result["error"],
            data=result.get("data"),
            code=result.get("code", 400),
        )
    data = dict(result.get("data") or {})
    get_execution_audit_service().emit_approval_event(
        trace_id=get_correlation_id() or None,
        task_id=tid,
        goal_id=str(data.get("goal_id") or "").strip() or None,
        action=action,
        approver_identity=_actor_username(),
        approval_scope=str(data.get("review_scope") or "task"),
        approval_source="task_review_endpoint",
        write_allowed=action == "approve",
        actor_role="hub",
        details={"comment_present": bool(comment)},
    )
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
    result = get_core_services().task_management_service.assign_task(
        task_id=tid,
        data=data,
        vector_authorization=_vector_authorization(),
    )
    if result.get("error"):
        return api_response(
            status="error",
            message=result["error"],
            data=result.get("data"),
            code=result.get("code", 400),
        )
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
        vector_authorization=_vector_authorization(),
    )
    if result.get("error"):
        return api_response(
            status="error",
            message=result["error"],
            data=result.get("data"),
            code=result.get("code", 400),
        )
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
    result = get_core_services().task_management_service.unassign_task(
        task_id=tid,
        vector_authorization=_vector_authorization(),
    )
    if result.get("error"):
        return api_response(
            status="error",
            message=result["error"],
            data=result.get("data"),
            code=result.get("code", 400),
        )
    return api_response(data=result["data"])


@management_bp.route("/tasks/<tid>/pause", methods=["POST"])
@check_auth
def pause_task(tid):
    ok, msg, data = _intervene_task(tid, "pause")
    if not ok:
        code = (
            404
            if msg == "not_found"
            else int((data or {}).get("http_status") or 400)
        )
        return api_response(status="error", message=msg, data=data or None, code=code)
    return api_response(data=data)


@management_bp.route("/tasks/<tid>/resume", methods=["POST"])
@check_auth
def resume_task(tid):
    ok, msg, data = _intervene_task(tid, "resume")
    if not ok:
        code = (
            404
            if msg == "not_found"
            else int((data or {}).get("http_status") or 400)
        )
        return api_response(status="error", message=msg, data=data or None, code=code)
    return api_response(data=data)


@management_bp.route("/tasks/<tid>/cancel", methods=["POST"])
@check_strict_auth
def cancel_task(tid):
    ok, msg, data = _intervene_task(tid, "cancel")
    if not ok:
        code = (
            404
            if msg == "not_found"
            else int((data or {}).get("http_status") or 400)
        )
        return api_response(status="error", message=msg, data=data or None, code=code)
    return api_response(data=data)


@management_bp.route("/tasks/<tid>/retry", methods=["POST"])
@check_strict_auth
def retry_task(tid):
    ok, msg, data = _intervene_task(tid, "retry")
    if not ok:
        code = (
            404
            if msg == "not_found"
            else int((data or {}).get("http_status") or 400)
        )
        return api_response(status="error", message=msg, data=data or None, code=code)
    return api_response(data=data)


@management_bp.route("/tasks/<tid>/kill-requests", methods=["POST"])
@admin_required
def kill_task_requests(tid):
    """Abort in-flight provider requests for one task across hub and workers."""
    task_id = str(tid or "").strip()
    if not task_id:
        return api_response(status="error", message="task_id required", code=400)
    vector_error = guard_vector_control_mutation(task_id)
    if vector_error is not None:
        return vector_error
    result = get_request_cancellation_service().cancel_task_requests(task_id=task_id, include_workers=True)
    return api_response(data=result)


@management_bp.route("/internal/tasks/<tid>/kill-requests", methods=["POST"])
@check_strict_auth
def kill_task_requests_internal(tid):
    """Internal fanout endpoint: abort in-flight provider requests for one task locally."""
    task_id = str(tid or "").strip()
    if not task_id:
        return api_response(status="error", message="task_id required", code=400)
    vector_error = guard_vector_control_mutation(task_id)
    if vector_error is not None:
        return vector_error
    result = get_request_cancellation_service().cancel_task_requests(task_id=task_id, include_workers=False)
    return api_response(data=result)


@management_bp.route(
    "/internal/tasks/<tid>/recovery-dispatch-admission",
    methods=["POST"],
)
@rate_limit(
    limit=240,
    window=60,
    namespace="recovery_dispatch_admission",
)
def recovery_dispatch_admission(tid):
    """Validate the opaque, task-scoped capability sent to one Worker.

    The lease itself is the credential for this narrow endpoint.  The Hub
    stores only its digest, so neither a Worker database nor logs contain a
    reusable Hub service credential.
    """

    from agent.services.recovery_dispatch_gate_service import (
        get_recovery_dispatch_gate_service,
    )

    token = str(
        request.headers.get(
            "X-Ananta-Recovery-Dispatch-Lease"
        )
        or ""
    ).strip()
    auth_header = str(
        request.headers.get("Authorization") or ""
    ).strip()
    worker_token = (
        auth_header.removeprefix("Bearer ").strip()
        if auth_header.startswith("Bearer ")
        else ""
    )
    worker_url = str(
        request.headers.get("X-Ananta-Worker-Url") or ""
    ).strip()
    payload = request.get_json(silent=True) or {}
    phase = str(payload.get("phase") or "").strip().lower()
    request_fingerprint = str(
        payload.get("request_fingerprint") or ""
    ).strip()
    decision = (
        get_recovery_dispatch_gate_service().admit_dispatch_lease(
            str(tid or ""),
            token=token,
            phase=phase,
            worker_url=worker_url,
            worker_token=worker_token,
            request_fingerprint=request_fingerprint,
        )
    )
    data = {
        "allowed": bool(decision.allowed),
        "reason_code": decision.reason_code,
        "source_task_id": decision.source_task_id,
        "plan_id": decision.plan_id,
        "release_epoch": decision.release_epoch,
    }
    if not decision.allowed:
        return api_response(
            status="error",
            message="recovery dispatch denied",
            data=data,
            code=409,
        )
    return api_response(data=data)


@management_bp.route("/tasks/<tid>/subtask-callback", methods=["POST"])
@_task_callback_auth
def subtask_callback(tid):
    payload = request.get_json() or {}
    proposal_results: list[dict] = []
    capability = getattr(g, "worker_result_capability", None)
    if payload.get("task_proposals") is not None:
        if not isinstance(capability, dict):
            return api_response(status="error", message="worker_result_capability_required", code=403)
        try:
            from agent.services.worker_task_proposal_result_adapter import (
                ingest_callback_task_proposals,
            )

            proposal_results = ingest_callback_task_proposals(
                source_task_id=tid,
                callback_payload=payload,
                capability_claims=capability,
            )
        except ValueError as exc:
            return api_response(status="error", message=str(exc), code=409)
    try:
        from agent.services.worker_result_callback_service import (
            WorkerResultCallbackError,
            WorkerResultCallbackService,
        )

        callback_result = WorkerResultCallbackService().accept(
            task_id=tid,
            payload=payload,
            capability_claims=capability,
        )
    except WorkerResultCallbackError as exc:
        return api_response(
            status="error",
            message=exc.reason_code,
            data={"reason_code": exc.reason_code},
            code=409,
        )
    result = {"data": callback_result}
    if result.get("error"):
        return api_response(
            status="error",
            message=result["error"],
            data=result.get("data"),
            code=result.get("code", 400),
        )
    return api_response(data={**result["data"], "task_proposals": proposal_results})


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
    result = get_core_services().task_management_service.create_followups(
        task_id=tid,
        data=data,
        vector_authorization=_vector_authorization(),
    )
    if result.get("error"):
        return api_response(
            status="error",
            message=result["error"],
            data=result.get("data"),
            code=result.get("code", 400),
        )
    return api_response(data=result["data"])
