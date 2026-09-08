"""Ordinary Hub TaskQueue integration; no second scheduler or Worker-owned tasks."""


class HubDialogTasks:
    def __init__(self, *, lifecycle=None, publisher_url=None, role_assignments=None):
        self.lifecycle = lifecycle
        self.publisher_url, self.role_assignments = publisher_url, role_assignments

    def list_page(self, tenant, project, offset):
        from agent.services.repository_registry import get_repository_registry

        return get_repository_registry().task_repo.get_paged(
            limit=50,
            offset=offset,
            status="in_progress",
            tenant_id=tenant,
            project_id=project,
            task_kind="meet_dialog_session",
        )

    def set_controls(self, scope, controls):
        return self._set_controls(scope, controls)

    def set_avatar_selection(self, scope, selection, controls):
        from agent.models.meet_avatar_selection import parse_avatar_selection
        from agent.services.meet_dialog_avatar_controls import advance_avatar_selection_controls
        from ananta_contracts.meet_dialog import validate_controls

        if scope.avatar_selection is None:
            return False  # Old assignments have not negotiated image support.
        try:
            validate_controls(controls)
            if "avatar" not in controls or controls != advance_avatar_selection_controls(
                scope, controls["avatar"]["since"]
            ):
                return False
        except ValueError:
            return False
        selection = parse_avatar_selection(selection, scope.tenant_id, scope.project_id)
        return self._set_controls(scope, controls, selection=selection)

    def set_voice_selection(self, scope, selection, controls):
        from agent.models.meet_voice_selection import parse_voice_selection
        from agent.services.meet_dialog_voice_selection import advance_voice_selection_controls
        from ananta_contracts.meet_dialog import validate_controls

        if scope.voice_selection is None:
            return False
        try:
            validate_controls(controls)
            if "speech" not in controls or controls != advance_voice_selection_controls(
                scope, controls["speech"]["since"]
            ):
                return False
        except ValueError:
            return False
        selection = parse_voice_selection(selection, scope.tenant_id, scope.project_id)
        return self._set_controls(scope, controls, voice_selection=selection)

    def _set_controls(self, scope, controls, *, selection=None, voice_selection=None):
        from agent.services.meet_dialog_controls import controls_projection
        from agent.services.task_runtime_service import compare_and_set_local_task_status

        task = self.get_by_id(scope.task_id)
        if task is None:
            return False
        context = dict(task.worker_execution_context or {})
        current = dict(context.get("meet_dialog", {}))
        if (
            current.get("controls") != controls_projection(scope.controls)
            or current.get("lease_id") != scope.lease_id
            or current.get("runtime_id") != scope.runtime_id
            or selection is not None
            and current.get("avatar_selection") != scope.avatar_selection
            or voice_selection is not None
            and current.get("voice_selection") != scope.voice_selection
        ):
            return False
        changed = current | {"controls": controls}
        if selection is not None:
            changed["avatar_selection"] = selection
        if voice_selection is not None:
            changed["voice_selection"] = voice_selection
        return compare_and_set_local_task_status(
            scope.task_id,
            "in_progress",
            expected_statuses={"in_progress"},
            authoritative_predicate=lambda row: (
                row.task_kind == "meet_dialog_session"
                and row.tenant_id == scope.tenant_id
                and row.project_id == scope.project_id
                and row.worker_execution_context == context
            ),
            worker_execution_context=context | {"meet_dialog": changed},
            event_type="meet_dialog_voice_selected"
            if voice_selection is not None
            else "meet_dialog_avatar_selected"
            if selection is not None
            else "meet_dialog_controls_changed",
            event_actor=scope.owner_subject,
            event_details={"revision": controls["revision"]},
        )

    def claim_audio(self, scope, job, now):
        from agent.services.meet_contract import MeetError
        from agent.services.meet_dialog_lifecycle import MeetDialogLifecycle, organization_tuple
        from agent.services.task_queue_service import get_task_queue_service
        from agent.services.task_runtime_service import compare_and_set_local_task_status

        task = self.get_by_id(scope.task_id)
        if (
            task is None
            or task.task_kind != "meet_dialog_session"
            or task.status != "in_progress"
            or task.tenant_id != scope.tenant_id
            or task.project_id != scope.project_id
        ):
            raise MeetError("meet_audio_task_inactive", 403)
        context = dict(task.worker_execution_context or {})
        current = dict(context.get("meet_dialog", {}))
        lifecycle = self.lifecycle if self.lifecycle is not None else MeetDialogLifecycle(self)
        lifecycle.require_current(task, current.get("binding_task_id", ""))
        inherited = organization_tuple(task)
        parent_id = task.parent_task_id
        previous = current.get("audio_job")
        if (
            current.get("lease_id") != scope.lease_id
            or current.get("runtime_id") != scope.runtime_id
            or current.get("audio_count", 720) >= 720
            or previous
            and previous["deadline"] > now
        ):
            raise MeetError("meet_audio_job_busy_or_exhausted", 409)
        changed = current | {"audio_job": job, "audio_count": current["audio_count"] + 1}
        claimed = compare_and_set_local_task_status(
            scope.task_id,
            "in_progress",
            expected_statuses={"in_progress"},
            authoritative_predicate=lambda row: (
                row.task_kind == "meet_dialog_session"
                and row.tenant_id == scope.tenant_id
                and row.project_id == scope.project_id
                and row.parent_task_id == parent_id
                and organization_tuple(row) == inherited
                and row.worker_execution_context == context
            ),
            worker_execution_context=context | {"meet_dialog": changed},
            event_type="meet_audio_delegated",
            event_actor="hub",
        )
        if not claimed:
            raise MeetError("meet_audio_job_conflict", 409)
        if previous:
            self.finish_audio(scope, previous, "failed", release=False)
        # Reserve at the parent before child ingestion: an uncertain creation cannot
        # produce parallel jobs. The reservation remains bounded by its 30s expiry.
        get_task_queue_service().ingest_task(
            task_id=job["task_id"],
            status="in_progress",
            title="Authorized Meet audio utterance",
            description="Bounded local ASR; no audio or transcript persisted.",
            created_by=scope.owner_subject,
            source="meet_audio",
            team_id=inherited.get("team_id"),
            event_type="meet_audio_ingested",
            event_channel="hub_task_queue",
            extra_fields={
                "task_kind": "meet_audio_receive",
                "tenant_id": scope.tenant_id,
                "project_id": scope.project_id,
                **{key: value for key, value in inherited.items() if key != "team_id"},
                "parent_task_id": scope.task_id,
                "required_capabilities": ["meet_audio_receive"],
                "worker_execution_context": {
                    "meet_audio": job,
                    "parent_dispatch": scope.lease_id,
                    "runtime_id": scope.runtime_id,
                },
            },
        )

    def finish_audio(self, scope, job, status, *, release=True):
        from agent.services.task_runtime_service import compare_and_set_local_task_status

        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("meet_audio_terminal_invalid")
        completed = compare_and_set_local_task_status(
            job["task_id"],
            status,
            expected_statuses={"in_progress"},
            authoritative_predicate=lambda row: (
                row.task_kind == "meet_audio_receive"
                and row.tenant_id == scope.tenant_id
                and row.project_id == scope.project_id
                and (row.worker_execution_context or {}).get("meet_audio") == job
                and (row.worker_execution_context or {}).get("parent_dispatch") == scope.lease_id
                and (row.worker_execution_context or {}).get("runtime_id") == scope.runtime_id
            ),
            event_type="meet_audio_" + status,
            event_actor="hub",
        )
        if release and completed:
            parent = self.get_by_id(scope.task_id)
            context = dict(parent.worker_execution_context or {})
            current = dict(context.get("meet_dialog", {}))
            if current.get("audio_job") == job:
                compare_and_set_local_task_status(
                    scope.task_id,
                    "in_progress",
                    expected_statuses={"in_progress"},
                    authoritative_predicate=lambda row: row.worker_execution_context == context,
                    worker_execution_context=context | {"meet_dialog": current | {"audio_job": None}},
                )
        return completed

    def finish_bound(self, task_id, lease_id, runtime_id, status):
        """Cleanup only: expired/revoked authority must still be able to stop itself."""
        from types import SimpleNamespace

        task = self.get_by_id(task_id)
        if task is None or task.task_kind != "meet_dialog_session":
            return False
        scope = SimpleNamespace(
            task_id=task_id,
            lease_id=lease_id,
            runtime_id=runtime_id,
            tenant_id=task.tenant_id,
            project_id=task.project_id,
        )
        finished = self.finish(scope, status)
        if finished:
            job = (task.worker_execution_context or {}).get("meet_dialog", {}).get("audio_job")
            if job:
                self.finish_audio(scope, job, "cancelled", release=False)
        return finished

    def get_by_id(self, task_id):
        from agent.services.repository_registry import get_repository_registry

        return get_repository_registry().task_repo.get_by_id(task_id)

    def start(self, task_id, tenant, project, context, *, phase=None):
        from agent.services.meet_dialog_lifecycle import MeetDialogLifecycle
        from agent.services.meet_role_assignment import get_meet_role_assignments
        from agent.services.task_queue_service import get_task_queue_service

        lifecycle = self.lifecycle if self.lifecycle is not None else MeetDialogLifecycle(self)
        scope = lifecycle.scope_for_parent(tenant, project, context["binding_task_id"])
        assignments = self.role_assignments if self.role_assignments is not None else get_meet_role_assignments()
        role_binding = assignments.admit(task_id, tenant, project, context, scope, self.publisher_url)
        execution = {"meet_dialog": context}
        if phase is not None:
            from agent.models.meet_dialog_phase import phase_binding, validate_record

            execution["meet_phase"] = validate_record(phase, phase_binding(task_id, tenant, project, context))
        if role_binding is not None:
            execution["meet_role_assignment"] = role_binding
        get_task_queue_service().ingest_task(
            task_id=task_id,
            status="in_progress",
            title="Authorized Meet dialog session",
            description="Hub-delegated isolated Meet client; content-free lifecycle metadata.",
            created_by=context["owner_subject"],
            source="meet_dialog",
            team_id=scope.pop("team_id", None),
            event_type="meet_dialog_delegated",
            event_channel="hub_task_queue",
            extra_fields={
                "task_kind": "meet_dialog_session",
                "tenant_id": tenant,
                "project_id": project,
                **scope,
                "required_capabilities": ["meet_dialog_session"],
                "parent_task_id": context["binding_task_id"] or None,
                "worker_execution_context": execution,
                **({"assigned_agent_url": role_binding["publisher_url"]} if role_binding is not None else {}),
            },
        )

    def finish(self, scope, status):
        from agent.services.task_runtime_service import compare_and_set_local_task_status

        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("meet_dialog_terminal_invalid")
        return compare_and_set_local_task_status(
            scope.task_id,
            status,
            expected_statuses={"in_progress"},
            authoritative_predicate=lambda task: (
                task.task_kind == "meet_dialog_session"
                and task.tenant_id == scope.tenant_id
                and task.project_id == scope.project_id
                and (task.worker_execution_context or {}).get("meet_dialog", {}).get("lease_id") == scope.lease_id
                and (task.worker_execution_context or {}).get("meet_dialog", {}).get("runtime_id") == scope.runtime_id
            ),
            event_type="meet_dialog_" + status,
            event_actor="hub",
            event_details={"runtime_id": scope.runtime_id},
        )
