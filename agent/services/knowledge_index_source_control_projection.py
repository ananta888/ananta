"""Canonical Source Control projection of completed governed index runs."""

from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

from agent.services.source_control_persistence import (
    KnowledgeIndexBindingRecord,
    KnowledgeIndexRunBindingRecord,
)
from ananta_contracts.knowledge_index_execution import parse_execution_job


_BOUND_JOB_SCHEMA = "ananta.knowledge_index_execution_job.v2"
_PUBLIC_MANIFEST_SCHEMA = "ananta.codecompass.artifact-manifest.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class KnowledgeIndexSourceControlProjectionError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


class KnowledgeIndexSourceControlCompletionProjector:
    """Project one verified Worker result into the canonical lifecycle model."""

    def __init__(self, *, repository: Any, clock=time.time) -> None:
        self._repository = repository
        self._clock = clock

    def project(
        self,
        *,
        envelope: Mapping[str, Any],
        result: Mapping[str, Any],
        artifact_references: Sequence[Mapping[str, Any]],
    ) -> tuple[KnowledgeIndexBindingRecord, KnowledgeIndexRunBindingRecord]:
        if str(envelope.get("schema") or "") != _BOUND_JOB_SCHEMA:
            raise KnowledgeIndexSourceControlProjectionError(
                "knowledge_index_source_projection_job_schema_invalid"
            )
        try:
            job = parse_execution_job(
                {
                    key: value
                    for key, value in envelope.items()
                    if key != "source_access_enforcement_manifest"
                }
            )
        except Exception as exc:
            raise KnowledgeIndexSourceControlProjectionError(
                "knowledge_index_source_projection_job_invalid"
            ) from exc
        if str(result.get("status") or "") != "completed":
            raise KnowledgeIndexSourceControlProjectionError(
                "knowledge_index_source_projection_result_incomplete"
            )
        index = self._mapping(result.get("knowledge_index"))
        run = self._mapping(result.get("run"))
        index_id = str(index.get("id") or "").strip()
        run_id = str(run.get("id") or "").strip()
        if (
            not index_id
            or not run_id
            or str(index.get("status") or "") != "completed"
            or str(run.get("status") or "") != "completed"
            or str(run.get("knowledge_index_id") or "") != index_id
        ):
            raise KnowledgeIndexSourceControlProjectionError(
                "knowledge_index_source_projection_result_binding_invalid"
            )

        public_manifest = self._public_manifest(index=index, run=run)
        authority = job.authority_binding
        if (
            public_manifest.get("schema") != _PUBLIC_MANIFEST_SCHEMA
            or str(public_manifest.get("knowledge_index_id") or "")
            != index_id
            or str(public_manifest.get("run_id") or "") != run_id
            or str(public_manifest.get("source_revision_id") or "")
            != authority.source_revision_id
            or str(public_manifest.get("status") or "") != "completed"
            or not _SHA256.fullmatch(
                str(public_manifest.get("manifest_digest") or "")
            )
        ):
            raise KnowledgeIndexSourceControlProjectionError(
                "knowledge_index_source_projection_manifest_invalid"
            )
        manifest_reference = self._manifest_reference(
            artifact_references,
            knowledge_index_id=index_id,
            run_id=run_id,
        )

        revision_record = self._repository.get_scoped_revision(
            tenant_id=authority.tenant_id,
            project_id=authority.project_id,
            source_revision_id=authority.source_revision_id,
        )
        if revision_record is None:
            raise KnowledgeIndexSourceControlProjectionError(
                "knowledge_index_source_projection_revision_not_found"
            )
        revision = revision_record.contract
        if (
            revision.tenant_id != authority.tenant_id
            or revision.project_id != authority.project_id
            or revision.source_revision_id != authority.source_revision_id
        ):
            raise KnowledgeIndexSourceControlProjectionError(
                "knowledge_index_source_projection_revision_mismatch"
            )

        now = float(self._clock())
        index_created = self._epoch(index.get("created_at"), fallback=now)
        run_created = self._epoch(run.get("created_at"), fallback=now)
        completed = self._epoch(run.get("finished_at"), fallback=now)
        digest = str(manifest_reference["sha256"])
        index_binding = KnowledgeIndexBindingRecord(
            knowledge_index_id=index_id,
            tenant_id=authority.tenant_id,
            project_id=authority.project_id,
            owner_id=revision.owner_id,
            connection_id=revision.connection_id,
            source_revision_id=authority.source_revision_id,
            policy_snapshot_id=authority.policy_snapshot_id,
            policy_snapshot_digest=authority.policy_snapshot_digest,
            index_contract_version=_PUBLIC_MANIFEST_SCHEMA,
            status="completed",
            artifact_manifest_digest=digest,
            activation_requested=True,
            lock_version=1,
            created_at_epoch=index_created,
            updated_at_epoch=completed,
        )
        run_binding = KnowledgeIndexRunBindingRecord(
            index_run_id=run_id,
            knowledge_index_id=index_id,
            tenant_id=authority.tenant_id,
            project_id=authority.project_id,
            owner_id=revision.owner_id,
            source_revision_id=authority.source_revision_id,
            policy_snapshot_id=authority.policy_snapshot_id,
            policy_snapshot_digest=authority.policy_snapshot_digest,
            status="completed",
            artifact_manifest_digest=digest,
            artifacts_verified=True,
            lock_version=1,
            created_at_epoch=run_created,
            completed_at_epoch=completed,
        )
        return self._repository.project_completed_index_run(
            index=index_binding,
            run=run_binding,
        )

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, Mapping) else {}

    @classmethod
    def _public_manifest(
        cls, *, index: Mapping[str, Any], run: Mapping[str, Any]
    ) -> dict[str, Any]:
        index_manifest = cls._mapping(
            cls._mapping(index.get("index_metadata")).get(
                "artifact_manifest"
            )
        )
        run_manifest = cls._mapping(
            cls._mapping(run.get("run_metadata")).get("artifact_manifest")
        )
        if not index_manifest or index_manifest != run_manifest:
            raise KnowledgeIndexSourceControlProjectionError(
                "knowledge_index_source_projection_manifest_missing"
            )
        return index_manifest

    @staticmethod
    def _manifest_reference(
        references: Sequence[Mapping[str, Any]],
        *,
        knowledge_index_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        matches = [
            dict(reference)
            for reference in references
            if str(reference.get("role") or "") == "manifest"
            and str(reference.get("knowledge_index_id") or "")
            == knowledge_index_id
            and str(reference.get("run_id") or "") == run_id
        ]
        if (
            len(matches) != 1
            or not _SHA256.fullmatch(str(matches[0].get("sha256") or ""))
        ):
            raise KnowledgeIndexSourceControlProjectionError(
                "knowledge_index_source_projection_artifact_manifest_invalid"
            )
        return matches[0]

    @staticmethod
    def _epoch(value: Any, *, fallback: float) -> float:
        if isinstance(value, bool):
            return fallback
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return fallback
        return parsed if parsed >= 0 else fallback


__all__ = [
    "KnowledgeIndexSourceControlCompletionProjector",
    "KnowledgeIndexSourceControlProjectionError",
]
