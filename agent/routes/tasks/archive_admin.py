"""HTTP routes for Hub-owned task archive and retention administration."""

from __future__ import annotations

import time
from typing import Any

from flask import Blueprint, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.routes.tasks.task_read_access import (
    task_read_access_context,
    task_read_error_response,
)
from agent.routes.tasks.vector_admin_boundary import (
    request_vector_authorization,
    vector_permission_error,
)
from agent.services.service_registry import get_core_services
from agent.services.task_admin_service import (
    RecoveryChildAdminMutationConflict,
)
from agent.services.task_read_access_service import TaskReadAccessError


def _parse_status_filters(raw: object) -> set[str]:
    return get_core_services().task_admin_service.parse_status_filters(
        raw
    )


def _filters(
    data: dict[str, Any],
    *,
    allow_age: bool,
) -> tuple[set[str], str, float | None, set[str]]:
    statuses = _parse_status_filters(data.get("statuses"))
    team_id = str(data.get("team_id") or "").strip()
    before_timestamp = data.get("before_timestamp")
    before_ts = (
        float(before_timestamp)
        if before_timestamp is not None
        else None
    )
    if (
        before_ts is None
        and allow_age
        and data.get("older_than_seconds") is not None
    ):
        before_ts = time.time() - float(
            data["older_than_seconds"]
        )
    raw_ids = data.get("task_ids") or []
    task_ids = {
        str(item).strip()
        for item in raw_ids
        if str(item).strip()
    }
    return statuses, team_id, before_ts, task_ids


def _conflict_error(exc: RecoveryChildAdminMutationConflict):
    return api_response(
        status="error",
        message=exc.reason_code,
        data=exc.as_data(),
        code=409,
    )


def _uniform_batch_error(
    *,
    errors: list[dict],
    response_data: dict[str, Any],
    mutated_ids: list[Any],
    allow_conflict: bool,
):
    if mutated_ids or not errors:
        return None
    candidates = ((403, "vector_store_admin_required"),)
    if allow_conflict:
        candidates += ((409, "task_admin_conflict"),)
    for status_code, default_reason in candidates:
        matching = [
            item
            for item in errors
            if int(item.get("http_status") or 0) == status_code
        ]
        if len(matching) == len(errors):
            return api_response(
                status="error",
                message=str(
                    matching[0].get("reason_code")
                    or default_reason
                ),
                data=response_data,
                code=status_code,
            )
    return None


@check_auth
def list_archived_tasks():
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    try:
        data = (
            get_core_services()
            .task_query_service.list_archived_tasks(
                limit=limit,
                offset=offset,
                access=task_read_access_context(),
            )
        )
    except TaskReadAccessError as exc:
        return task_read_error_response(exc)
    return api_response(data=data)


@check_auth
def archive_task_route(tid: str):
    try:
        archived = (
            get_core_services()
            .task_admin_service.archive_task(
                task_id=tid,
                vector_authorization=request_vector_authorization(),
            )
        )
    except PermissionError as exc:
        return vector_permission_error(exc)
    except RecoveryChildAdminMutationConflict as exc:
        return _conflict_error(exc)
    if not archived:
        return api_response(
            status="error",
            message="not_found",
            code=404,
        )
    return api_response(status="archived", data={"id": tid})


@check_auth
def archive_tasks_batch_route():
    data = request.get_json(silent=True) or {}
    statuses, team_id, before_ts, task_ids = _filters(
        data,
        allow_age=False,
    )
    if not (statuses or team_id or task_ids or before_ts is not None):
        return api_response(
            status="error",
            message="archive_filter_required",
            code=400,
        )
    try:
        archived_ids = (
            get_core_services()
            .task_admin_service.archive_tasks(
                statuses=statuses,
                team_id=team_id,
                before_ts=before_ts,
                task_ids=task_ids,
                vector_authorization=request_vector_authorization(),
            )
        )
    except PermissionError as exc:
        return vector_permission_error(exc)
    except RecoveryChildAdminMutationConflict as exc:
        return _conflict_error(exc)
    return api_response(
        data={
            "archived_count": len(archived_ids),
            "archived_ids": archived_ids,
        }
    )


@check_auth
def restore_task_route(tid: str):
    try:
        restored = (
            get_core_services()
            .task_admin_service.restore_task(
                task_id=tid,
                vector_authorization=request_vector_authorization(),
            )
        )
    except PermissionError as exc:
        return vector_permission_error(exc)
    except RecoveryChildAdminMutationConflict as exc:
        return _conflict_error(exc)
    if not restored:
        return api_response(
            status="error",
            message="not_found",
            code=404,
        )
    return api_response(status="restored", data={"id": tid})


@check_auth
def delete_archived_task_route(tid: str):
    try:
        deleted = (
            get_core_services()
            .task_admin_service.delete_archived_task(
                task_id=tid,
                vector_authorization=request_vector_authorization(),
            )
        )
    except PermissionError as exc:
        return vector_permission_error(exc)
    except RecoveryChildAdminMutationConflict as exc:
        return _conflict_error(exc)
    if not deleted:
        return api_response(
            status="error",
            message="not_found",
            code=404,
        )
    return api_response(
        data={"deleted_count": 1, "deleted_ids": [tid]}
    )


