"""Task create/patch adapter for the Angular Control-Center API."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from flask import Blueprint, current_app, request

from agent.auth import check_auth, get_authenticated_source_control_principal
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.db_models import TaskDB
from agent.routes.tasks.status import normalize_task_status
from agent.routes.tasks.vector_admin_boundary import (
    reserved_vector_mutation_response,
)
from agent.services.hub_event_service import build_task_history_event
from agent.services.project_access_authority import (
    ProjectAccessError,
    ProjectCapability,
)
from agent.services.recovery_task_mutation_policy import (
    RecoveryTaskMutationConflict,
    ensure_external_recovery_mutation_allowed,
)
from agent.services.task_runtime_service import (
    update_local_task_status,
)


class ControlCenterTaskMutationRoutes:
    """Expose task mutations through injected Control-Center projections."""

    def __init__(
        self,
        *,
        repository_provider: Callable[[], Any],
        task_serializer: Callable[[Any], dict[str, Any]],
        user_id_provider: Callable[[], str],
    ) -> None:
        self._repository_provider = repository_provider
        self._task_serializer = task_serializer
        self._user_id_provider = user_id_provider

    @check_auth
    def create_task(self):
        """B04: POST /api/tasks."""

        body = request.get_json(silent=True) or {}
        vector_error = reserved_vector_mutation_response(body)
        if vector_error is not None:
            return vector_error
        title = str(body.get("title") or "").strip()
        if not title:
            return api_response(
                status="error",
                message="title_required",
                code=400,
            )

        project_id = str(body.get("project_id") or "").strip()
        project_scope = None
        principal = None
        if project_id:
            principal = get_authenticated_source_control_principal()
            authority = current_app.extensions.get("project_access_authority")
            if authority is None:
                return api_response(
                    status="error",
                    message="project_access_authority_unavailable",
                    code=503,
                )
            try:
                project_scope = authority.require(
                    tenant_id=str(principal.tenant_id or ""),
                    project_id=project_id,
                    subject_id=principal.subject_id,
                    capability=ProjectCapability.WRITE,
                    tenant_admin=principal.is_admin,
                )
            except ProjectAccessError as exc:
                return api_response(
                    status="error",
                    message=exc.reason_code,
                    code=exc.public_status,
                )

        task = TaskDB(
            id=str(uuid.uuid4()),
            title=title,
            description=str(body.get("description") or ""),
            status=normalize_task_status(
                str(body.get("status") or "backlog")
            ),
            priority=str(body.get("priority") or "Medium"),
            team_id=(project_scope.team_id if project_scope else None),
            tenant_id=(project_scope.tenant_id if project_scope else None),
            project_id=(project_scope.project_id if project_scope else None),
            task_kind=(
                str(body.get("task_kind") or "").strip()
                or None
            ),
        )
        if project_scope is not None and principal is not None:
            task.history = [
                build_task_history_event(
                    task,
                    "task_ingested",
                    actor=principal.subject_id,
                    details={
                        "source": "api",
                        "channel": "control_center_task_management",
                    },
                )
            ]
        saved = self._repository_provider().task_repo.save(task)
        log_audit(
            "control_center_task_created",
            {
                "task_id": saved.id,
                "actor": self._user_id_provider(),
            },
        )
        return api_response(
            data={"task": self._task_serializer(saved)},
            code=201,
        )

    @check_auth
    def patch_task(self, task_id: str):
        """B05: PATCH /api/tasks/{taskId}."""

        repos = self._repository_provider()
        task = repos.task_repo.get_by_id(task_id)
        if task is None:
            return api_response(
                status="error",
                message="not_found",
                code=404,
            )
        body = request.get_json(silent=True) or {}
        vector_error = (
            reserved_vector_mutation_response(task)
            or reserved_vector_mutation_response(body)
        )
        if vector_error is not None:
            return vector_error
        try:
            ensure_external_recovery_mutation_allowed(
                task,
                action="control_center_patch",
            )
        except RecoveryTaskMutationConflict as exc:
            return api_response(
                status="error",
                message=exc.reason_code,
                data=exc.as_data(),
                code=409,
            )

        values: dict[str, Any] = {}
        if "title" in body:
            values["title"] = (
                str(body.get("title") or "").strip()
                or task.title
            )
        if "description" in body:
            values["description"] = str(
                body.get("description") or ""
            )
        if "priority" in body:
            values["priority"] = str(
                body.get("priority") or task.priority
            )
        target_status = normalize_task_status(
            str(body.get("status") or task.status)
        )
        actor = self._user_id_provider()
        update_local_task_status(
            task_id,
            target_status,
            event_type="control_center_task_updated",
            event_actor=actor or "control_center",
            event_details={
                "status_requested": "status" in body,
                "fields": sorted(values),
            },
            force=True,
            **values,
        )
        saved = repos.task_repo.get_by_id(task_id)
        if saved is None:
            return api_response(
                status="error",
                message="not_found",
                code=404,
            )
        log_audit(
            "control_center_task_updated",
            {
                "task_id": saved.id,
                "actor": actor,
                "status": saved.status,
            },
        )
        return api_response(
            data={"task": self._task_serializer(saved)}
        )

    def register(self, blueprint: Blueprint) -> None:
        blueprint.add_url_rule(
            "/tasks",
            endpoint="create_task",
            view_func=self.create_task,
            methods=["POST"],
        )
        blueprint.add_url_rule(
            "/tasks/<task_id>",
            endpoint="patch_task",
            view_func=self.patch_task,
            methods=["PATCH"],
        )


__all__ = ["ControlCenterTaskMutationRoutes"]
