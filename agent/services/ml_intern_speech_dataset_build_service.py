"""Build and persist immutable, content-addressed speech dataset manifests."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

import jsonschema
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models.speech_evidence import (
    SpeechDatasetManifestDB,
    SpeechEvidenceConsentDB,
    SpeechEvidenceDB,
)
from agent.repositories.semantic_media_audit_outbox import SqlSemanticMediaAuditOutbox
from agent.repositories.speech_evidence_lineage import (
    SpeechEvidenceLineageRepository,
    SpeechLineageEdge,
    SpeechLineageNode,
    get_speech_evidence_lineage_repository,
)
from agent.services.ml_intern_speech_dataset_port import (
    MlInternSpeechDatasetPort,
    MlInternSpeechDatasetPortError,
    UnavailableMlInternSpeechDatasetPort,
)
from agent.services.semantic_media_audit_service import SemanticMediaAuditPort
from agent.services.speech_evidence_consent_service import (
    SpeechEvidenceConsentService,
    get_speech_evidence_consent_service,
)
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_evidence_governance import canonical_json


class SpeechDatasetManifestError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, status_code: int = 422) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(message)


class SpeechDatasetConsentPort(Protocol):
    def authorize_import(
        self,
        principal: VoicePrincipal,
        *,
        consent_ref: Mapping[str, object],
        data_classes: Sequence[str],
    ) -> None: ...


class SpeechDatasetEvidenceFencePort(Protocol):
    """Serialize evidence invalidation against canonical dataset publication."""

    def fence_inputs(
        self,
        session: Session,
        principal: VoicePrincipal,
        *,
        records: Sequence[Mapping[str, object]],
    ) -> None: ...


class UncheckedSpeechDatasetEvidenceFence:
    """Compatibility port for datasets whose records are not Hub evidence rows."""

    def fence_inputs(
        self,
        session: Session,
        principal: VoicePrincipal,
        *,
        records: Sequence[Mapping[str, object]],
    ) -> None:
        del session, principal, records


class HubSpeechDatasetEvidenceFence:
    """Require every evidence input to remain admitted at the commit boundary.

    The compare-and-no-op update is the SQLite writer fence and complements
    ``FOR UPDATE`` on row-locking databases.  Whichever side wins between an
    offer/revocation fence and dataset publication is therefore observable:
    publication either commits first and is included in lineage impact, or it
    fails before the manifest becomes visible.
    """

    def fence_inputs(
        self,
        session: Session,
        principal: VoicePrincipal,
        *,
        records: Sequence[Mapping[str, object]],
    ) -> None:
        evidence_digests = sorted(
            {str(record["record_digest"]) for record in records if record.get("lineage_kind") == "evidence"}
        )
        for evidence_digest in evidence_digests:
            current = session.exec(
                select(SpeechEvidenceDB)
                .where(
                    SpeechEvidenceDB.tenant_id == principal.tenant_id,
                    SpeechEvidenceDB.owner_subject == principal.subject,
                    SpeechEvidenceDB.content_digest == evidence_digest,
                )
                .with_for_update()
            ).first()
            if current is None or current.state != "admitted":
                raise SpeechDatasetManifestError(
                    "speech_dataset_evidence_fenced",
                    "dataset evidence is missing or no longer admitted",
                    status_code=410,
                )
            fenced = session.exec(
                update(SpeechEvidenceDB)
                .where(
                    SpeechEvidenceDB.id == current.id,
                    SpeechEvidenceDB.tenant_id == principal.tenant_id,
                    SpeechEvidenceDB.owner_subject == principal.subject,
                    SpeechEvidenceDB.content_digest == evidence_digest,
                    SpeechEvidenceDB.state == "admitted",
                )
                .values(updated_at_ms=SpeechEvidenceDB.updated_at_ms)
            )
            if fenced.rowcount != 1:
                raise SpeechDatasetManifestError(
                    "speech_dataset_evidence_fenced",
                    "dataset evidence changed before publication",
                    status_code=410,
                )

    def fence_imports(
        self,
        session: Session,
        principal: VoicePrincipal,
        *,
        records: Sequence[Mapping[str, object]],
    ) -> None: ...


class HubSpeechDatasetConsentAuthority:
    """Revalidate dataset-import authority at the curation boundary."""

    def __init__(
        self,
        consent: SpeechEvidenceConsentService | None = None,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._consent = consent or get_speech_evidence_consent_service()
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def authorize_import(
        self,
        principal: VoicePrincipal,
        *,
        consent_ref: Mapping[str, object],
        data_classes: Sequence[str],
    ) -> None:
        try:
            current = self._consent.get(principal, str(consent_ref.get("consent_id") or ""))
        except Exception as exc:
            raise SpeechDatasetManifestError(
                "speech_dataset_import_consent_stale",
                "dataset import consent is missing, stale or too narrow",
                status_code=403,
            ) from exc
        if (
            current.state != "active"
            or current.expires_at_ms <= self._clock_ms()
            or current.consent_version != consent_ref.get("consent_version")
            or current.revocation_epoch != consent_ref.get("revocation_epoch")
            or current.consent_digest != consent_ref.get("consent_digest")
            or current.grants.get("dataset_import") is not True
            or not set(data_classes) <= set(current.data_classes)
        ):
            raise SpeechDatasetManifestError(
                "speech_dataset_import_consent_stale",
                "dataset import consent is missing, stale or too narrow",
                status_code=403,
            )

    def fence_imports(
        self,
        session: Session,
        principal: VoicePrincipal,
        *,
        records: Sequence[Mapping[str, object]],
    ) -> None:
        now = self._clock_ms()
        checked: set[tuple[str, int, int, str]] = set()
        for record in records:
            data_classes = tuple(str(value) for value in record["data_classes"])
            for raw_ref in record["consent_refs"]:
                consent_ref = dict(raw_ref)
                consent_version = consent_ref.get("consent_version")
                revocation_epoch = consent_ref.get("revocation_epoch")
                binding = (
                    str(consent_ref.get("consent_id") or ""),
                    consent_version if type(consent_version) is int else -1,
                    revocation_epoch if type(revocation_epoch) is int else -1,
                    str(consent_ref.get("consent_digest") or ""),
                )
                row = session.exec(
                    select(SpeechEvidenceConsentDB)
                    .where(
                        SpeechEvidenceConsentDB.id == binding[0],
                        SpeechEvidenceConsentDB.tenant_id == principal.tenant_id,
                        SpeechEvidenceConsentDB.owner_subject == principal.subject,
                    )
                    .with_for_update()
                ).first()
                scope = dict(row.scope_payload or {}) if row is not None else {}
                if row is None or (
                    row.state != "active"
                    or row.expires_at_ms <= now
                    or row.consent_version != binding[1]
                    or row.revocation_epoch != binding[2]
                    or row.consent_digest != binding[3]
                    or dict(scope.get("grants") or {}).get("dataset_import") is not True
                    or not set(data_classes) <= set(scope.get("data_classes") or [])
                ):
                    raise SpeechDatasetManifestError(
                        "speech_dataset_import_consent_stale",
                        "dataset import consent changed before publication",
                        status_code=403,
                    )
                if binding in checked:
                    continue
                checked.add(binding)
                fenced = session.exec(
                    update(SpeechEvidenceConsentDB)
                    .where(
                        SpeechEvidenceConsentDB.id == binding[0],
                        SpeechEvidenceConsentDB.tenant_id == principal.tenant_id,
                        SpeechEvidenceConsentDB.owner_subject == principal.subject,
                        SpeechEvidenceConsentDB.state == "active",
                        SpeechEvidenceConsentDB.consent_version == binding[1],
                        SpeechEvidenceConsentDB.revocation_epoch == binding[2],
                        SpeechEvidenceConsentDB.consent_digest == binding[3],
                        SpeechEvidenceConsentDB.expires_at_ms > now,
                    )
                    .values(consent_version=SpeechEvidenceConsentDB.consent_version)
                )
                if fenced.rowcount != 1:
                    raise SpeechDatasetManifestError(
                        "speech_dataset_import_consent_stale",
                        "dataset import consent changed before publication",
                        status_code=403,
                    )


class MlInternSpeechDatasetBuildService:
    SCHEMA = "ananta.speech-dataset-manifest.v1"
    ALGORITHM_VERSION = "speech-dataset-builder-v1"

    def __init__(
        self,
        *,
        publisher: MlInternSpeechDatasetPort | None = None,
        clock_ms: Callable[[], int] | None = None,
        schema_path: Path | None = None,
        lineage: SpeechEvidenceLineageRepository | None = None,
        consent_authority: SpeechDatasetConsentPort | None = None,
        evidence_fence: SpeechDatasetEvidenceFencePort | None = None,
        audit: SemanticMediaAuditPort | None = None,
    ) -> None:
        self._publisher = publisher or UnavailableMlInternSpeechDatasetPort()
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        path = schema_path or Path(__file__).parents[2] / "schemas/voice/speech_dataset_manifest.v1.json"
        self._schema = json.loads(path.read_text(encoding="utf-8"))
        self._validator = jsonschema.Draft202012Validator(self._schema)
        self._record_validator = jsonschema.Draft202012Validator(self._schema["properties"]["records"]["items"])
        self._lineage = lineage or get_speech_evidence_lineage_repository()
        self._consent_authority = consent_authority or HubSpeechDatasetConsentAuthority()
        self._evidence_fence = evidence_fence or UncheckedSpeechDatasetEvidenceFence()
        self._audit = audit

    def build(
        self,
        principal: VoicePrincipal,
        *,
        dataset_id: str,
        records: Sequence[Mapping[str, object]],
        curation_report_digest: str,
        curation_summary: Mapping[str, object] | None = None,
        parent_digest: str | None = None,
        authority: str = "hub",
    ) -> tuple[dict[str, object], bool]:
        if authority != "hub":
            raise SpeechDatasetManifestError(
                "speech_dataset_hub_authority_required", "only Hub may build datasets", status_code=403
            )
        if not 1 <= len(records) <= 10_000:
            raise SpeechDatasetManifestError("speech_dataset_record_limit_invalid", "record count is out of range")
        if not _digest(curation_report_digest) or (parent_digest is not None and not _digest(parent_digest)):
            raise SpeechDatasetManifestError("speech_dataset_digest_invalid", "dataset digest is invalid")
        normalized = [_normalize_record(record) for record in records]
        self._validate_records(normalized)
        normalized_curation = None if curation_summary is None else json.loads(canonical_json(dict(curation_summary)))
        _validate_curation_binding(
            normalized,
            normalized_curation,
            curation_report_digest=curation_report_digest,
        )
        self._authorize_imports(principal, normalized)
        self._assert_lineage(normalized)
        contributors = sorted({value for record in normalized for value in record["contributors"]})
        split_groups = sorted(
            {
                str(value)
                for record in normalized
                for value in (
                    record["session_group_id"],
                    record["utterance_family_id"].split(":", 1)[1],
                    record["source_digest"],
                    record["near_duplicate_group_id"],
                )
            }
        )
        unsigned: dict[str, object] = {
            "schema": self.SCHEMA,
            "dataset_id": dataset_id,
            "parent_digest": parent_digest,
            "algorithm_version": self.ALGORITHM_VERSION,
            "contributors": contributors,
            "records": normalized,
            "split_groups": split_groups,
            "curation_report_digest": curation_report_digest,
        }
        if normalized_curation is not None:
            unsigned["curation_summary"] = normalized_curation
        manifest_digest = hashlib.sha256(canonical_json(unsigned)).hexdigest()
        manifest = {
            **unsigned,
            "version": f"sha256:{manifest_digest}",
            "manifest_digest": manifest_digest,
        }
        self.validate(manifest)
        audit_event = self._prepare_audit(principal, manifest)
        existing = self.get_by_digest(principal, manifest_digest)
        if existing is not None:
            if existing != manifest:
                raise SpeechDatasetManifestError(
                    "speech_dataset_manifest_digest_conflict", "manifest digest is already bound", status_code=409
                )
            if audit_event is not None:
                self._audit.append_prepared(audit_event)
            return existing, False
        self._assert_parent(principal, dataset_id, parent_digest)
        created_at_ms = self._clock_ms()
        consent_refs = sorted({str(ref["consent_id"]) for record in normalized for ref in record["consent_refs"]})
        max_epoch = max(int(ref["revocation_epoch"]) for record in normalized for ref in record["consent_refs"])
        row = SpeechDatasetManifestDB(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            dataset_id=dataset_id,
            version=str(manifest["version"]),
            parent_digest=parent_digest,
            manifest_digest=manifest_digest,
            manifest_payload=copy.deepcopy(manifest),
            record_count=len(normalized),
            consent_refs=consent_refs,
            revocation_epoch=max_epoch,
            created_at_ms=created_at_ms,
        )
        lineage_nodes = [
            SpeechLineageNode("manifest", manifest_digest, revocation_epoch=max_epoch),
            SpeechLineageNode("evaluation", curation_report_digest, revocation_epoch=max_epoch),
            *[
                SpeechLineageNode(
                    str(record["lineage_kind"]),
                    str(record["record_digest"]),
                    consent_id=str(record["consent_refs"][0]["consent_id"]),
                    revocation_epoch=max(int(ref["revocation_epoch"]) for ref in record["consent_refs"]),
                )
                for record in normalized
            ],
        ]
        lineage_edges = [
            SpeechLineageEdge("evaluation", curation_report_digest, "manifest", manifest_digest, "curated_as"),
            *[
                SpeechLineageEdge(
                    str(record["lineage_kind"]),
                    str(record["record_digest"]),
                    "manifest",
                    manifest_digest,
                    "included_in",
                )
                for record in normalized
            ],
        ]
        if parent_digest is not None:
            lineage_nodes.append(SpeechLineageNode("manifest", parent_digest))
            lineage_edges.append(SpeechLineageEdge("manifest", parent_digest, "manifest", manifest_digest, "parent_of"))
        try:
            with Session(engine) as session:
                self._consent_authority.fence_imports(session, principal, records=normalized)
                self._evidence_fence.fence_inputs(session, principal, records=normalized)
                self._assert_parent_in_session(session, principal, dataset_id, parent_digest)
                try:
                    published = self._publisher.publish_manifest(
                        tenant_id=principal.tenant_id,
                        owner_subject=principal.subject,
                        manifest=manifest,
                    )
                except MlInternSpeechDatasetPortError as exc:
                    raise SpeechDatasetManifestError(exc.reason_code, str(exc), status_code=503) from exc
                if published is not True:
                    raise SpeechDatasetManifestError(
                        "speech_dataset_publish_rejected",
                        "dataset manifest publication failed",
                        status_code=503,
                    )
                session.add(row)
                event_digest = self._lineage.stage(
                    session,
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    nodes=lineage_nodes,
                    edges=lineage_edges,
                    now_ms=created_at_ms,
                )
                if audit_event is not None:
                    SqlSemanticMediaAuditOutbox.enqueue_in_session(session, audit_event)
                session.commit()
        except IntegrityError as exc:
            concurrent = self.get_by_digest(principal, manifest_digest)
            if concurrent == manifest:
                return concurrent, False
            raise SpeechDatasetManifestError(
                "speech_dataset_manifest_write_conflict", str(exc), status_code=409
            ) from exc
        self._lineage.process_outbox(
            event_digest=event_digest,
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
        )
        return copy.deepcopy(manifest), True

    def _prepare_audit(self, principal: VoicePrincipal, manifest: Mapping[str, object]):
        if self._audit is None:
            return None
        digest = str(manifest.get("manifest_digest") or "")
        dataset_id = str(manifest.get("dataset_id") or "")
        records = manifest.get("records")
        epoch = 1
        if isinstance(records, list):
            epochs = [
                int(ref.get("revocation_epoch") or 0)
                for record in records
                if isinstance(record, Mapping)
                for ref in record.get("consent_refs", ())
                if isinstance(ref, Mapping)
            ]
            epoch = max([1, *epochs])
        try:
            return self._audit.prepare_transition(
                idempotency_key=f"speech-dataset:published:{digest}",
                tenant_id=principal.tenant_id,
                scope=f"speech-job:{dataset_id}",
                event_type="speech_dataset",
                transition="published",
                reason_code="speech_dataset_curated",
                epoch=epoch,
                job_ref=digest,
            )
        except Exception as exc:
            raise SpeechDatasetManifestError(
                "semantic_audit_unavailable",
                "speech dataset audit is unavailable",
                status_code=503,
            ) from exc

    def validate(self, manifest: Mapping[str, object]) -> None:
        errors = sorted(self._validator.iter_errors(dict(manifest)), key=lambda item: list(item.path))
        if errors:
            raise SpeechDatasetManifestError("speech_dataset_manifest_schema_invalid", errors[0].message)
        payload = dict(manifest)
        claimed = str(payload.pop("manifest_digest"))
        version = str(payload.pop("version"))
        actual = hashlib.sha256(canonical_json(payload)).hexdigest()
        if claimed != actual or version != f"sha256:{actual}":
            raise SpeechDatasetManifestError(
                "speech_dataset_manifest_tampered", "manifest content address does not match"
            )
        records = [dict(record) for record in manifest["records"]]
        summary = manifest.get("curation_summary")
        _validate_curation_binding(
            records,
            dict(summary) if isinstance(summary, Mapping) else None,
            curation_report_digest=str(manifest["curation_report_digest"]),
        )
        self._assert_lineage(records)
        _forbid_sensitive_values(manifest)

    def get_by_digest(self, principal: VoicePrincipal, digest: str) -> dict[str, object] | None:
        with Session(engine) as session:
            row = session.exec(
                select(SpeechDatasetManifestDB).where(
                    SpeechDatasetManifestDB.tenant_id == principal.tenant_id,
                    SpeechDatasetManifestDB.owner_subject == principal.subject,
                    SpeechDatasetManifestDB.manifest_digest == digest,
                )
            ).first()
            return copy.deepcopy(dict(row.manifest_payload)) if row is not None else None

    @staticmethod
    def _assert_lineage(records: Sequence[Mapping[str, object]]) -> None:
        record_digests: set[str] = set()
        source_groups: dict[str, tuple[str, str]] = {}
        for record in records:
            digest = str(record["record_digest"])
            if digest in record_digests:
                raise SpeechDatasetManifestError("speech_dataset_duplicate_record", "record digest is duplicated")
            record_digests.add(digest)
            source = str(record["source_digest"])
            binding = (str(record["session_group_id"]), str(record["utterance_family_id"]))
            previous = source_groups.setdefault(source, binding)
            if previous != binding:
                raise SpeechDatasetManifestError(
                    "speech_dataset_duplicate_lineage", "one source digest has conflicting session or utterance lineage"
                )
            contributors = set(record["contributors"])
            for provenance in dict(record["field_provenance"]).values():
                if str(provenance["contributor_digest"]) not in contributors:
                    raise SpeechDatasetManifestError(
                        "speech_dataset_field_provenance_invalid", "field provenance references an unknown contributor"
                    )

    def _authorize_imports(
        self,
        principal: VoicePrincipal,
        records: Sequence[Mapping[str, object]],
    ) -> None:
        for record in records:
            for consent_ref in record["consent_refs"]:
                self._consent_authority.authorize_import(
                    principal,
                    consent_ref=dict(consent_ref),
                    data_classes=tuple(str(value) for value in record["data_classes"]),
                )

    def _validate_records(self, records: Sequence[Mapping[str, object]]) -> None:
        for record in records:
            errors = sorted(self._record_validator.iter_errors(record), key=lambda item: list(item.path))
            if errors:
                raise SpeechDatasetManifestError(
                    "speech_dataset_manifest_schema_invalid",
                    errors[0].message,
                )
            _forbid_sensitive_values(record)

    @staticmethod
    def _assert_parent(principal: VoicePrincipal, dataset_id: str, parent_digest: str | None) -> None:
        with Session(engine) as session:
            MlInternSpeechDatasetBuildService._assert_parent_in_session(
                session,
                principal,
                dataset_id,
                parent_digest,
            )

    @staticmethod
    def _assert_parent_in_session(
        session: Session,
        principal: VoicePrincipal,
        dataset_id: str,
        parent_digest: str | None,
    ) -> None:
        latest = session.exec(
            select(SpeechDatasetManifestDB)
            .where(
                SpeechDatasetManifestDB.tenant_id == principal.tenant_id,
                SpeechDatasetManifestDB.owner_subject == principal.subject,
                SpeechDatasetManifestDB.dataset_id == dataset_id,
            )
            .order_by(SpeechDatasetManifestDB.created_at_ms.desc())
            .with_for_update()
        ).first()
        if latest is None and parent_digest is not None:
            raise SpeechDatasetManifestError(
                "speech_dataset_parent_not_found", "first dataset version cannot name a parent"
            )
        if latest is not None and parent_digest != latest.manifest_digest:
            raise SpeechDatasetManifestError(
                "speech_dataset_parent_mismatch", "new version must extend the current parent", status_code=409
            )


def _normalize_record(record: Mapping[str, object]) -> dict[str, object]:
    # Round-trip through canonical JSON removes custom Mapping subclasses and
    # rejects non-finite values before schema validation.
    try:
        normalized = json.loads(canonical_json(dict(record)))
        normalized.setdefault("lineage_kind", "evidence")
        return normalized
    except (TypeError, ValueError) as exc:
        raise SpeechDatasetManifestError("speech_dataset_record_invalid", str(exc)) from exc


def _validate_curation_binding(
    records: Sequence[Mapping[str, object]],
    summary: Mapping[str, object] | None,
    *,
    curation_report_digest: str,
) -> None:
    curation_fields = {"curation_status", "reconciliation_outcome"}
    if summary is None:
        if any(curation_fields & set(record) for record in records):
            raise SpeechDatasetManifestError(
                "speech_dataset_curation_binding_missing",
                "reconciliation records require a bound curation summary",
            )
        return
    try:
        statuses = ("resolved", "unresolved", "rejected", "quarantined")
        count = int(summary["input_count"])
        counts = {status: int(summary[f"{status}_count"]) for status in statuses}
        digest_lists = {
            status: tuple(str(value) for value in summary[f"{status}_record_digests"]) for status in statuses
        }
        outcomes = tuple(dict(value) for value in summary["outcomes"])
        terminal = summary["terminal"]
        trainable = summary["trainable"]
    except (KeyError, TypeError, ValueError) as exc:
        raise SpeechDatasetManifestError(
            "speech_dataset_curation_binding_invalid",
            "curation summary is incomplete",
        ) from exc
    if (
        type(terminal) is not bool
        or type(trainable) is not bool
        or count != len(records)
        or sum(counts.values()) != count
        or any(counts[status] != len(digest_lists[status]) for status in statuses)
    ):
        raise SpeechDatasetManifestError(
            "speech_dataset_curation_counts_invalid",
            "curation counts do not partition the input records",
        )
    flattened = tuple(value for status in statuses for value in digest_lists[status])
    record_digests = tuple(str(record.get("record_digest") or "") for record in records)
    if len(flattened) != len(set(flattened)) or set(flattened) != set(record_digests):
        raise SpeechDatasetManifestError(
            "speech_dataset_curation_partition_invalid",
            "curation categories overlap or omit records",
        )
    expected_trainable = bool(
        terminal
        and counts["resolved"] > 0
        and counts["unresolved"] == 0
        and counts["rejected"] == 0
        and counts["quarantined"] == 0
    )
    if trainable is not expected_trainable:
        raise SpeechDatasetManifestError(
            "speech_dataset_curation_trainable_invalid",
            "curation trainability does not match its categories",
        )
    if hashlib.sha256(canonical_json(summary)).hexdigest() != curation_report_digest:
        raise SpeechDatasetManifestError(
            "speech_dataset_curation_digest_invalid",
            "curation summary digest does not match",
        )
    if hashlib.sha256(canonical_json(outcomes)).hexdigest() != summary.get("resolution_outcomes_sha256"):
        raise SpeechDatasetManifestError(
            "speech_dataset_curation_outcomes_digest_invalid",
            "curation outcome digest does not match",
        )
    records_by_digest = {str(record["record_digest"]): record for record in records}
    source_digests: set[str] = set()
    if len(outcomes) != count:
        raise SpeechDatasetManifestError(
            "speech_dataset_curation_outcomes_invalid",
            "every source record requires exactly one curation outcome",
        )
    for outcome in outcomes:
        output_digest = str(outcome.get("output_record_digest") or "")
        source_digest = str(outcome.get("source_record_digest") or "")
        status = str(outcome.get("status") or "")
        resolution_id = outcome.get("resolution_id")
        region_ids = outcome.get("unresolved_region_ids")
        record = records_by_digest.get(output_digest)
        if (
            record is None
            or source_digest in source_digests
            or status not in statuses
            or output_digest not in digest_lists[status]
            or record.get("curation_status") != status
            or outcome.get("reconciliation_digest") != summary.get("reconciliation_digest")
            or not isinstance(region_ids, list)
            or region_ids != sorted(set(region_ids))
        ):
            raise SpeechDatasetManifestError(
                "speech_dataset_curation_outcomes_invalid",
                "curation outcomes are duplicated or inconsistent",
            )
        source_digests.add(source_digest)
        embedded = dict(record.get("reconciliation_outcome") or {})
        projected = {key: value for key, value in outcome.items() if key != "output_record_digest"}
        if embedded != projected or hashlib.sha256(canonical_json(embedded)).hexdigest() != output_digest:
            raise SpeechDatasetManifestError(
                "speech_dataset_curation_record_digest_invalid",
                "record digest does not bind its curation outcome",
            )
        if status == "resolved":
            valid_disposition = _digest(str(resolution_id or "")) and not region_ids
        else:
            valid_disposition = resolution_id is None and (status != "unresolved" or bool(region_ids))
        if not valid_disposition:
            raise SpeechDatasetManifestError(
                "speech_dataset_curation_disposition_invalid",
                "curation disposition is not closed",
            )


def _forbid_sensitive_values(value: object, *, key: str = "") -> None:
    lowered = key.lower()
    if any(token in lowered for token in ("private_key", "secret", "plaintext", "local_path", "file_path")):
        raise SpeechDatasetManifestError(
            "speech_dataset_sensitive_metadata_forbidden", "manifest contains a forbidden field"
        )
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            _forbid_sensitive_values(child, key=str(child_key))
    elif isinstance(value, list):
        for child in value:
            _forbid_sensitive_values(child, key=key)
    elif isinstance(value, str):
        lowered_value = value.lower()
        if lowered_value.startswith(("file://", "/home/", "/tmp/", "/var/", "c:\\")):
            raise SpeechDatasetManifestError("speech_dataset_local_path_forbidden", "manifest contains a local path")


def _digest(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


__all__ = [
    "HubSpeechDatasetConsentAuthority",
    "MlInternSpeechDatasetBuildService",
    "SpeechDatasetConsentPort",
    "SpeechDatasetManifestError",
]
