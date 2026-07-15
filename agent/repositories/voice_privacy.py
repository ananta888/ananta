from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping

from sqlmodel import Session, delete, select

from agent.database import engine
from agent.db_models import (
    ArchivedTaskDB,
    TaskDB,
    VoiceConfigurationDeltaDB,
    VoiceConsentDB,
    VoiceFeedbackDB,
    VoiceGovernanceIdempotencyDB,
    VoiceLiveRunDB,
    VoiceLiveRunSegmentDB,
    VoicePersonalizationProfileDB,
    VoiceResultArtifactDB,
    VoiceReviewDB,
)
from agent.services.voice_governance_domain import VoicePrincipal


class VoicePrivacyRepository:
    """Deletes every Hub-owned voice artifact for one tenant/profile scope."""

    _VOICE_TASK_KINDS = frozenset(
        {
            "restricted_inference",
            "voice_training_export",
            "voice_transcription",
            "voice_live_run",
            "voice_generative_judge",
            "voice_generative_corrector",
        }
    )
    _VOICE_CONTEXT_BY_TASK_KIND = {
        "restricted_inference": "restricted_inference",
        "voice_generative_judge": "voice_generative_judge",
        "voice_generative_corrector": "voice_generative_corrector",
        "voice_transcription": "voice_transcription",
        "voice_live_run": "voice_live_run",
    }

    def delete_profile(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        *,
        session_ids: Iterable[str] = (),
        task_ids: Iterable[str] = (),
    ) -> dict[str, int]:
        scoped_models = (VoiceConsentDB, VoiceReviewDB, VoiceFeedbackDB, VoicePersonalizationProfileDB)
        counts: dict[str, int] = {}
        with Session(engine) as session:
            reviews = list(
                session.exec(
                    select(VoiceReviewDB).where(
                        VoiceReviewDB.tenant_id == principal.tenant_id,
                        VoiceReviewDB.owner_subject == principal.subject,
                        VoiceReviewDB.profile_id == profile_id,
                    )
                ).all()
            )
            feedback = list(
                session.exec(
                    select(VoiceFeedbackDB).where(
                        VoiceFeedbackDB.tenant_id == principal.tenant_id,
                        VoiceFeedbackDB.owner_subject == principal.subject,
                        VoiceFeedbackDB.profile_id == profile_id,
                    )
                ).all()
            )
            consents = list(
                session.exec(
                    select(VoiceConsentDB).where(
                        VoiceConsentDB.tenant_id == principal.tenant_id,
                        VoiceConsentDB.owner_subject == principal.subject,
                        VoiceConsentDB.profile_id == profile_id,
                    )
                ).all()
            )
            profiles = list(
                session.exec(
                    select(VoicePersonalizationProfileDB).where(
                        VoicePersonalizationProfileDB.tenant_id == principal.tenant_id,
                        VoicePersonalizationProfileDB.owner_subject == principal.subject,
                        VoicePersonalizationProfileDB.profile_id == profile_id,
                    )
                ).all()
            )
            result_artifacts = list(
                session.exec(
                    select(VoiceResultArtifactDB).where(
                        VoiceResultArtifactDB.tenant_id == principal.tenant_id,
                        VoiceResultArtifactDB.owner_subject == principal.subject,
                        VoiceResultArtifactDB.profile_id == profile_id,
                    )
                ).all()
            )
            live_runs = list(
                session.exec(
                    select(VoiceLiveRunDB).where(
                        VoiceLiveRunDB.tenant_id == principal.tenant_id,
                        VoiceLiveRunDB.owner_subject == principal.subject,
                        VoiceLiveRunDB.profile_id == profile_id,
                    )
                ).all()
            )
            live_run_ids = {item.id for item in live_runs}
            live_segments = (
                list(
                    session.exec(
                        select(VoiceLiveRunSegmentDB).where(
                            VoiceLiveRunSegmentDB.run_id.in_(live_run_ids),
                            VoiceLiveRunSegmentDB.tenant_id == principal.tenant_id,
                            VoiceLiveRunSegmentDB.owner_subject == principal.subject,
                        )
                    ).all()
                )
                if live_run_ids
                else []
            )
            related_session_ids = {
                str(value)
                for value in (
                    *session_ids,
                    *(item.session_id for item in reviews),
                )
                if value
            }
            related_resource_ids = {
                profile_id,
                *(item.id for item in reviews),
                *(item.id for item in feedback),
                *(item.id for item in consents),
                *(item.id for item in profiles),
                *(item.id for item in result_artifacts),
                *(item.id for item in live_runs),
                *(item.id for item in live_segments),
                *related_session_ids,
            }
            related_session_ids.update(
                str(item.configuration_session_id)
                for item in live_runs
                if item.configuration_session_id
            )
            related_resource_ids.update(related_session_ids)
            if live_run_ids:
                session.exec(
                    delete(VoiceLiveRunSegmentDB).where(
                        VoiceLiveRunSegmentDB.run_id.in_(live_run_ids),
                        VoiceLiveRunSegmentDB.tenant_id == principal.tenant_id,
                        VoiceLiveRunSegmentDB.owner_subject == principal.subject,
                    )
                )
                session.exec(
                    delete(VoiceLiveRunDB).where(
                        VoiceLiveRunDB.id.in_(live_run_ids),
                        VoiceLiveRunDB.tenant_id == principal.tenant_id,
                        VoiceLiveRunDB.owner_subject == principal.subject,
                    )
                )
            counts[VoiceLiveRunSegmentDB.__tablename__] = len(live_segments)
            counts[VoiceLiveRunDB.__tablename__] = len(live_runs)

            for model in scoped_models:
                predicate = (
                    (model.tenant_id == principal.tenant_id)
                    & (model.owner_subject == principal.subject)
                    & (model.profile_id == profile_id)
                )
                rows = session.exec(select(model.id).where(predicate)).all()
                session.exec(delete(model).where(predicate))
                counts[model.__tablename__] = len(rows)

            result_predicate = (
                (VoiceResultArtifactDB.tenant_id == principal.tenant_id)
                & (VoiceResultArtifactDB.owner_subject == principal.subject)
                & (VoiceResultArtifactDB.profile_id == profile_id)
            )
            session.exec(delete(VoiceResultArtifactDB).where(result_predicate))
            counts[VoiceResultArtifactDB.__tablename__] = len(result_artifacts)

            deleted_task_ids, task_counts, task_session_ids = self._delete_tasks(
                session,
                principal=principal,
                profile_id=profile_id,
                artifact_ids={item.id for item in result_artifacts},
                explicit_task_ids=(
                    {str(item) for item in task_ids if item}
                    | {item.parent_task_id for item in live_runs}
                    | {item.task_id for item in live_segments if item.task_id}
                    | {item.correction_task_id for item in live_segments if item.correction_task_id}
                ),
            )
            counts.update(task_counts)
            related_resource_ids.update(deleted_task_ids)
            related_session_ids.update(task_session_ids)
            related_resource_ids.update(task_session_ids)

            config_predicate = (
                (VoiceConfigurationDeltaDB.tenant_id == principal.tenant_id)
                & (VoiceConfigurationDeltaDB.owner_subject == principal.subject)
                & (VoiceConfigurationDeltaDB.scope == "profile")
                & (VoiceConfigurationDeltaDB.scope_id == profile_id)
            )
            config_rows = session.exec(select(VoiceConfigurationDeltaDB.id).where(config_predicate)).all()
            session.exec(delete(VoiceConfigurationDeltaDB).where(config_predicate))
            session_config_predicate = (
                (VoiceConfigurationDeltaDB.tenant_id == principal.tenant_id)
                & (VoiceConfigurationDeltaDB.owner_subject == principal.subject)
                & (VoiceConfigurationDeltaDB.scope == "session")
                & (VoiceConfigurationDeltaDB.scope_id.in_(related_session_ids))
            )
            session_config_rows = (
                session.exec(select(VoiceConfigurationDeltaDB.id).where(session_config_predicate)).all()
                if related_session_ids
                else []
            )
            if related_session_ids:
                session.exec(delete(VoiceConfigurationDeltaDB).where(session_config_predicate))
            counts[VoiceConfigurationDeltaDB.__tablename__] = len(config_rows) + len(session_config_rows)

            idempotency_rows = list(
                session.exec(
                    select(VoiceGovernanceIdempotencyDB).where(
                        VoiceGovernanceIdempotencyDB.tenant_id == principal.tenant_id,
                        VoiceGovernanceIdempotencyDB.owner_subject == principal.subject,
                    )
                ).all()
            )
            deleted_idempotency = 0
            for record in idempotency_rows:
                if record.operation.startswith("voice_privacy.delete:"):
                    if record.operation == f"voice_privacy.delete:{profile_id}":
                        session.delete(record)
                        deleted_idempotency += 1
                    continue
                operation_reference = record.operation.rsplit(":", 1)[-1]
                if operation_reference in related_resource_ids or _references_any(
                    record.result_metadata,
                    related_resource_ids,
                ):
                    session.delete(record)
                    deleted_idempotency += 1
            counts[VoiceGovernanceIdempotencyDB.__tablename__] = deleted_idempotency
            session.commit()
        return counts

    def _delete_tasks(
        self,
        session: Session,
        *,
        principal: VoicePrincipal,
        profile_id: str,
        artifact_ids: set[str],
        explicit_task_ids: set[str],
    ) -> tuple[set[str], dict[str, int], set[str]]:
        tenant_scope_hash = hashlib.sha256(principal.tenant_id.encode()).hexdigest()
        owner_subject_hash = hashlib.sha256(principal.subject.encode()).hexdigest()
        tasks = list(session.exec(select(TaskDB)).all())
        archived = list(session.exec(select(ArchivedTaskDB)).all())
        task_records: list[TaskDB | ArchivedTaskDB] = [*tasks, *archived]
        deleted_ids: set[str] = set()
        related_session_ids: set[str] = set()
        for task in task_records:
            matches_profile = self._task_matches_profile(
                task,
                principal=principal,
                profile_id=profile_id,
                tenant_scope_hash=tenant_scope_hash,
                owner_subject_hash=owner_subject_hash,
                artifact_ids=artifact_ids,
            )
            if task.id in explicit_task_ids:
                matches_profile = matches_profile or self._explicit_task_matches_profile(
                    task,
                    principal=principal,
                    profile_id=profile_id,
                    tenant_scope_hash=tenant_scope_hash,
                    owner_subject_hash=owner_subject_hash,
                )
            if matches_profile:
                deleted_ids.add(task.id)
                voice_context = self._voice_context(task)
                if task.task_kind == "voice_transcription" and voice_context is not None:
                    configuration_session_id = str(voice_context.get("configuration_session_id") or "").strip()
                    if configuration_session_id:
                        related_session_ids.add(configuration_session_id)

        changed = True
        while changed:
            changed = False
            for task in task_records:
                if task.id in deleted_ids or task.task_kind not in self._VOICE_TASK_KINDS:
                    continue
                is_related = task.parent_task_id in deleted_ids or task.source_task_id in deleted_ids
                if is_related and self._task_scope_matches(
                    task,
                    profile_id=profile_id,
                    tenant_scope_hash=tenant_scope_hash,
                    owner_subject_hash=owner_subject_hash,
                ):
                    deleted_ids.add(task.id)
                    changed = True

        task_count = sum(1 for task in tasks if task.id in deleted_ids)
        archived_count = sum(1 for task in archived if task.id in deleted_ids)
        if deleted_ids:
            session.exec(delete(TaskDB).where(TaskDB.id.in_(deleted_ids)))
            session.exec(delete(ArchivedTaskDB).where(ArchivedTaskDB.id.in_(deleted_ids)))
        return (
            deleted_ids,
            {
                TaskDB.__tablename__: task_count,
                ArchivedTaskDB.__tablename__: archived_count,
            },
            related_session_ids,
        )

    @staticmethod
    def _task_matches_profile(
        task: TaskDB | ArchivedTaskDB,
        *,
        principal: VoicePrincipal,
        profile_id: str,
        tenant_scope_hash: str,
        owner_subject_hash: str,
        artifact_ids: set[str],
    ) -> bool:
        if task.task_kind not in VoicePrivacyRepository._VOICE_TASK_KINDS:
            return False
        if task.task_kind == "voice_training_export":
            training = VoicePrivacyRepository._context_for(task, "voice_training_export")
            if training is None:
                return False
            return (
                training.get("tenant_id") == principal.tenant_id
                and training.get("owner_subject") == principal.subject
                and training.get("profile_id") == profile_id
            )

        voice = VoicePrivacyRepository._voice_context(task)
        if voice is None or not VoicePrivacyRepository._task_scope_matches(
            task,
            profile_id=profile_id,
            tenant_scope_hash=tenant_scope_hash,
            owner_subject_hash=owner_subject_hash,
        ):
            return False
        profile_scoped = voice.get("profile_id") == profile_id
        result_scoped = task.last_output in artifact_ids or _references_any(
            task.verification_status,
            artifact_ids,
        )
        return profile_scoped or result_scoped

    @staticmethod
    def _explicit_task_matches_profile(
        task: TaskDB | ArchivedTaskDB,
        *,
        principal: VoicePrincipal,
        profile_id: str,
        tenant_scope_hash: str,
        owner_subject_hash: str,
    ) -> bool:
        """Treat a client-linked task ID as a candidate, never as authority."""

        if task.task_kind == "voice_training_export":
            return VoicePrivacyRepository._task_matches_profile(
                task,
                principal=principal,
                profile_id=profile_id,
                tenant_scope_hash=tenant_scope_hash,
                owner_subject_hash=owner_subject_hash,
                artifact_ids=set(),
            )
        voice = VoicePrivacyRepository._voice_context(task)
        return (
            voice is not None
            and voice.get("profile_id") == profile_id
            and VoicePrivacyRepository._task_scope_matches(
                task,
                profile_id=profile_id,
                tenant_scope_hash=tenant_scope_hash,
                owner_subject_hash=owner_subject_hash,
            )
        )

    @staticmethod
    def _task_scope_matches(
        task: TaskDB | ArchivedTaskDB,
        *,
        profile_id: str,
        tenant_scope_hash: str,
        owner_subject_hash: str,
    ) -> bool:
        voice = VoicePrivacyRepository._voice_context(task)
        if voice is None:
            return False
        context_profile_id = voice.get("profile_id")
        return (
            voice.get("tenant_scope_hash") == tenant_scope_hash
            and voice.get("owner_subject_hash") == owner_subject_hash
            and (context_profile_id is None or context_profile_id == profile_id)
        )

    @staticmethod
    def _voice_context(task: TaskDB | ArchivedTaskDB) -> Mapping[str, object] | None:
        context_key = VoicePrivacyRepository._VOICE_CONTEXT_BY_TASK_KIND.get(str(task.task_kind or ""))
        if context_key is None:
            return None
        return VoicePrivacyRepository._context_for(task, context_key)

    @staticmethod
    def _context_for(
        task: TaskDB | ArchivedTaskDB,
        context_key: str,
    ) -> Mapping[str, object] | None:
        context = task.worker_execution_context
        if not isinstance(context, Mapping):
            return None
        scoped_context = context.get(context_key)
        return scoped_context if isinstance(scoped_context, Mapping) else None


def _references_any(value: object, references: set[str]) -> bool:
    if not references:
        return False
    if isinstance(value, str):
        return value in references
    if isinstance(value, Mapping):
        return any(_references_any(item, references) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_references_any(item, references) for item in value)
    return False
