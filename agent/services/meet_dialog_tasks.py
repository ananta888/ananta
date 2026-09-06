"""Ordinary Hub TaskQueue integration; no second scheduler or Worker-owned tasks."""


class HubDialogTasks:
    def list_page(self, tenant, project, offset):
        from agent.services.repository_registry import get_repository_registry
        return get_repository_registry().task_repo.get_paged(limit=50, offset=offset, status="in_progress",
            tenant_id=tenant, project_id=project, task_kind="meet_dialog_session")

    def set_controls(self, scope, controls):
        from dataclasses import asdict
        from agent.services.task_runtime_service import compare_and_set_local_task_status
        task = self.get_by_id(scope.task_id)
        context = dict(task.worker_execution_context or {})
        current = dict(context.get("meet_dialog", {}))
        if current.get("controls") != asdict(scope.controls) or current.get("lease_id") != scope.lease_id or current.get("runtime_id") != scope.runtime_id:
            return False
        return compare_and_set_local_task_status(scope.task_id, "in_progress", expected_statuses={"in_progress"},
            authoritative_predicate=lambda row: row.task_kind == "meet_dialog_session" and row.tenant_id == scope.tenant_id
                and row.project_id == scope.project_id and row.worker_execution_context == context,
            worker_execution_context=context | {"meet_dialog": current | {"controls": controls}},
            event_type="meet_dialog_controls_changed", event_actor=scope.owner_subject,
            event_details={"revision": controls["revision"]})

    def claim_audio(self, scope, job, now):
        from agent.services.task_runtime_service import compare_and_set_local_task_status
        from agent.services.task_queue_service import get_task_queue_service
        from agent.services.meet_contract import MeetError
        task = self.get_by_id(scope.task_id)
        context = dict(task.worker_execution_context or {})
        current = dict(context.get("meet_dialog", {}))
        previous = current.get("audio_job")
        if (current.get("lease_id") != scope.lease_id or current.get("runtime_id") != scope.runtime_id
                or current.get("audio_count", 720) >= 720 or previous and previous["deadline"] > now):
            raise MeetError("meet_audio_job_busy_or_exhausted", 409)
        changed = current | {"audio_job": job, "audio_count": current["audio_count"] + 1}
        claimed = compare_and_set_local_task_status(scope.task_id, "in_progress", expected_statuses={"in_progress"},
            authoritative_predicate=lambda row: row.task_kind == "meet_dialog_session"
                and row.tenant_id == scope.tenant_id and row.project_id == scope.project_id
                and row.worker_execution_context == context,
            worker_execution_context=context | {"meet_dialog": changed}, event_type="meet_audio_delegated", event_actor="hub")
        if not claimed:
            raise MeetError("meet_audio_job_conflict", 409)
        if previous:
            self.finish_audio(scope, previous, "failed", release=False)
        # Reserve at the parent before child ingestion: an uncertain creation cannot
        # produce parallel jobs. The reservation remains bounded by its 30s expiry.
        get_task_queue_service().ingest_task(task_id=job["task_id"], status="in_progress", title="Authorized Meet audio utterance",
            description="Bounded local ASR; no audio or transcript persisted.", created_by=scope.owner_subject,
            source="meet_audio", event_type="meet_audio_ingested", event_channel="hub_task_queue",
            extra_fields={"task_kind": "meet_audio_receive", "tenant_id": scope.tenant_id, "project_id": scope.project_id,
                          "parent_task_id": scope.task_id, "required_capabilities": ["meet_audio_receive"],
                          "worker_execution_context": {"meet_audio": job, "parent_dispatch": scope.lease_id, "runtime_id": scope.runtime_id}})

    def finish_audio(self, scope, job, status, *, release=True):
        from agent.services.task_runtime_service import compare_and_set_local_task_status
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("meet_audio_terminal_invalid")
        completed = compare_and_set_local_task_status(job["task_id"], status, expected_statuses={"in_progress"},
            authoritative_predicate=lambda row: row.task_kind == "meet_audio_receive"
                and row.tenant_id == scope.tenant_id and row.project_id == scope.project_id
                and (row.worker_execution_context or {}).get("meet_audio") == job
                and (row.worker_execution_context or {}).get("parent_dispatch") == scope.lease_id
                and (row.worker_execution_context or {}).get("runtime_id") == scope.runtime_id,
            event_type="meet_audio_" + status, event_actor="hub")
        if release and completed:
            parent = self.get_by_id(scope.task_id)
            context = dict(parent.worker_execution_context or {})
            current = dict(context.get("meet_dialog", {}))
            if current.get("audio_job") == job:
                compare_and_set_local_task_status(scope.task_id, "in_progress", expected_statuses={"in_progress"},
                    authoritative_predicate=lambda row: row.worker_execution_context == context,
                    worker_execution_context=context | {"meet_dialog": current | {"audio_job": None}})
        return completed

    def finish_bound(self, task_id, lease_id, runtime_id, status):
        """Cleanup only: expired/revoked authority must still be able to stop itself."""
        from types import SimpleNamespace
        task = self.get_by_id(task_id)
        if task is None or task.task_kind != "meet_dialog_session":
            return False
        scope = SimpleNamespace(task_id=task_id, lease_id=lease_id, runtime_id=runtime_id,
            tenant_id=task.tenant_id, project_id=task.project_id)
        finished = self.finish(scope, status)
        if finished:
            job = (task.worker_execution_context or {}).get("meet_dialog", {}).get("audio_job")
            if job:
                self.finish_audio(scope, job, "cancelled", release=False)
        return finished

    def get_by_id(self, task_id):
        from agent.services.repository_registry import get_repository_registry
        return get_repository_registry().task_repo.get_by_id(task_id)

    def start(self, task_id, tenant, project, context):
        from agent.services.task_queue_service import get_task_queue_service
        get_task_queue_service().ingest_task(task_id=task_id, status="in_progress", title="Authorized Meet dialog session",
            description="Hub-delegated isolated Meet client; content-free lifecycle metadata.",
            created_by=context["owner_subject"], source="meet_dialog", event_type="meet_dialog_delegated", event_channel="hub_task_queue",
            extra_fields={"task_kind": "meet_dialog_session", "tenant_id": tenant, "project_id": project,
                          "required_capabilities": ["meet_dialog_session"], "parent_task_id": context["binding_task_id"] or None,
                          "worker_execution_context": {"meet_dialog": context}})

    def finish(self, scope, status):
        from agent.services.task_runtime_service import compare_and_set_local_task_status
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("meet_dialog_terminal_invalid")
        return compare_and_set_local_task_status(scope.task_id, status, expected_statuses={"in_progress"},
            authoritative_predicate=lambda task: task.task_kind == "meet_dialog_session"
                and task.tenant_id == scope.tenant_id and task.project_id == scope.project_id
                and (task.worker_execution_context or {}).get("meet_dialog", {}).get("lease_id") == scope.lease_id
                and (task.worker_execution_context or {}).get("meet_dialog", {}).get("runtime_id") == scope.runtime_id,
            event_type="meet_dialog_" + status, event_actor="hub", event_details={"runtime_id": scope.runtime_id})
