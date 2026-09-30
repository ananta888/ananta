"""Approved, contained deletion of knowledge-index artifacts.

``ContainedArtifactDeletionService`` deletes index outputs only below the
configured Hub artifact root, after a matching purge approval, with a
crash-recoverable deletion claim.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.db_models import KnowledgeIndexDB, KnowledgeIndexRunDB
from agent.db_models.source_control import (
    ActiveKnowledgeIndexDB,
    KnowledgeIndexRunSourceBindingDB,
    KnowledgeIndexSourceBindingDB,
    SourceControlArtifactDeletionDB,
    SourceControlPurgeApprovalDB,
)
from agent.services.source_control_adapter_common import (
    SourceControlProductionAdapterError,
)
from agent.services.source_control_artifact_integrity import sha256_file
from agent.services.source_control_projection_service import (
    SourceControlPrincipal,
)


# Keep the established log channel of the production adapters module.
_LOG = logging.getLogger("agent.services.source_control_production_adapters")
_TERMINAL_RUN_STATES = frozenset(
    {"completed", "failed", "cancelled", "purged", "tombstoned"}
)


class ContainedArtifactDeletionService:
    """Delete approved index artifacts only under the configured Hub root."""

    def __init__(
        self,
        *,
        engine: Engine,
        artifact_root: str | Path,
        clock=time.time,
    ) -> None:
        self._engine = engine
        self._root = Path(artifact_root).resolve()
        self._clock = clock

    def is_deleted(self, *, knowledge_index_id: str) -> bool:
        with Session(self._engine) as db:
            row = db.get(
                SourceControlArtifactDeletionDB, knowledge_index_id
            )
            return bool(row is not None and row.state == "completed")

    def requires_deletion(self, *, knowledge_index_id: str) -> bool:
        with Session(self._engine) as db:
            binding = db.get(
                KnowledgeIndexSourceBindingDB, knowledge_index_id
            )
            if binding is None:
                return False
            if binding.artifact_manifest_digest:
                return not self.is_deleted(
                    knowledge_index_id=knowledge_index_id
                )
            return bool(
                db.exec(
                    select(KnowledgeIndexRunSourceBindingDB).where(
                        KnowledgeIndexRunSourceBindingDB.knowledge_index_id
                        == knowledge_index_id,
                        KnowledgeIndexRunSourceBindingDB.artifact_manifest_digest.is_not(
                            None
                        ),
                    )
                ).first()
            )

    def delete(
        self,
        *,
        principal: SourceControlPrincipal,
        knowledge_index_id: str,
        expected_version: int,
        approval_id: str | None,
    ) -> Mapping[str, object]:
        if "admin" not in principal.roles:
            raise SourceControlProductionAdapterError(
                "purge_admin_required", status_code=403
            )
        if not approval_id:
            raise SourceControlProductionAdapterError(
                "artifact_retention_approval_required", status_code=409
            )
        with Session(self._engine) as db:
            binding = db.exec(
                select(KnowledgeIndexSourceBindingDB).where(
                    KnowledgeIndexSourceBindingDB.knowledge_index_id
                    == knowledge_index_id,
                    KnowledgeIndexSourceBindingDB.tenant_id
                    == principal.tenant_id,
                    KnowledgeIndexSourceBindingDB.project_id
                    == principal.project_id,
                )
            ).first()
            if binding is None:
                raise SourceControlProductionAdapterError(
                    "knowledge_index_not_found", status_code=404
                )
            if (
                binding.status != "tombstoned"
                or int(binding.lock_version) != expected_version
            ):
                raise SourceControlProductionAdapterError(
                    "index_version_conflict", status_code=412
                )
            active = db.exec(
                select(ActiveKnowledgeIndexDB).where(
                    ActiveKnowledgeIndexDB.tenant_id
                    == principal.tenant_id,
                    ActiveKnowledgeIndexDB.project_id
                    == principal.project_id,
                    ActiveKnowledgeIndexDB.knowledge_index_id
                    == knowledge_index_id,
                )
            ).first()
            if active is not None:
                raise SourceControlProductionAdapterError(
                    "active_index_cannot_be_purged", status_code=409
                )
            index = db.get(KnowledgeIndexDB, knowledge_index_id)
            if index is None:
                raise SourceControlProductionAdapterError(
                    "knowledge_index_not_found", status_code=404
                )
            receipt = db.get(
                SourceControlArtifactDeletionDB, knowledge_index_id
            )
            if receipt is not None:
                if (
                    receipt.tenant_id != principal.tenant_id
                    or receipt.project_id != principal.project_id
                    or receipt.approval_id != approval_id
                ):
                    raise SourceControlProductionAdapterError(
                        "artifact_deletion_conflict", status_code=409
                    )
                if receipt.state == "completed":
                    return {
                        "status": "completed",
                        "manifest_digest": receipt.manifest_digest,
                    }
                raw_output = Path(str(index.output_dir or ""))
                if not raw_output.exists():
                    return self._recover_claimed_deletion(
                        index=index,
                        receipt=receipt,
                    )
            metadata = dict(index.index_metadata or {})
            if metadata.get("retention_released") is not True:
                raise SourceControlProductionAdapterError(
                    "artifact_retention_not_released", status_code=409
                )
            nonterminal = db.exec(
                select(KnowledgeIndexRunDB).where(
                    KnowledgeIndexRunDB.knowledge_index_id
                    == knowledge_index_id
                )
            ).all()
            if any(
                str(run.status) not in _TERMINAL_RUN_STATES
                for run in nonterminal
            ):
                raise SourceControlProductionAdapterError(
                    "artifact_run_reference_active", status_code=409
                )
            output_dir = self._contained_output(index)
            manifest_path = self._contained_manifest(
                index, output_dir
            )
            manifest_digest = sha256_file(manifest_path)
            expected_digests = {
                str(binding.artifact_manifest_digest or "")
            }
            expected_digests.update(
                str(row.artifact_manifest_digest or "")
                for row in db.exec(
                    select(KnowledgeIndexRunSourceBindingDB).where(
                        KnowledgeIndexRunSourceBindingDB.knowledge_index_id
                        == knowledge_index_id
                    )
                ).all()
            )
            expected_digests.discard("")
            if manifest_digest not in expected_digests:
                raise SourceControlProductionAdapterError(
                    "artifact_manifest_digest_mismatch", status_code=409
                )
            request_digest = hashlib.sha256(
                json.dumps(
                    {
                        "tenant_id": principal.tenant_id,
                        "project_id": principal.project_id,
                        "knowledge_index_id": knowledge_index_id,
                        "expected_version": expected_version,
                        "approval_id": approval_id,
                        "manifest_digest": manifest_digest,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("ascii")
            ).hexdigest()
            quarantine = (
                self._root
                / ".source-control-purge"
                / f"{knowledge_index_id}.{request_digest[:16]}"
            )
            receipt = db.get(
                SourceControlArtifactDeletionDB, knowledge_index_id
            )
            if receipt is not None:
                if receipt.request_digest != request_digest:
                    raise SourceControlProductionAdapterError(
                        "artifact_deletion_conflict", status_code=409
                    )
                if receipt.state == "completed":
                    return {
                        "status": "completed",
                        "manifest_digest": manifest_digest,
                    }
                db.expunge(index)
            else:
                db.add(
                    SourceControlArtifactDeletionDB(
                        knowledge_index_id=knowledge_index_id,
                        tenant_id=principal.tenant_id,
                        project_id=principal.project_id,
                        request_digest=request_digest,
                        manifest_digest=manifest_digest,
                        approval_id=approval_id,
                        quarantine_path_digest=hashlib.sha256(
                            str(quarantine).encode("utf-8")
                        ).hexdigest(),
                        state="claimed",
                        created_at_epoch=float(self._clock()),
                    )
                )
                try:
                    db.commit()
                except IntegrityError as exc:
                    db.rollback()
                    raise SourceControlProductionAdapterError(
                        "artifact_deletion_in_progress",
                        status_code=409,
                    ) from exc
                db.expunge(index)
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        if output_dir.exists():
            if quarantine.exists():
                raise SourceControlProductionAdapterError(
                    "artifact_quarantine_conflict", status_code=409
                )
            os.replace(output_dir, quarantine)
        if quarantine.exists():
            if quarantine.is_symlink() or not quarantine.is_dir():
                raise SourceControlProductionAdapterError(
                    "artifact_quarantine_invalid", status_code=409
                )
            shutil.rmtree(quarantine)
        with Session(self._engine) as db:
            stored = db.get(KnowledgeIndexDB, knowledge_index_id)
            if stored is not None:
                metadata = dict(stored.index_metadata or {})
                metadata.update(
                    {
                        "artifacts_purged": True,
                        "artifact_manifest_digest": manifest_digest,
                    }
                )
                stored.index_metadata = metadata
                stored.output_dir = None
                stored.manifest_path = None
                stored.updated_at = float(self._clock())
                db.add(stored)
            receipt = db.get(
                SourceControlArtifactDeletionDB, knowledge_index_id
            )
            if receipt is None:
                raise SourceControlProductionAdapterError(
                    "artifact_deletion_receipt_missing",
                    status_code=500,
                )
            receipt.state = "completed"
            receipt.completed_at_epoch = float(self._clock())
            db.add(receipt)
            db.commit()
        _LOG.info(
            "source_control_artifact_deleted index=%s manifest=%s actor=%s",
            knowledge_index_id,
            manifest_digest,
            principal.subject_id,
        )
        return {
            "status": "completed",
            "manifest_digest": manifest_digest,
        }

    def delete_approved(
        self,
        *,
        scope: object,
        index: object,
        expected_version: int,
        approval_id: str,
        approval_request_digest: str,
        approval_claim_id: str,
    ) -> Mapping[str, object]:
        """Defense-in-depth check before crossing the filesystem boundary."""

        tenant_id = str(getattr(scope, "tenant_id", ""))
        project_id = str(getattr(scope, "project_id", ""))
        actor_id = str(getattr(scope, "actor_id", ""))
        roles = frozenset(getattr(scope, "roles", ()))
        knowledge_index_id = str(
            getattr(index, "knowledge_index_id", "")
        )
        with Session(self._engine) as db:
            approval = db.get(
                SourceControlPurgeApprovalDB, approval_id
            )
            if (
                approval is None
                or approval.tenant_id != tenant_id
                or approval.project_id != project_id
                or approval.action != "purge"
                or approval.object_type != "knowledge_index"
                or approval.object_id != knowledge_index_id
                or approval.request_digest != approval_request_digest
                or approval.claim_id != approval_claim_id
                or approval.state not in {"claimed", "consumed"}
                or (
                    approval.state == "claimed"
                    and float(approval.claim_expires_at_epoch or 0)
                    <= float(self._clock())
                )
            ):
                raise SourceControlProductionAdapterError(
                    "purge_approval_not_claimed", status_code=409
                )

        class _Principal:
            subject_id = actor_id

        principal = _Principal()
        principal.tenant_id = tenant_id
        principal.project_id = project_id
        principal.roles = roles
        return self.delete(
            principal=principal,  # type: ignore[arg-type]
            knowledge_index_id=knowledge_index_id,
            expected_version=expected_version,
            approval_id=approval_id,
        )

    def _contained_output(self, index: KnowledgeIndexDB) -> Path:
        raw = Path(str(index.output_dir or ""))
        if not raw.is_absolute() or raw.is_symlink():
            raise SourceControlProductionAdapterError(
                "artifact_output_path_invalid", status_code=409
            )
        resolved = raw.resolve(strict=True)
        try:
            relative = resolved.relative_to(self._root)
        except ValueError as exc:
            raise SourceControlProductionAdapterError(
                "artifact_output_outside_root", status_code=409
            ) from exc
        if not relative.parts or not resolved.is_dir():
            raise SourceControlProductionAdapterError(
                "artifact_output_path_invalid", status_code=409
            )
        return resolved

    def _recover_claimed_deletion(
        self,
        *,
        index: KnowledgeIndexDB,
        receipt: SourceControlArtifactDeletionDB,
    ) -> Mapping[str, object]:
        quarantine = (
            self._root
            / ".source-control-purge"
            / (
                f"{receipt.knowledge_index_id}."
                f"{receipt.request_digest[:16]}"
            )
        )
        if (
            hashlib.sha256(str(quarantine).encode("utf-8")).hexdigest()
            != receipt.quarantine_path_digest
        ):
            raise SourceControlProductionAdapterError(
                "artifact_quarantine_digest_mismatch", status_code=409
            )
        if quarantine.exists():
            if quarantine.is_symlink() or not quarantine.is_dir():
                raise SourceControlProductionAdapterError(
                    "artifact_quarantine_invalid", status_code=409
                )
            resolved = quarantine.resolve(strict=True)
            try:
                resolved.relative_to(self._root)
            except ValueError as exc:
                raise SourceControlProductionAdapterError(
                    "artifact_quarantine_outside_root", status_code=409
                ) from exc
            shutil.rmtree(resolved)
        with Session(self._engine) as db:
            stored = db.get(
                KnowledgeIndexDB, receipt.knowledge_index_id
            )
            if stored is not None:
                metadata = dict(stored.index_metadata or {})
                metadata.update(
                    {
                        "artifacts_purged": True,
                        "artifact_manifest_digest": (
                            receipt.manifest_digest
                        ),
                    }
                )
                stored.index_metadata = metadata
                stored.output_dir = None
                stored.manifest_path = None
                stored.updated_at = float(self._clock())
                db.add(stored)
            current = db.get(
                SourceControlArtifactDeletionDB,
                receipt.knowledge_index_id,
            )
            if current is None or current.state != "claimed":
                raise SourceControlProductionAdapterError(
                    "artifact_deletion_receipt_invalid",
                    status_code=409,
                )
            current.state = "completed"
            current.completed_at_epoch = float(self._clock())
            db.add(current)
            db.commit()
        return {
            "status": "completed",
            "manifest_digest": receipt.manifest_digest,
        }

    @staticmethod
    def _contained_manifest(
        index: KnowledgeIndexDB, output_dir: Path
    ) -> Path:
        raw = Path(str(index.manifest_path or ""))
        if not raw.is_absolute() or raw.is_symlink():
            raise SourceControlProductionAdapterError(
                "artifact_manifest_path_invalid", status_code=409
            )
        resolved = raw.resolve(strict=True)
        try:
            resolved.relative_to(output_dir)
        except ValueError as exc:
            raise SourceControlProductionAdapterError(
                "artifact_manifest_outside_output", status_code=409
            ) from exc
        if not resolved.is_file():
            raise SourceControlProductionAdapterError(
                "artifact_manifest_path_invalid", status_code=409
            )
        return resolved

__all__ = [
    "ContainedArtifactDeletionService",
    "HubBoundSourceIndexSubmissionAdapter",
    "HubSourceControlOperationsAdapter",
    "PersistentGrantEffectivePolicy",
    "SQLSourceRevisionAccessCatalog",
    "ScopedEffectiveDestinationCatalog",
    "ScopedWorkerModelDestinationCatalog",
    "SourceControlProductionAdapterError",
    "build_scoped_effective_access_service",
]


__all__ = ["ContainedArtifactDeletionService"]
