"""TaskArtifactModel — COSMOS-004

Typisiertes Artifact-Schema und Service.  Baut auf den bestehenden ArtifactStore
(artifact_store.py) auf — speichert dort die rohen Bytes; dieses Modul fügt
Typen, Policy-Klassen, Lifecycle und explizite Zugriffskontrolle hinzu.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ────────────────────────────────────────────────────────────────────

class ArtifactType(str, Enum):
    INPUT_SNAPSHOT = "input_snapshot"
    CONTEXT_BUNDLE = "context_bundle"
    WORKER_PROMPT = "worker_prompt"
    WORKER_OUTPUT = "worker_output"
    DIFF_PATCH = "diff_patch"
    TEST_REPORT = "test_report"
    REVIEW_REPORT = "review_report"
    RISK_REPORT = "risk_report"
    APPROVAL_RECORD = "approval_record"
    FINAL_SUMMARY = "final_summary"


class ArtifactPolicyClass(str, Enum):
    PUBLIC = "public"           # alle Worker dürfen lesen (aber nur via explizite Freigabe)
    INTERNAL = "internal"       # nur explizit freigegebene Worker
    SENSITIVE = "sensitive"     # nur Hub + Owner
    SECRET_REF = "secret_ref"  # niemals als Artefakt gespeichert, nur Referenz


class ArtifactLifecycle(str, Enum):
    CREATED = "created"
    IN_USE = "in_use"
    ARCHIVED = "archived"
    DELETED = "deleted"


# ── Core dataclass ────────────────────────────────────────────────────────────

@dataclass
class TaskArtifact:
    artifact_id: str
    run_id: str
    artifact_type: ArtifactType
    version: int
    policy_class: ArtifactPolicyClass
    created_at: float
    created_by: str         # worker_id or "hub"
    content_hash: str       # sha256 of content; "" for SECRET_REF
    storage_ref: str        # path or object key; "" for SECRET_REF
    lifecycle: ArtifactLifecycle
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Access control ────────────────────────────────────────────────────────

    def can_be_read_by(self, worker_id: str, *, granted_artifact_ids: list[str]) -> bool:
        """Returns True only if this artifact_id is in the explicit granted list.

        The policy_class is intentionally NOT used to grant access here — the design
        doc mandates that every Worker-Run receives an explicit allowlist of artifact_ids.
        This method enforces that: no implicit access, not even for PUBLIC artifacts.
        """
        return self.artifact_id in granted_artifact_ids

    # ── Serialisation ─────────────────────────────────────────────────────────

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "run_id": self.run_id,
            "artifact_type": self.artifact_type.value,
            "version": self.version,
            "policy_class": self.policy_class.value,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "content_hash": self.content_hash,
            "storage_ref": self.storage_ref,
            "lifecycle": self.lifecycle.value,
            "metadata": dict(self.metadata),
        }

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        artifact_type: ArtifactType,
        created_by: str,
        content: bytes | str,
        policy_class: ArtifactPolicyClass = ArtifactPolicyClass.INTERNAL,
        metadata: dict | None = None,
    ) -> "TaskArtifact":
        """Factory method.

        - Computes content_hash (sha256) — unless policy_class is SECRET_REF.
        - Sets lifecycle=CREATED.
        - SECRET_REF: content_hash and storage_ref are left empty; content is never stored.
        """
        artifact_id = str(uuid.uuid4())

        if policy_class is ArtifactPolicyClass.SECRET_REF:
            content_hash = ""
            storage_ref = ""
        else:
            raw: bytes = content if isinstance(content, bytes) else content.encode("utf-8")
            content_hash = hashlib.sha256(raw).hexdigest()
            # storage_ref is set by the caller / service after persisting;
            # here we use the artifact_id as a logical reference placeholder.
            storage_ref = artifact_id

        return cls(
            artifact_id=artifact_id,
            run_id=run_id,
            artifact_type=artifact_type,
            version=1,
            policy_class=policy_class,
            created_at=time.time(),
            created_by=created_by,
            content_hash=content_hash,
            storage_ref=storage_ref,
            lifecycle=ArtifactLifecycle.CREATED,
            metadata=dict(metadata or {}),
        )


# ── Service ───────────────────────────────────────────────────────────────────

class TaskArtifactService:
    """In-memory registry of TaskArtifacts for a set of runs.

    Backed by a plain dict — no DB dependency.
    The underlying ArtifactStore (artifact_store.py) can be used separately for
    actual byte-level persistence; this service manages the typed metadata layer.
    """

    def __init__(self) -> None:
        # artifact_id → TaskArtifact
        self._artifacts: dict[str, TaskArtifact] = {}
        # artifact_id → set of worker_ids that have been granted explicit access
        self._grants: dict[str, set[str]] = {}

    # ── Create ────────────────────────────────────────────────────────────────

    def create_artifact(
        self,
        *,
        run_id: str,
        artifact_type: ArtifactType,
        created_by: str,
        content: bytes | str,
        policy_class: ArtifactPolicyClass = ArtifactPolicyClass.INTERNAL,
        metadata: dict | None = None,
    ) -> TaskArtifact:
        """Create and register a new artifact."""
        artifact = TaskArtifact.create(
            run_id=run_id,
            artifact_type=artifact_type,
            created_by=created_by,
            content=content,
            policy_class=policy_class,
            metadata=metadata,
        )
        self._artifacts[artifact.artifact_id] = artifact
        self._grants[artifact.artifact_id] = set()
        # The creator always has access (they wrote it).
        self._grants[artifact.artifact_id].add(created_by)
        return artifact

    # ── Access ────────────────────────────────────────────────────────────────

    def get_for_worker(
        self, *, worker_id: str, artifact_ids: list[str]
    ) -> list[TaskArtifact]:
        """Returns only the artifacts the worker has explicit access to.

        Filters the supplied *artifact_ids* list — artifacts not registered or not
        granted to the worker are silently omitted.

        The method builds the per-worker granted-artifact-ids list (all artifact_ids
        for which this worker has an explicit grant) before calling can_be_read_by.
        """
        # Collect all artifact_ids this worker has been explicitly granted
        worker_granted: list[str] = [
            aid for aid, workers in self._grants.items()
            if worker_id in workers
        ]
        result: list[TaskArtifact] = []
        for aid in artifact_ids:
            artifact = self._artifacts.get(aid)
            if artifact is None:
                continue
            if artifact.can_be_read_by(worker_id, granted_artifact_ids=worker_granted):
                result.append(artifact)
        return result

    # ── Grant ─────────────────────────────────────────────────────────────────

    def grant_access(self, artifact_id: str, worker_id: str) -> None:
        """Grant a specific worker access to this artifact.

        Raises KeyError if artifact_id is not registered.
        """
        if artifact_id not in self._artifacts:
            raise KeyError(f"artifact_id not found: {artifact_id!r}")
        self._grants.setdefault(artifact_id, set()).add(worker_id)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def archive(self, artifact_id: str) -> TaskArtifact:
        """Transition artifact lifecycle to ARCHIVED.

        Raises KeyError if artifact_id is not registered.
        """
        if artifact_id not in self._artifacts:
            raise KeyError(f"artifact_id not found: {artifact_id!r}")
        artifact = self._artifacts[artifact_id]
        from dataclasses import replace
        archived = replace(artifact, lifecycle=ArtifactLifecycle.ARCHIVED)
        self._artifacts[artifact_id] = archived
        return archived

    # ── Inspection ────────────────────────────────────────────────────────────

    def is_secret_ref(self, artifact: TaskArtifact) -> bool:
        """Returns True if artifact is SECRET_REF — content is never stored."""
        return artifact.policy_class is ArtifactPolicyClass.SECRET_REF

    def summary(self, run_id: str) -> dict:
        """Returns summary of all artifacts for a run: counts by type and lifecycle."""
        by_type: dict[str, int] = {}
        by_lifecycle: dict[str, int] = {}

        for artifact in self._artifacts.values():
            if artifact.run_id != run_id:
                continue
            type_key = artifact.artifact_type.value
            lc_key = artifact.lifecycle.value
            by_type[type_key] = by_type.get(type_key, 0) + 1
            by_lifecycle[lc_key] = by_lifecycle.get(lc_key, 0) + 1

        return {
            "run_id": run_id,
            "total": sum(by_type.values()),
            "by_type": by_type,
            "by_lifecycle": by_lifecycle,
        }
