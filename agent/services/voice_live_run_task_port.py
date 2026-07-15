from __future__ import annotations

import hashlib
import json
import uuid
from typing import Mapping

from agent.db_models import VoiceLiveRunDB, VoiceLiveRunSegmentDB
from agent.services.voice_delegation_task_service import (
    VoiceDelegationTask,
    get_voice_delegation_task_service,
)
from agent.services.voice_governance_domain import VoicePrincipal, voice_scope_digest


class VoiceLiveRunTaskPort:
    """Small Hub task port keeping task infrastructure out of orchestration."""

    def ensure_parent(self, principal: VoicePrincipal, run: VoiceLiveRunDB) -> None:
        from agent.repository import task_repo
        from agent.services.task_queue_service import get_task_queue_service

        if task_repo.get_by_id(run.parent_task_id) is not None:
            return
        tenant_scope_hash = hashlib.sha256(principal.tenant_id.encode()).hexdigest()
        owner_subject_hash = hashlib.sha256(principal.subject.encode()).hexdigest()
        get_task_queue_service().ingest_task(
            task_id=run.parent_task_id,
            status="in_progress",
            title="Hub-controlled Voice live run",
            description="Orchestrate bounded Voice transcription segments.",
            priority="medium",
            created_by=principal.subject,
            source="voice_api",
            tags=["voice_transcription", "voice_live_run", "hub_orchestration"],
            event_type="voice_live_run_started",
            event_details={"run_id": run.id, "max_duration_seconds": run.max_duration_seconds},
            extra_fields={
                "task_kind": "voice_live_run",
                "required_capabilities": ["voice_transcription"],
                "worker_execution_context": {
                    "voice_live_run": {
                        "run_id": run.id,
                        "profile_id": run.profile_id,
                        "configuration_session_id": run.configuration_session_id,
                        "tenant_scope_hash": tenant_scope_hash,
                        "owner_subject_hash": owner_subject_hash,
                        "deletion_scope_digest": voice_scope_digest(principal, run.profile_id),
                        "max_duration_seconds": run.max_duration_seconds,
                        "segment_duration_seconds": run.segment_duration_seconds,
                        "persistence_owner": "hub",
                        "raw_audio_persistence_allowed": False,
                    }
                },
            },
        )

    def create_link_child(
        self,
        principal: VoicePrincipal,
        run: VoiceLiveRunDB,
        *,
        sequence: int,
        result_ref: str,
        idempotency_key: str,
    ) -> VoiceDelegationTask:
        return get_voice_delegation_task_service().start(
            principal,
            request_id=f"voice-live-link-{uuid.uuid4().hex}",
            request_hash=f"voice-live-result:{run.id}:{sequence}:{result_ref}",
            effective_configuration={},
            deadline_seconds=120.0,
            idempotency_key=idempotency_key,
            profile_id=run.profile_id,
            configuration_session_id=run.configuration_session_id,
            parent_task_id=run.parent_task_id,
            operation="live_segment_link",
        )

    def create_correction_child(
        self,
        principal: VoicePrincipal,
        run: VoiceLiveRunDB,
        segment: VoiceLiveRunSegmentDB,
        *,
        effective_configuration: Mapping[str, object],
        idempotency_key: str,
    ) -> VoiceDelegationTask:
        configuration_digest = hashlib.sha256(
            json.dumps(
                dict(effective_configuration),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        return get_voice_delegation_task_service().start(
            principal,
            request_id=f"voice-live-correction-{uuid.uuid4().hex}",
            request_hash=(
                f"voice-live-correction:{run.id}:{segment.sequence}:"
                f"{segment.provisional_result_ref}:{configuration_digest}"
            ),
            effective_configuration=dict(effective_configuration),
            deadline_seconds=120.0,
            idempotency_key=idempotency_key,
            profile_id=run.profile_id,
            configuration_session_id=run.configuration_session_id,
            parent_task_id=segment.task_id or run.parent_task_id,
            operation="live_segment_correction",
        )

    @staticmethod
    def complete_child(task: VoiceDelegationTask, *, result_ref: str) -> None:
        get_voice_delegation_task_service().complete(task, result_ref=result_ref)

    @staticmethod
    def fail_child(task: VoiceDelegationTask, exc: BaseException) -> None:
        get_voice_delegation_task_service().fail(task, exc)

    @staticmethod
    def complete_parent(run: VoiceLiveRunDB, *, result_ref: str) -> None:
        from agent.repository import task_repo
        from agent.services.voice_task_terminal_service import get_voice_task_terminal_service

        current = task_repo.get_by_id(run.parent_task_id)
        if current is not None and current.status == "completed" and current.last_output == result_ref:
            return
        get_voice_task_terminal_service().update_existing(
            run.parent_task_id,
            "completed",
            last_output=result_ref,
            verification_status={
                "voice_live_run": {
                    "status": "verified",
                    "run_id": run.id,
                    "result_ref": result_ref,
                    "run_status": run.status,
                }
            },
            event_type="voice_live_run_completed",
            event_actor="hub",
            event_details={"run_id": run.id, "result_ref": result_ref, "status": run.status},
        )

    @staticmethod
    def expire_parent(run: VoiceLiveRunDB) -> None:
        from agent.repository import task_repo
        from agent.services.voice_task_terminal_service import get_voice_task_terminal_service

        current = task_repo.get_by_id(run.parent_task_id)
        if current is not None and current.status == "cancelled":
            return
        get_voice_task_terminal_service().update_existing(
            run.parent_task_id,
            "cancelled",
            status_reason_code="voice_live_run_expired",
            status_reason_details={"run_id": run.id},
            event_type="voice_live_run_expired",
            event_actor="hub",
            event_details={"run_id": run.id},
        )

    @staticmethod
    def cancel_child(task_id: str, *, reason_code: str) -> None:
        get_voice_delegation_task_service().cancel(task_id, reason_code=reason_code)

    @staticmethod
    def delete_child_tree(
        principal: VoicePrincipal,
        *,
        profile_id: str,
        root_task_id: str,
        expected_result_ref: str | None = None,
    ) -> int:
        """Remove only the scoped Voice execution tree created after deletion."""

        from sqlmodel import Session, delete, select

        from agent.database import engine
        from agent.db_models import ArchivedTaskDB, TaskDB

        tenant_hash = hashlib.sha256(principal.tenant_id.encode()).hexdigest()
        owner_hash = hashlib.sha256(principal.subject.encode()).hexdigest()
        context_keys = {
            "voice_live_run": "voice_live_run",
            "voice_transcription": "voice_transcription",
            "voice_generative_judge": "voice_generative_judge",
            "voice_generative_corrector": "voice_generative_corrector",
            "restricted_inference": "restricted_inference",
        }
        with Session(engine) as session:
            active = list(session.exec(select(TaskDB)).all())
            archived = list(session.exec(select(ArchivedTaskDB)).all())
            candidates = [*active, *archived]

            def scoped(task) -> bool:
                key = context_keys.get(str(task.task_kind or ""))
                context = task.worker_execution_context if isinstance(task.worker_execution_context, dict) else {}
                value = context.get(key) if key else None
                return bool(
                    isinstance(value, Mapping)
                    and value.get("tenant_scope_hash") == tenant_hash
                    and value.get("owner_subject_hash") == owner_hash
                    and value.get("profile_id") == profile_id
                )

            root = next((task for task in candidates if task.id == root_task_id), None)
            root_is_recreated_completion = bool(
                root is not None
                and expected_result_ref
                and root.status == "completed"
                and root.last_output == expected_result_ref
            )
            if root is not None and not scoped(root) and not root_is_recreated_completion:
                return 0
            deleted_ids = {root_task_id}
            allowed_descendant_kinds = set(context_keys)
            changed = True
            while changed:
                changed = False
                for task in candidates:
                    if task.id in deleted_ids:
                        continue
                    is_descendant = task.parent_task_id in deleted_ids or task.source_task_id in deleted_ids
                    if is_descendant and (scoped(task) or str(task.task_kind or "") in allowed_descendant_kinds):
                        deleted_ids.add(task.id)
                        changed = True
            session.exec(delete(TaskDB).where(TaskDB.id.in_(deleted_ids)))
            session.exec(delete(ArchivedTaskDB).where(ArchivedTaskDB.id.in_(deleted_ids)))
            session.commit()
            return len(deleted_ids)

    def delete_children_for_parent(
        self,
        principal: VoicePrincipal,
        *,
        profile_id: str,
        parent_task_id: str,
    ) -> int:
        from sqlmodel import Session, select

        from agent.database import engine
        from agent.db_models import TaskDB

        with Session(engine) as session:
            root_ids = [
                task.id for task in session.exec(select(TaskDB).where(TaskDB.parent_task_id == parent_task_id)).all()
            ]
        return sum(
            self.delete_child_tree(
                principal,
                profile_id=profile_id,
                root_task_id=task_id,
            )
            for task_id in root_ids
        )