@check_auth
def restore_tasks_batch_route():
    data = request.get_json(silent=True) or {}
    statuses, team_id, before_ts, task_ids = _filters(
        data,
        allow_age=False,
    )
    if not (statuses or team_id or task_ids or before_ts is not None):
        return api_response(
            status="error",
            message="restore_filter_required",
            code=400,
        )
    try:
        restored_ids = (
            get_core_services()
            .task_admin_service.restore_tasks(
                statuses=statuses,
                team_id=team_id,
                before_ts=before_ts,
                task_ids=task_ids,
                vector_authorization=request_vector_authorization(),
            )
        )
    except PermissionError as exc:
        return vector_permission_error(exc)
    except RecoveryChildAdminMutationConflict as exc:
        return _conflict_error(exc)
    return api_response(
        data={
            "restored_count": len(restored_ids),
            "restored_ids": restored_ids,
        }
    )


@check_auth
def cleanup_archived_tasks_route():
    data = request.get_json(silent=True) or {}
    statuses, team_id, before_ts, task_ids = _filters(
        data,
        allow_age=True,
    )
    if not (statuses or team_id or before_ts is not None or task_ids):
        return api_response(
            status="error",
            message="cleanup_filter_required",
            code=400,
        )
    deleted_ids, errors = (
        get_core_services()
        .task_admin_service.cleanup_archived_tasks(
            statuses=statuses,
            team_id=team_id,
            before_ts=before_ts,
            task_ids=task_ids,
            vector_authorization=request_vector_authorization(),
        )
    )
    response_data = {
        "matched_count": len(deleted_ids) + len(errors),
        "deleted_count": len(deleted_ids),
        "deleted_ids": deleted_ids,
        "errors": errors,
    }
    failure = _uniform_batch_error(
        errors=errors,
        response_data=response_data,
        mutated_ids=deleted_ids,
        allow_conflict=True,
    )
    return failure or api_response(data=response_data)


@check_auth
def archive_retention_apply_route():
    data = request.get_json(silent=True) or {}
    team_id = str(data.get("team_id") or "").strip()
    statuses = _parse_status_filters(data.get("statuses"))
    retain_seconds = float(data.get("retain_seconds") or 0)
    if retain_seconds <= 0:
        return api_response(
            status="error",
            message="retain_seconds_required",
            code=400,
        )
    cutoff = time.time() - retain_seconds
    deleted_ids, errors = (
        get_core_services()
        .task_admin_service.apply_archive_retention_with_errors(
            team_id=team_id,
            statuses=statuses,
            cutoff=cutoff,
            vector_authorization=request_vector_authorization(),
        )
    )
    response_data = {
        "deleted_count": len(deleted_ids),
        "deleted_ids": deleted_ids,
        "errors": errors,
        "cutoff": cutoff,
    }
    failure = _uniform_batch_error(
        errors=errors,
        response_data=response_data,
        mutated_ids=deleted_ids,
        allow_conflict=False,
    )
    return failure or api_response(data=response_data)


@check_auth
def cleanup_tasks_route():
    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode") or "archive").strip().lower()
    if mode not in {"archive", "delete"}:
        return api_response(
            status="error",
            message="invalid_mode",
            code=400,
        )
    statuses, team_id, before_ts, task_ids = _filters(
        data,
        allow_age=True,
    )
    if not (statuses or team_id or before_ts is not None or task_ids):
        return api_response(
            status="error",
            message="cleanup_filter_required",
            code=400,
        )
    matched, archived_ids, deleted_ids, errors = (
        get_core_services()
        .task_admin_service.cleanup_active_tasks(
            mode=mode,
            statuses=statuses,
            team_id=team_id,
            before_ts=before_ts,
            task_ids=task_ids,
            vector_authorization=request_vector_authorization(),
        )
    )
    response_data = {
        "mode": mode,
        "matched_count": len(matched),
        "archived_count": len(archived_ids),
        "deleted_count": len(deleted_ids),
        "archived_ids": archived_ids,
        "deleted_ids": deleted_ids,
        "errors": errors,
    }
    failure = _uniform_batch_error(
        errors=errors,
        response_data=response_data,
        mutated_ids=[*archived_ids, *deleted_ids],
        allow_conflict=True,
    )
    return failure or api_response(data=response_data)


def register_archive_admin_routes(blueprint: Blueprint) -> None:
    """Attach routes to the established blueprint to keep endpoint names."""

    routes = (
        ("/tasks/archived", list_archived_tasks, ["GET"]),
        ("/tasks/<tid>/archive", archive_task_route, ["POST"]),
        ("/tasks/archive/batch", archive_tasks_batch_route, ["POST"]),
        (
            "/tasks/archived/<tid>/restore",
            restore_task_route,
            ["POST"],
        ),
        (
            "/tasks/archived/<tid>",
            delete_archived_task_route,
            ["DELETE"],
        ),
        (
            "/tasks/archived/restore/batch",
            restore_tasks_batch_route,
            ["POST"],
        ),
        (
            "/tasks/archived/cleanup",
            cleanup_archived_tasks_route,
            ["POST"],
        ),
        (
            "/tasks/archive/retention/apply",
            archive_retention_apply_route,
            ["POST"],
        ),
        ("/tasks/cleanup", cleanup_tasks_route, ["POST"]),
    )
    for rule, view_func, methods in routes:
        blueprint.add_url_rule(
            rule,
            endpoint=view_func.__name__,
            view_func=view_func,
            methods=methods,
        )


__all__ = ["register_archive_admin_routes"]
