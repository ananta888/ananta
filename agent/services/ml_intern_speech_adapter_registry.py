"""Pair-scoped, CAS-bound registry for local speech adapters."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from agent.repositories.ml_intern_speech_adapter_registry import (
    MlInternSpeechAdapterRepository,
    MlInternSpeechAdapterRepositoryError,
    SpeechAdapterCasMutation,
)
from agent.repositories.speech_evidence_lineage import SpeechLineageEdge, SpeechLineageNode
from agent.services.ml_intern_speech_eval_service import SpeechEvaluationDecision
from agent.services.semantic_media_audit_service import SemanticMediaAuditEvent, SemanticMediaAuditPort

_DIGEST_LENGTH = 64
_STATUSES = frozenset({"evaluated", "approved", "revoked", "deprecated", "expired"})
_DEFAULT_AUTHORITY_AUDIT: SemanticMediaAuditPort | None = None


class SpeechAdapterRegistryError(ValueError):
    def __init__(self, reason_code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.status_code = status_code


class SpeechAdapterNotFound(SpeechAdapterRegistryError):
    pass


class SpeechAdapterVersionConflict(SpeechAdapterRegistryError):
    pass


@dataclass(frozen=True)
class SpeechAdapterExportReceipt:
    export_id: str
    encrypted_artifact_ref: str
    ciphertext_sha256: str
    size_bytes: int
    encryption_scheme: str = "AES-256-GCM"


class SpeechAdapterExportPort(Protocol):
    def verify_export_consent(
        self,
        record: "SpeechAdapterRecord",
        *,
        export_consent_digest: str,
        export_consent_epoch: int,
    ) -> bool: ...

    def encrypt_export(
        self,
        record: "SpeechAdapterRecord",
        *,
        destination_ref: str,
        export_consent_digest: str,
        export_consent_epoch: int,
    ) -> SpeechAdapterExportReceipt: ...


class SpeechAdapterExportLineagePort(Protocol):
    def publish_registration(self, record: "SpeechAdapterRecord") -> None: ...

    def publish_export(
        self,
        record: "SpeechAdapterRecord",
        receipt: SpeechAdapterExportReceipt,
        *,
        export_consent_digest: str,
    ) -> None: ...


class HubSpeechAdapterExportLineage:
    """Hub-side adapter that records content-free adapter lifecycle nodes."""

    def publish_registration(self, record: "SpeechAdapterRecord") -> None:
        from agent.services.ml_intern_speech_lineage_service import get_ml_intern_speech_lineage_service
        from agent.services.voice_governance_domain import VoicePrincipal

        nodes, edges = _registration_lineage(record)
        get_ml_intern_speech_lineage_service().publish(
            VoicePrincipal(record.tenant_id, record.owner_subject),
            nodes=nodes,
            edges=edges,
        )

    def publish_export(
        self,
        record: "SpeechAdapterRecord",
        receipt: SpeechAdapterExportReceipt,
        *,
        export_consent_digest: str,
    ) -> None:
        from agent.repositories.speech_evidence_lineage import SpeechLineageEdge, SpeechLineageNode
        from agent.services.ml_intern_speech_lineage_service import get_ml_intern_speech_lineage_service
        from agent.services.voice_governance_domain import VoicePrincipal
        from ananta_contracts.speech_evidence_governance import canonical_json

        receipt_digest = hashlib.sha256(
            canonical_json(
                {
                    **asdict(receipt),
                    "export_consent_digest": export_consent_digest,
                }
            )
        ).hexdigest()
        get_ml_intern_speech_lineage_service().publish(
            VoicePrincipal(record.tenant_id, record.owner_subject),
            nodes=(
                SpeechLineageNode("adapter", record.artifact_sha256),
                SpeechLineageNode("export", receipt.ciphertext_sha256),
                SpeechLineageNode("receipt", receipt_digest),
            ),
            edges=(
                SpeechLineageEdge(
                    "adapter",
                    record.artifact_sha256,
                    "export",
                    receipt.ciphertext_sha256,
                    "exported_as",
                ),
                SpeechLineageEdge(
                    "export",
                    receipt.ciphertext_sha256,
                    "receipt",
                    receipt_digest,
                    "acknowledged_by",
                ),
            ),
        )


@dataclass
class SpeechAdapterRecord:
    adapter_id: str
    version: str
    tenant_id: str
    owner_subject: str
    pair_id: str
    direction: str
    speaker_digest: str
    scope_digest: str
    base_model_id: str
    base_model_digest: str
    backend: str
    backend_digest: str
    dataset_digest: str
    split_digest: str
    evaluation_report_digest: str
    evaluation_policy_version: str
    evaluation_passed: bool
    evaluation_approval_eligible: bool
    consent_digest: str
    consent_expires_at_ms: int
    artifact_ref: str
    artifact_sha256: str
    artifact_size_bytes: int
    expires_at_ms: int
    status: str = "evaluated"
    registry_version: int = 1
    approved_by_digest: str | None = None
    approval_reason_code: str | None = None
    approved_at_ms: int | None = None
    revoked_at_ms: int | None = None
    deprecated_at_ms: int | None = None
    expired_at_ms: int | None = None
    rollback_of_adapter_id: str | None = None
    created_at_ms: int = 0
    updated_at_ms: int = 0
    lineage: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def public_dict(self) -> dict[str, Any]:
        """Content-free API model without server paths, keys or owner identity."""

        return {
            "adapter_id": self.adapter_id,
            "version": self.version,
            "pair_id": self.pair_id,
            "direction": self.direction,
            "speaker_digest": self.speaker_digest,
            "scope_digest": self.scope_digest,
            "base_model_id": self.base_model_id,
            "base_model_digest": self.base_model_digest,
            "backend": self.backend,
            "backend_digest": self.backend_digest,
            "dataset_digest": self.dataset_digest,
            "split_digest": self.split_digest,
            "evaluation_report_digest": self.evaluation_report_digest,
            "evaluation_policy_version": self.evaluation_policy_version,
            "consent_digest": self.consent_digest,
            "consent_expires_at_ms": self.consent_expires_at_ms,
            "artifact_ref": self.artifact_ref,
            "artifact_sha256": self.artifact_sha256,
            "artifact_size_bytes": self.artifact_size_bytes,
            "expires_at_ms": self.expires_at_ms,
            "status": self.status,
            "registry_version": self.registry_version,
            "approval_reason_code": self.approval_reason_code,
            "approved_at_ms": self.approved_at_ms,
            "revoked_at_ms": self.revoked_at_ms,
            "deprecated_at_ms": self.deprecated_at_ms,
            "expired_at_ms": self.expired_at_ms,
            "rollback_of_adapter_id": self.rollback_of_adapter_id,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "lineage": [dict(event) for event in self.lineage],
        }


def _registration_lineage(
    record: SpeechAdapterRecord,
) -> tuple[tuple[SpeechLineageNode, ...], tuple[SpeechLineageEdge, ...]]:
    """Return the canonical content-free registration graph.

    The same immutable graph is staged with the registry write and later
    materialized through the lineage port.  Keeping one builder prevents the
    transactional outbox and graph projection from drifting apart.
    """

    return (
        (
            SpeechLineageNode("manifest", record.dataset_digest),
            SpeechLineageNode("split", record.split_digest),
            SpeechLineageNode("model", record.base_model_digest),
            SpeechLineageNode("evaluation", record.evaluation_report_digest),
            SpeechLineageNode("adapter", record.artifact_sha256),
        ),
        (
            SpeechLineageEdge(
                "manifest", record.dataset_digest, "split", record.split_digest, "split_into"
            ),
            SpeechLineageEdge(
                "split", record.split_digest, "adapter", record.artifact_sha256, "trained_into"
            ),
            SpeechLineageEdge(
                "model", record.base_model_digest, "adapter", record.artifact_sha256, "base_for"
            ),
            SpeechLineageEdge(
                "evaluation",
                record.evaluation_report_digest,
                "adapter",
                record.artifact_sha256,
                "evaluated_adapter",
            ),
        ),
    )


class MlInternSpeechAdapterRegistry:
    def __init__(
        self,
        path: str | Path = "artifacts/speech-adapters/registry.json",
        *,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        audit_sink: Callable[[str, dict[str, Any]], None] | None = None,
        export_lineage: SpeechAdapterExportLineagePort | None = None,
        authority_audit: SemanticMediaAuditPort | None = None,
        repository: MlInternSpeechAdapterRepository | None = None,
    ) -> None:
        self._path = Path(path)
        self._clock_ms = clock_ms
        self._audit = audit_sink or (lambda _event, _details: None)
        self._export_lineage = export_lineage or HubSpeechAdapterExportLineage()
        self._authority_audit = authority_audit or _DEFAULT_AUTHORITY_AUDIT
        self._repository = repository or MlInternSpeechAdapterRepository()
        self._import_legacy_once()

    def register_evaluated(
        self,
        *,
        adapter_id: str,
        version: str,
        tenant_id: str,
        owner_subject: str,
        pair_id: str,
        direction: str,
        speaker_digest: str,
        scope_digest: str,
        base_model_id: str,
        base_model_digest: str,
        backend: str,
        backend_digest: str,
        dataset_digest: str,
        split_digest: str,
        evaluation: SpeechEvaluationDecision,
        consent_digest: str,
        consent_expires_at_ms: int,
        artifact_ref: str,
        artifact_sha256: str,
        artifact_size_bytes: int,
        expires_at_ms: int,
    ) -> SpeechAdapterRecord:
        now = int(self._clock_ms())
        _bounded_id(adapter_id, "adapter_id")
        _bounded_id(version, "version")
        _bounded_id(tenant_id, "tenant_id")
        _bounded_id(owner_subject, "owner_subject")
        _bounded_id(pair_id, "pair_id")
        if direction not in {"sender_to_receiver", "receiver_to_sender"}:
            raise SpeechAdapterRegistryError("speech_adapter_direction_invalid", "adapter direction is invalid")
        for name, digest in {
            "speaker_digest": speaker_digest,
            "scope_digest": scope_digest,
            "base_model_digest": base_model_digest,
            "backend_digest": backend_digest,
            "dataset_digest": dataset_digest,
            "split_digest": split_digest,
            "evaluation_report_digest": evaluation.report_digest,
            "consent_digest": consent_digest,
            "artifact_sha256": artifact_sha256,
        }.items():
            _digest(digest, name)
        if not evaluation.passed:
            raise SpeechAdapterRegistryError(
                "speech_adapter_evaluation_failed",
                "failed evaluation cannot enter the adapter registry",
            )
        if not artifact_ref.startswith("artifact://speech-adapters/") or ".." in artifact_ref.split("/"):
            raise SpeechAdapterRegistryError(
                "speech_adapter_artifact_ref_invalid",
                "adapter artifact must use the opaque Hub artifact namespace",
            )
        if artifact_size_bytes <= 0 or artifact_size_bytes > 8 * 1024**3:
            raise SpeechAdapterRegistryError("speech_adapter_artifact_size_invalid", "adapter artifact size is invalid")
        if consent_expires_at_ms <= now or expires_at_ms <= now or expires_at_ms > consent_expires_at_ms:
            raise SpeechAdapterRegistryError(
                "speech_adapter_expiry_invalid",
                "adapter expiry must be current and bounded by consent",
            )
        event = _lineage_event("registered", now, owner_subject, "speech_adapter_evaluated")
        proposed = SpeechAdapterRecord(
            adapter_id=adapter_id,
            version=version,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            pair_id=pair_id,
            direction=direction,
            speaker_digest=speaker_digest,
            scope_digest=scope_digest,
            base_model_id=_bounded_id(base_model_id, "base_model_id"),
            base_model_digest=base_model_digest,
            backend=_bounded_id(backend, "backend"),
            backend_digest=backend_digest,
            dataset_digest=dataset_digest,
            split_digest=split_digest,
            evaluation_report_digest=evaluation.report_digest,
            evaluation_policy_version=evaluation.policy_version,
            evaluation_passed=evaluation.passed,
            evaluation_approval_eligible=evaluation.approval_eligible,
            consent_digest=consent_digest,
            consent_expires_at_ms=consent_expires_at_ms,
            artifact_ref=artifact_ref,
            artifact_sha256=artifact_sha256,
            artifact_size_bytes=artifact_size_bytes,
            expires_at_ms=expires_at_ms,
            created_at_ms=now,
            updated_at_ms=now,
            lineage=[event],
        )
        audit_event = self._prepare_authority(proposed, "evaluated", "speech_adapter_evaluated")
        lineage_nodes, lineage_edges = _registration_lineage(proposed)
        try:
            raw, _replayed = self._repository.create(
                proposed.to_dict(),
                audit_event=audit_event,
                lineage_nodes=lineage_nodes,
                lineage_edges=lineage_edges,
            )
        except MlInternSpeechAdapterRepositoryError as exc:
            raise _repository_error(exc) from exc
        record = _record(raw)
        self._publish_registration_lineage(record)
        self._emit("speech_adapter_registered", record, "speech_adapter_evaluated")
        return record

    def list_for_pair(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        pair_id: str,
        direction: str,
    ) -> list[SpeechAdapterRecord]:
        return [
            _record(raw)
            for raw in self._repository.list_for_pair(
                tenant_id=tenant_id,
                owner_subject=owner_subject,
                pair_id=pair_id,
                direction=direction,
            )
        ]

    def get_for_pair(
        self,
        adapter_id: str,
        *,
        tenant_id: str,
        owner_subject: str,
        pair_id: str,
        direction: str,
    ) -> SpeechAdapterRecord:
        raw = self._repository.get(
            adapter_id,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            pair_id=pair_id,
            direction=direction,
        )
        if raw is None:
            raise SpeechAdapterNotFound("speech_adapter_not_found", "speech adapter was not found", status_code=404)
        return _record(raw)

    def approve(
        self,
        adapter_id: str,
        *,
        tenant_id: str,
        owner_subject: str,
        pair_id: str,
        direction: str,
        expected_version: int,
        authorized_confirmation: bool,
        approved_by: str,
        reason_code: str,
        current_consent_digest: str,
    ) -> SpeechAdapterRecord:
        if authorized_confirmation is not True:
            raise SpeechAdapterRegistryError(
                "speech_adapter_approval_confirmation_required",
                "explicit authorized approval is required",
            )
        now = int(self._clock_ms())
        raw = self._repository.get(
            adapter_id,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            pair_id=pair_id,
            direction=direction,
        )
        if raw is None:
            raise SpeechAdapterNotFound("speech_adapter_not_found", "speech adapter was not found", status_code=404)
        if raw.get("status") == "approved":
            replay = _record(raw)
            replay_reason = replay.approval_reason_code or "speech_adapter_approved"
            self._repository.enqueue_replay((self._prepare_authority(replay, "approved", replay_reason),))
            self._emit("speech_adapter_approved", replay, replay_reason)
            return replay
        _cas(raw, expected_version)
        if raw.get("status") != "evaluated" or not raw.get("evaluation_approval_eligible"):
            raise SpeechAdapterRegistryError(
                "speech_adapter_approval_gate_failed",
                "adapter evaluation is not approval-eligible",
            )
        if raw.get("consent_digest") != current_consent_digest or int(raw.get("consent_expires_at_ms", 0)) <= now:
            raise SpeechAdapterRegistryError(
                "speech_adapter_consent_stale",
                "adapter approval requires current matching consent",
            )
        if int(raw.get("expires_at_ms", 0)) <= now:
            raise SpeechAdapterRegistryError("speech_adapter_expired", "expired adapter cannot be approved")
        raw["status"] = "approved"
        raw["approved_by_digest"] = _actor_digest(approved_by)
        raw["approval_reason_code"] = _bounded_id(reason_code, "reason_code")
        raw["approved_at_ms"] = now
        _mutated(raw, "approved", now, approved_by, reason_code)
        proposed = _record(raw)
        audit_event = self._prepare_authority(proposed, "approved", reason_code)
        try:
            record = _record(
                self._repository.replace(
                    proposed.to_dict(),
                    expected_version=expected_version,
                    audit_event=audit_event,
                )
            )
        except MlInternSpeechAdapterRepositoryError as exc:
            raise _repository_error(exc) from exc
        self._emit("speech_adapter_approved", record, reason_code)
        return record

    def change_status(
        self,
        adapter_id: str,
        *,
        target: str,
        tenant_id: str,
        owner_subject: str,
        pair_id: str,
        direction: str,
        expected_version: int,
        actor: str,
        reason_code: str,
    ) -> SpeechAdapterRecord:
        if target not in {"revoked", "deprecated", "expired"}:
            raise SpeechAdapterRegistryError("speech_adapter_transition_invalid", "adapter target status is invalid")
        now = int(self._clock_ms())
        raw = self._repository.get(
            adapter_id,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            pair_id=pair_id,
            direction=direction,
        )
        if raw is None:
            raise SpeechAdapterNotFound("speech_adapter_not_found", "speech adapter was not found", status_code=404)
        if raw.get("status") == target:
            replay = _record(raw)
            replay_reason = _latest_reason(replay, reason_code)
            self._repository.enqueue_replay((self._prepare_authority(replay, target, replay_reason),))
            self._emit(f"speech_adapter_{target}", replay, replay_reason)
            return replay
        _cas(raw, expected_version)
        current = str(raw.get("status") or "")
        allowed = {
            "revoked": {"evaluated", "approved", "deprecated"},
            "deprecated": {"evaluated", "approved"},
            "expired": {"evaluated", "approved", "deprecated"},
        }[target]
        if current not in allowed:
            raise SpeechAdapterRegistryError(
                "speech_adapter_transition_invalid",
                f"cannot transition {current} to {target}",
                status_code=409,
            )
        raw["status"] = target
        raw[f"{target}_at_ms"] = now
        _mutated(raw, target, now, actor, reason_code)
        proposed = _record(raw)
        try:
            record = _record(
                self._repository.replace(
                    proposed.to_dict(),
                    expected_version=expected_version,
                    audit_event=self._prepare_authority(proposed, target, reason_code),
                )
            )
        except MlInternSpeechAdapterRepositoryError as exc:
            raise _repository_error(exc) from exc
        self._emit(f"speech_adapter_{target}", record, reason_code)
        return record

    def fence_by_artifact_digest(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        artifact_sha256: str,
        revocation_epoch: int,
        authority: str = "hub",
    ) -> tuple[str, ...]:
        """Revoke every matching SQL-backed adapter under a Hub fence.

        Lineage carries artifact digests rather than pair-local adapter IDs;
        this additive operation therefore fences all scoped registry entries
        bound to the impacted immutable artifact.
        """

        if authority != "hub":
            raise SpeechAdapterRegistryError(
                "speech_adapter_hub_fence_required",
                "only the Hub may apply a lineage revocation fence",
                status_code=403,
            )
        _bounded_id(tenant_id, "tenant_id")
        _bounded_id(owner_subject, "owner_subject")
        _digest(artifact_sha256, "artifact_sha256")
        if type(revocation_epoch) is not int or revocation_epoch < 1:
            raise SpeechAdapterRegistryError(
                "speech_adapter_revocation_epoch_invalid",
                "revocation epoch must be a positive integer",
            )
        now = int(self._clock_ms())
        raw_records = self._repository.list_by_artifact(
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            artifact_sha256=artifact_sha256,
        )
        affected: list[SpeechAdapterRecord] = []
        mutations: list[SpeechAdapterCasMutation] = []
        replay_events: list[SemanticMediaAuditEvent | None] = []
        for raw in raw_records:
            if raw.get("status") == "revoked":
                replay = _record(raw)
                affected.append(replay)
                replay_events.append(
                    self._prepare_authority(replay, "revoked", _latest_reason(replay, "speech_evidence_revoked"))
                )
                continue
            if raw.get("status") not in {"evaluated", "approved", "deprecated", "expired"}:
                raise SpeechAdapterRegistryError(
                    "speech_adapter_transition_invalid",
                    "impacted adapter cannot be fenced",
                    status_code=409,
                )
            expected_version = int(raw["registry_version"])
            raw["status"] = "revoked"
            raw["revoked_at_ms"] = now
            _mutated(raw, "revoked", now, "hub", "speech_evidence_revoked")
            raw["lineage"][-1]["revocation_epoch"] = revocation_epoch
            proposed = _record(raw)
            affected.append(proposed)
            mutations.append(
                SpeechAdapterCasMutation(
                    proposed.to_dict(),
                    expected_version,
                    self._prepare_authority(proposed, "revoked", "speech_evidence_revoked"),
                )
            )
        try:
            if mutations:
                saved = self._repository.replace_many(mutations)
                saved_by_id = {str(raw["adapter_id"]): _record(raw) for raw in saved}
                affected = [saved_by_id.get(record.adapter_id, record) for record in affected]
            if replay_events:
                self._repository.enqueue_replay(replay_events)
        except MlInternSpeechAdapterRepositoryError as exc:
            raise _repository_error(exc) from exc
        for record in affected:
            self._emit("speech_adapter_revoked", record, "speech_evidence_revoked")
        return tuple(sorted(record.adapter_id for record in affected))

    def rollback(
        self,
        *,
        from_adapter_id: str,
        to_adapter_id: str,
        tenant_id: str,
        owner_subject: str,
        pair_id: str,
        direction: str,
        from_expected_version: int,
        to_expected_version: int,
        actor: str,
        reason_code: str,
    ) -> SpeechAdapterRecord:
        now = int(self._clock_ms())
        current = self._repository.get(
            from_adapter_id,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            pair_id=pair_id,
            direction=direction,
        )
        target = self._repository.get(
            to_adapter_id,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            pair_id=pair_id,
            direction=direction,
        )
        if current is None or target is None:
            raise SpeechAdapterNotFound("speech_adapter_not_found", "speech adapter was not found", status_code=404)
        if target.get("status") == "approved" and target.get("rollback_of_adapter_id") == from_adapter_id:
            replay = _record(target)
            current_record = _record(current)
            replay_reason = _latest_reason(replay, reason_code)
            self._repository.enqueue_replay(
                (
                    self._prepare_authority(
                        current_record,
                        "deprecated",
                        _latest_reason(current_record, replay_reason),
                    ),
                    self._prepare_authority(replay, "approved", replay_reason),
                )
            )
            self._emit("speech_adapter_rollback", replay, replay_reason)
            return replay
        _cas(current, from_expected_version)
        _cas(target, to_expected_version)
        immutable_keys = ("scope_digest", "base_model_digest", "backend_digest")
        if any(current.get(key) != target.get(key) for key in immutable_keys):
            raise SpeechAdapterRegistryError(
                "speech_adapter_rollback_binding_mismatch",
                "rollback target does not share the adapter execution scope",
            )
        if current.get("status") != "approved" or target.get("status") != "deprecated":
            raise SpeechAdapterRegistryError(
                "speech_adapter_rollback_state_invalid",
                "rollback requires an approved current and deprecated previous adapter",
                status_code=409,
            )
        if int(target.get("expires_at_ms", 0)) <= now or int(target.get("consent_expires_at_ms", 0)) <= now:
            raise SpeechAdapterRegistryError("speech_adapter_rollback_expired", "rollback target has expired")
        current["status"] = "deprecated"
        current["deprecated_at_ms"] = now
        _mutated(current, "rollback_from", now, actor, reason_code)
        target["status"] = "approved"
        target["rollback_of_adapter_id"] = from_adapter_id
        target["approved_by_digest"] = _actor_digest(actor)
        target["approved_at_ms"] = now
        _mutated(target, "rollback_to", now, actor, reason_code)
        current_record = _record(current)
        target_record = _record(target)
        try:
            saved = self._repository.replace_many(
                (
                    SpeechAdapterCasMutation(
                        current_record.to_dict(),
                        from_expected_version,
                        self._prepare_authority(current_record, "deprecated", reason_code),
                    ),
                    SpeechAdapterCasMutation(
                        target_record.to_dict(),
                        to_expected_version,
                        self._prepare_authority(target_record, "approved", reason_code),
                    ),
                )
            )
        except MlInternSpeechAdapterRepositoryError as exc:
            raise _repository_error(exc) from exc
        record = _record(saved[1])
        self._emit("speech_adapter_rollback", record, reason_code)
        return record

    def export_encrypted(
        self,
        adapter_id: str,
        *,
        tenant_id: str,
        owner_subject: str,
        pair_id: str,
        direction: str,
        expected_version: int,
        export_consent_digest: str,
        export_consent_epoch: int,
        destination_ref: str,
        export_port: SpeechAdapterExportPort,
    ) -> SpeechAdapterExportReceipt:
        record = self.get_for_pair(
            adapter_id,
            tenant_id=tenant_id,
            owner_subject=owner_subject,
            pair_id=pair_id,
            direction=direction,
        )
        _cas(record.to_dict(), expected_version)
        now = int(self._clock_ms())
        if record.status != "approved" or now >= min(record.expires_at_ms, record.consent_expires_at_ms):
            raise SpeechAdapterRegistryError(
                "speech_adapter_export_forbidden",
                "only current approved adapters can export",
            )
        _digest(export_consent_digest, "export_consent_digest")
        if (
            isinstance(export_consent_epoch, bool)
            or not isinstance(export_consent_epoch, int)
            or not 1 <= export_consent_epoch <= 2_147_483_647
        ):
            raise SpeechAdapterRegistryError(
                "speech_adapter_export_consent_epoch_invalid",
                "a positive export-consent epoch is required",
            )
        try:
            consent_verified = export_port.verify_export_consent(
                record,
                export_consent_digest=export_consent_digest,
                export_consent_epoch=export_consent_epoch,
            )
        except Exception as exc:
            raise SpeechAdapterRegistryError(
                "speech_adapter_export_consent_unavailable",
                "speech adapter export consent could not be verified",
                status_code=503,
            ) from exc
        if not consent_verified:
            raise SpeechAdapterRegistryError(
                "speech_adapter_export_consent_missing",
                "current separate export consent is required",
            )
        if not destination_ref.startswith("artifact://speech-adapter-exports/") or ".." in destination_ref.split("/"):
            raise SpeechAdapterRegistryError("speech_adapter_export_target_invalid", "export target is invalid")
        try:
            receipt = export_port.encrypt_export(
                record,
                destination_ref=destination_ref,
                export_consent_digest=export_consent_digest,
                export_consent_epoch=export_consent_epoch,
            )
        except Exception as exc:
            raise SpeechAdapterRegistryError(
                str(getattr(exc, "reason_code", "speech_adapter_export_failed"))[:128],
                "encrypted speech adapter export failed",
                status_code=503,
            ) from exc
        if (
            not receipt.encrypted_artifact_ref.startswith("artifact://speech-adapter-exports/")
            or receipt.encryption_scheme != "AES-256-GCM"
        ):
            raise SpeechAdapterRegistryError(
                "speech_adapter_export_receipt_invalid",
                "export port returned an unsafe receipt",
            )
        try:
            self._export_lineage.publish_export(
                record,
                receipt,
                export_consent_digest=export_consent_digest,
            )
        except Exception as exc:
            raise SpeechAdapterRegistryError(
                str(getattr(exc, "reason_code", "speech_adapter_export_lineage_failed"))[:128],
                "speech adapter export lineage could not be committed",
                status_code=503,
            ) from exc
        self._emit("speech_adapter_exported", record, "speech_adapter_export_consent_verified")
        return receipt

    def _read_legacy(self) -> tuple[bytes, list[dict[str, Any]]]:
        if not self._path.exists():
            return b"", []
        try:
            raw_bytes = self._path.read_bytes()
            payload = json.loads(raw_bytes, parse_constant=_reject_non_finite)
        except (OSError, ValueError) as exc:
            raise SpeechAdapterRegistryError(
                "speech_adapter_registry_corrupt",
                "speech adapter registry could not be loaded",
                status_code=500,
            ) from exc
        if not isinstance(payload, dict) or payload.get("schema") != "ananta.speech-adapter-registry.v1":
            raise SpeechAdapterRegistryError(
                "speech_adapter_registry_schema_invalid",
                "speech adapter registry schema is invalid",
                status_code=500,
            )
        records = payload.get("adapters")
        if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
            raise SpeechAdapterRegistryError(
                "speech_adapter_registry_shape_invalid",
                "speech adapter registry records are invalid",
                status_code=500,
            )
        return raw_bytes, [dict(item) for item in records]

    def _import_legacy_once(self) -> None:
        raw_bytes, raw_records = self._read_legacy()
        if not raw_bytes:
            return
        records = [_record(raw) for raw in raw_records]
        events = [
            self._prepare_authority(record, "legacy_imported", "legacy_registry_imported")
            for record in records
        ]
        try:
            self._repository.import_legacy_once(
                source_digest=hashlib.sha256(raw_bytes).hexdigest(),
                records=[record.to_dict() for record in records],
                audit_events=events,
                imported_at_ms=int(self._clock_ms()),
            )
        except MlInternSpeechAdapterRepositoryError as exc:
            raise _repository_error(exc) from exc

    def _emit(self, event: str, record: SpeechAdapterRecord, reason_code: str) -> None:
        self._audit(
            event,
            {
                "adapter_id": record.adapter_id,
                "scope_digest": record.scope_digest,
                "artifact_sha256": record.artifact_sha256,
                "registry_version": record.registry_version,
                "status": record.status,
                "reason_code": reason_code,
            },
        )

    def _prepare_authority(
        self,
        record: SpeechAdapterRecord,
        transition: str,
        reason_code: str,
    ) -> SemanticMediaAuditEvent | None:
        if self._authority_audit is None:
            return None
        try:
            return self._authority_audit.prepare_transition(
                idempotency_key=(
                    f"speech-adapter:{transition}:{record.adapter_id}:{record.registry_version}"
                ),
                tenant_id=record.tenant_id,
                scope=f"semantic-media-session:{record.pair_id}",
                event_type="speech_adapter",
                transition=transition,
                reason_code=reason_code,
                epoch=max(1, record.registry_version),
                contract_ref=record.artifact_sha256,
                job_ref=record.adapter_id,
            )
        except Exception as exc:
            raise SpeechAdapterRegistryError(
                "semantic_audit_unavailable",
                "speech adapter audit is unavailable",
                status_code=503,
            ) from exc

    def _publish_registration_lineage(self, record: SpeechAdapterRecord) -> None:
        try:
            self._export_lineage.publish_registration(record)
        except Exception as exc:
            raise SpeechAdapterRegistryError(
                str(getattr(exc, "reason_code", "speech_adapter_registration_lineage_failed"))[:128],
                "speech adapter registration lineage could not be committed",
                status_code=503,
            ) from exc


def _latest_reason(record: SpeechAdapterRecord, fallback: str) -> str:
    if not record.lineage:
        return fallback
    return str(record.lineage[-1].get("reason_code") or fallback)


def _record(raw: dict[str, Any]) -> SpeechAdapterRecord:
    allowed = set(SpeechAdapterRecord.__dataclass_fields__)
    if set(raw) != allowed or raw.get("status") not in _STATUSES:
        raise SpeechAdapterRegistryError(
            "speech_adapter_record_invalid",
            "speech adapter registry record is invalid",
            status_code=500,
        )
    return SpeechAdapterRecord(**raw)


def _repository_error(exc: MlInternSpeechAdapterRepositoryError) -> SpeechAdapterRegistryError:
    reason = exc.reason_code
    if reason == "speech_adapter_not_found":
        return SpeechAdapterNotFound(reason, "speech adapter was not found", status_code=404)
    if reason in {"speech_adapter_version_conflict", "speech_adapter_invalid_next_version"}:
        return SpeechAdapterVersionConflict(
            "speech_adapter_version_conflict",
            "speech adapter changed since it was read",
            status_code=409,
        )
    return SpeechAdapterRegistryError(
        reason,
        "speech adapter authority rejected the mutation",
        status_code=409,
    )


def _cas(raw: dict[str, Any], expected: int) -> None:
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
        raise SpeechAdapterRegistryError("speech_adapter_expected_version_invalid", "expected version is required")
    if int(raw.get("registry_version", 0)) != expected:
        raise SpeechAdapterVersionConflict(
            "speech_adapter_version_conflict",
            "speech adapter changed since it was read",
            status_code=409,
        )


def _mutated(raw: dict[str, Any], event: str, now: int, actor: str, reason_code: str) -> None:
    raw["registry_version"] = int(raw.get("registry_version", 0)) + 1
    raw["updated_at_ms"] = now
    raw.setdefault("lineage", []).append(_lineage_event(event, now, actor, reason_code))


def _lineage_event(event: str, now: int, actor: str, reason_code: str) -> dict[str, Any]:
    return {
        "event": _bounded_id(event, "lineage_event"),
        "at_ms": now,
        "actor_digest": _actor_digest(actor),
        "reason_code": _bounded_id(reason_code, "reason_code"),
    }


def _actor_digest(actor: str) -> str:
    value = _bounded_id(actor, "actor")
    return hashlib.sha256(value.encode()).hexdigest()


def _bounded_id(value: str, field: str) -> str:
    result = str(value or "").strip()
    if not result or len(result) > 192 or any(character.isspace() for character in result):
        raise SpeechAdapterRegistryError(f"{field}_invalid", f"{field} is invalid")
    return result


def _digest(value: str, field: str) -> str:
    result = str(value or "").strip()
    if len(result) != _DIGEST_LENGTH or any(character not in "0123456789abcdef" for character in result):
        raise SpeechAdapterRegistryError(f"{field}_invalid", f"{field} must be a lowercase SHA-256 digest")
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite registry value {value!r}")


def configure_ml_intern_speech_adapter_audit(audit: SemanticMediaAuditPort | None) -> None:
    """Configure the Hub recorder used by non-route adapter compositions."""

    global _DEFAULT_AUTHORITY_AUDIT
    _DEFAULT_AUTHORITY_AUDIT = audit
