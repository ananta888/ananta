from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import (
    ArchivedTaskDB,
    TaskDB,
    VoiceConfigurationDeltaDB,
    VoiceConsentDB,
    VoiceFeedbackDB,
    VoiceGovernanceIdempotencyDB,
    VoicePersonalizationProfileDB,
    VoiceResultArtifactDB,
    VoiceReviewDB,
)
from agent.repositories.voice_privacy import VoicePrivacyRepository
from agent.services.voice_governance_domain import VoicePrincipal


@dataclass(frozen=True)
class VoiceDeletionCandidateScope:
    principal: VoicePrincipal
    profile_id: str


class VoiceDeletionReconciliationRepository:
    """Discover restored scopes and remove only pre-deletion records."""

    _SCOPED_MODELS = (
        VoiceConsentDB,
        VoiceFeedbackDB,
        VoicePersonalizationProfileDB,
        VoiceResultArtifactDB,
        VoiceReviewDB,
    )

    def list_candidate_scopes(self) -> tuple[VoiceDeletionCandidateScope, ...]:
        scopes: set[tuple[str, str, str]] = set()
        with Session(engine) as session:
            for model in self._SCOPED_MODELS:
                rows = session.exec(select(model.tenant_id, model.owner_subject, model.profile_id).distinct()).all()
                scopes.update((str(row[0]), str(row[1]), str(row[2])) for row in rows)
            configurations = session.exec(
                select(
                    VoiceConfigurationDeltaDB.tenant_id,
                    VoiceConfigurationDeltaDB.owner_subject,
                    VoiceConfigurationDeltaDB.scope_id,
                )
                .where(VoiceConfigurationDeltaDB.scope == "profile")
                .distinct()
            ).all()
            scopes.update((str(row[0]), str(row[1]), str(row[2])) for row in configurations)
            idempotency_rows = session.exec(select(VoiceGovernanceIdempotencyDB)).all()
            for record in idempotency_rows:
                profile_id = self._profile_from_operation(record.operation)
                if profile_id:
                    scopes.add((record.tenant_id, record.owner_subject, profile_id))
        return tuple(
            VoiceDeletionCandidateScope(
                principal=VoicePrincipal(tenant_id=tenant_id, subject=owner_subject),
                profile_id=profile_id,
            )
            for tenant_id, owner_subject, profile_id in sorted(scopes)
        )

    def delete_before(
        self,
        principal: VoicePrincipal,
        profile_id: str,
        *,
        deleted_at: float,
        session_ids: set[str] | None = None,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        with Session(engine) as session:
            reviews = self._old_rows(session, VoiceReviewDB, principal, profile_id, deleted_at)
            result_artifacts = self._old_rows(
                session,
                VoiceResultArtifactDB,
                principal,
                profile_id,
                deleted_at,
            )
            related_resource_ids = {str(row.id) for row in (*reviews, *result_artifacts)}
            related_session_ids = {str(row.session_id) for row in reviews if row.session_id}
            related_session_ids.update(str(item) for item in (session_ids or set()) if item)
            for model in self._SCOPED_MODELS:
                rows = self._old_rows(session, model, principal, profile_id, deleted_at)
                for row in rows:
                    session.delete(row)
                counts[model.__tablename__] = len(rows)

            task_counts, task_session_ids, task_ids = self._delete_tasks_before(
                session,
                principal,
                profile_id,
                deleted_at=deleted_at,
            )
            counts.update(task_counts)
            related_session_ids.update(task_session_ids)
            related_resource_ids.update(task_ids)
            related_resource_ids.update(related_session_ids)

            profile_configurations = list(
                session.exec(
                    select(VoiceConfigurationDeltaDB).where(
                        VoiceConfigurationDeltaDB.tenant_id == principal.tenant_id,
                        VoiceConfigurationDeltaDB.owner_subject == principal.subject,
                        VoiceConfigurationDeltaDB.scope == "profile",
                        VoiceConfigurationDeltaDB.scope_id == profile_id,
                        VoiceConfigurationDeltaDB.created_at <= deleted_at,
                    )
                ).all()
            )
            session_configurations = (
                list(
                    session.exec(
                        select(VoiceConfigurationDeltaDB).where(
                            VoiceConfigurationDeltaDB.tenant_id == principal.tenant_id,
                            VoiceConfigurationDeltaDB.owner_subject == principal.subject,
                            VoiceConfigurationDeltaDB.scope == "session",
                            VoiceConfigurationDeltaDB.scope_id.in_(related_session_ids),
                            VoiceConfigurationDeltaDB.created_at <= deleted_at,
                        )
                    ).all()
                )
                if related_session_ids
                else []
            )
            for configuration in (*profile_configurations, *session_configurations):
                session.delete(configuration)
            counts[VoiceConfigurationDeltaDB.__tablename__] = len(profile_configurations) + len(
                session_configurations
            )

            old_idempotency = [
                record
                for record in session.exec(
                    select(VoiceGovernanceIdempotencyDB).where(
                        VoiceGovernanceIdempotencyDB.tenant_id == principal.tenant_id,
                        VoiceGovernanceIdempotencyDB.owner_subject == principal.subject,
                        VoiceGovernanceIdempotencyDB.created_at <= deleted_at,
                    )
                ).all()
                if self._idempotency_matches_profile(
                    record,
                    profile_id=profile_id,
                    related_resource_ids=related_resource_ids,
                    related_session_ids=related_session_ids,
                )
            ]
            for record in old_idempotency:
                session.delete(record)
            counts[VoiceGovernanceIdempotencyDB.__tablename__] = len(old_idempotency)
            session.commit()
        return counts

    def delete_tasks_by_scope_digest(
        self,
        scope_digest: str,
        *,
        deleted_at: float,
    ) -> dict[str, int]:
        with Session(engine) as session:
            tasks = [task for task in session.exec(select(TaskDB)).all() if task.created_at <= deleted_at]
            archived = [task for task in session.exec(select(ArchivedTaskDB)).all() if task.created_at <= deleted_at]
            deleted_ids = {
                task.id
                for task in (*tasks, *archived)
                if self._task_deletion_scope_digest(task) == scope_digest
            }
            for task in tasks:
                if task.id in deleted_ids:
                    session.delete(task)
            for task in archived:
                if task.id in deleted_ids:
                    session.delete(task)
            task_count = sum(task.id in deleted_ids for task in tasks)
            archived_count = sum(task.id in deleted_ids for task in archived)
            session.commit()
        return {
            TaskDB.__tablename__: task_count,
            ArchivedTaskDB.__tablename__: archived_count,
        }

    @staticmethod
    def _delete_tasks_before(
        session: Session,
        principal: VoicePrincipal,
        profile_id: str,
        *,
        deleted_at: float,
    ) -> tuple[dict[str, int], set[str], set[str]]:
        tenant_scope_hash = hashlib.sha256(principal.tenant_id.encode()).hexdigest()
        owner_subject_hash = hashlib.sha256(principal.subject.encode()).hexdigest()
        tasks = [task for task in session.exec(select(TaskDB)).all() if task.created_at <= deleted_at]
        archived = [task for task in session.exec(select(ArchivedTaskDB)).all() if task.created_at <= deleted_at]
        deleted_ids: set[str] = set()
        related_session_ids: set[str] = set()
        for task in (*tasks, *archived):
            if VoicePrivacyRepository._task_matches_profile(
                task,
                principal=principal,
                profile_id=profile_id,
                tenant_scope_hash=tenant_scope_hash,
                owner_subject_hash=owner_subject_hash,
                artifact_ids=set(),
            ):
                deleted_ids.add(task.id)
                voice_context = dict((task.worker_execution_context or {}).get("voice_transcription") or {})
                configuration_session_id = str(voice_context.get("configuration_session_id") or "").strip()
                if configuration_session_id:
                    related_session_ids.add(configuration_session_id)
        changed = True
        while changed:
            changed = False
            for task in (*tasks, *archived):
                if task.id in deleted_ids:
                    continue
                related = task.parent_task_id in deleted_ids or task.source_task_id in deleted_ids
                if related and VoicePrivacyRepository._task_scope_matches(
                    task,
                    profile_id=profile_id,
                    tenant_scope_hash=tenant_scope_hash,
                    owner_subject_hash=owner_subject_hash,
                ):
                    deleted_ids.add(task.id)
                    changed = True
        for task in tasks:
            if task.id in deleted_ids:
                session.delete(task)
        for task in archived:
            if task.id in deleted_ids:
                session.delete(task)
        return (
            {
                TaskDB.__tablename__: sum(task.id in deleted_ids for task in tasks),
                ArchivedTaskDB.__tablename__: sum(task.id in deleted_ids for task in archived),
            },
            related_session_ids,
            deleted_ids,
        )

    @staticmethod
    def _old_rows(session: Session, model, principal: VoicePrincipal, profile_id: str, deleted_at: float):
        return list(
            session.exec(
                select(model).where(
                    model.tenant_id == principal.tenant_id,
                    model.owner_subject == principal.subject,
                    model.profile_id == profile_id,
                    model.created_at <= deleted_at,
                )
            ).all()
        )

    @staticmethod
    def _profile_from_operation(operation: str) -> str | None:
        prefixes = (
            "voice_consent.set:",
            "voice_personalization.feedback:",
            "voice_personalization.import:",
            "voice_personalization.reset:",
            "voice_privacy.delete:",
            "voice_training_export.create:",
        )
        for prefix in prefixes:
            if operation.startswith(prefix):
                profile_id = operation.removeprefix(prefix).strip()
                return profile_id or None
        configuration_prefix = "voice_configuration.put:profile:"
        if operation.startswith(configuration_prefix):
            profile_id = operation.removeprefix(configuration_prefix).strip()
            return profile_id or None
        return None

    @staticmethod
    def _idempotency_matches_profile(
        record: VoiceGovernanceIdempotencyDB,
        *,
        profile_id: str,
        related_resource_ids: set[str],
        related_session_ids: set[str],
    ) -> bool:
        if VoiceDeletionReconciliationRepository._profile_from_operation(record.operation) == profile_id:
            return True
        if record.operation.startswith("voice_configuration.put:session:"):
            return record.operation.removeprefix("voice_configuration.put:session:") in related_session_ids
        if record.operation.startswith("voice_review.decide:"):
            return record.operation.removeprefix("voice_review.decide:") in related_resource_ids
        return _references_any(record.result_metadata, related_resource_ids)

    @staticmethod
    def _task_deletion_scope_digest(task: TaskDB | ArchivedTaskDB) -> str:
        context = task.worker_execution_context if isinstance(task.worker_execution_context, dict) else {}
        for key in (
            "voice_transcription",
            "restricted_inference",
            "voice_generative_judge",
            "voice_training_export",
        ):
            scoped = context.get(key)
            if isinstance(scoped, dict):
                digest = str(scoped.get("deletion_scope_digest") or "")
                if len(digest) == 64:
                    return digest
        return ""


def _references_any(value: object, references: set[str]) -> bool:
    if not references:
        return False
    if isinstance(value, str):
        return value in references
    if isinstance(value, dict):
        return any(_references_any(item, references) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_references_any(item, references) for item in value)
    return False
