from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import SpeechReconciliationMutationDB
from agent.repositories.speech_reconciliation import (
    SpeechReconciliationJobRecord,
    SpeechReconciliationMutationResult,
    SpeechReconciliationRepository,
    SpeechReconciliationRepositoryError,
)
from agent.services.semantic_media_audit_service import (
    InMemorySemanticMediaAuditRepository,
    SemanticMediaAuditRecorder,
    SemanticMediaAuditService,
)
from agent.services.speech_reconciliation_budget_service import (
    AdmittedSourceDuration,
    SpeechReconciliationBudgetService,
)
from agent.services.speech_reconciliation_job_service import (
    AdmittedSpeechReconciliationManifest,
    SpeechReconciliationAdmission,
    SpeechReconciliationJobService,
    SpeechReconciliationJobServiceError,
    default_speech_reconciliation_policy_digest,
)
from agent.services.speech_reconciliation_read_model_service import SpeechReconciliationReadModelService
from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_evidence_governance import SpeechEvidenceConsent
from ananta_contracts.speech_reconciliation import RESOURCE_FIELDS, SpeechResourceVector

NOW = 10_000_000
DIGEST = "a" * 64


def _consent(owner: str = "owner", **changes) -> SpeechEvidenceConsent:
    values = {
        "schema": "ananta.speech-evidence-consent.v1",
        "consent_id": "consent-reconciliation",
        "tenant_id": "tenant",
        "owner_subject": owner,
        "speaker_id": owner,
        "recipient_id": owner,
        "direction": "local",
        "pair_id": "pair-local",
        "session_id": "session-local",
        "session_epoch": 1,
        "purpose": "speech_reconciliation",
        "data_classes": ["audio", "transcript"],
        "retention_seconds": 86_400,
        "trainer_locations": [],
        "grants": {"raw_audio_share": True, "dataset_import": True},
        "consent_version": 1,
        "revocation_epoch": 0,
        "issued_at_ms": NOW - 1_000,
        "expires_at_ms": NOW + 10_000_000,
        "state": "active",
        "required_signers": [],
        "signatures": {},
    }
    values.update(changes)
    return SpeechEvidenceConsent.from_mapping(values, now_ms=NOW)


def _record(spec, *, state="queued", version=1) -> SpeechReconciliationJobRecord:
    return SpeechReconciliationJobRecord(
        id=spec.job_id,
        tenant_id=spec.tenant_id,
        owner_subject=spec.owner_subject,
        pair_scope_digest=spec.pair_scope_digest,
        request_digest=spec.request_digest,
        state=state,
        stage="admission",
        reason_code="speech_reconciliation_admitted",
        consent_id=spec.consent_id,
        consent_version=spec.consent_version,
        revocation_epoch=spec.revocation_epoch,
        input_manifest_digest=spec.input_manifest_digest,
        input_lineage_digest=spec.input_lineage_digest,
        input_artifact_ref=spec.input_artifact_ref,
        policy_digest=spec.policy_digest,
        research_policy_ref=spec.research_policy_ref,
        source_duration_ms=spec.source_duration_ms,
        max_compute_factor=spec.max_compute_factor,
        budget_plan=dict(spec.budget_plan),
        ledger_sequence=0,
        key_epoch=spec.key_epoch,
        deadline_at_ms=spec.deadline_at_ms,
        active_attempt_id=None,
        fencing_epoch=0,
        checkpoint_count=0,
        resolved_count=0,
        unresolved_count=0,
        rejected_count=0,
        quarantined_count=0,
        version=version,
        created_at_ms=NOW,
        updated_at_ms=NOW,
        finished_at_ms=None,
    )


class _Repository:
    def __init__(self) -> None:
        self.rows: dict[str, SpeechReconciliationJobRecord] = {}
        self.requests: dict[str, str] = {}
        self.mutations: dict[
            tuple[str, str, str],
            tuple[str, SpeechReconciliationJobRecord, str | None, int | None],
        ] = {}

    def create_job(self, spec, **_kwargs):
        existing_id = self.requests.get(spec.idempotency_key_digest)
        if existing_id:
            return self.rows[existing_id], False
        row = _record(spec)
        self.rows[row.id] = row
        self.requests[spec.idempotency_key_digest] = row.id
        return row, True

    def get_job(self, *, tenant_id, owner_subject, job_id):
        row = self.rows.get(job_id)
        return row if row and (row.tenant_id, row.owner_subject) == (tenant_id, owner_subject) else None

    def list_jobs(self, *, tenant_id, owner_subject, offset, limit):
        rows = [row for row in self.rows.values() if (row.tenant_id, row.owner_subject) == (tenant_id, owner_subject)]
        return rows[offset : offset + limit]

    def transition(
        self,
        *,
        job_id,
        target_state,
        reason_code,
        expected_version,
        idempotency_key_digest,
        request_digest,
        **_kwargs,
    ):
        operation = {"paused": "pause", "queued": "resume", "cancel_requested": "cancel"}[target_state]
        binding = (job_id, operation, idempotency_key_digest)
        previous = self.mutations.get(binding)
        if previous is not None:
            if previous[0] != request_digest:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_idempotency_conflict")
            return SpeechReconciliationMutationResult(previous[1], False, previous[2], previous[3])
        row = self.rows[job_id]
        if row.version != expected_version:
            raise SpeechReconciliationRepositoryError("speech_reconciliation_version_stale")
        attempt_id = row.active_attempt_id
        fencing_epoch = row.fencing_epoch if attempt_id is not None else None
        row = replace(
            row,
            state=target_state,
            reason_code=reason_code,
            active_attempt_id=(
                None if target_state in {"paused", "cancel_requested"} else row.active_attempt_id
            ),
            version=row.version + 1,
        )
        self.rows[job_id] = row
        self.mutations[binding] = (request_digest, row, attempt_id, fencing_epoch)
        return SpeechReconciliationMutationResult(row, True, attempt_id, fencing_epoch)

    def reduce_factor(
        self,
        *,
        job_id,
        max_compute_factor,
        expected_version,
        reason_code,
        idempotency_key_digest,
        request_digest,
        **_kwargs,
    ):
        binding = (job_id, "reduce", idempotency_key_digest)
        previous = self.mutations.get(binding)
        if previous is not None:
            if previous[0] != request_digest:
                raise SpeechReconciliationRepositoryError("speech_reconciliation_idempotency_conflict")
            return SpeechReconciliationMutationResult(previous[1], False, previous[2], previous[3])
        row = self.rows[job_id]
        row = replace(
            row,
            max_compute_factor=max_compute_factor,
            reason_code=reason_code,
            version=expected_version + 1,
        )
        self.rows[job_id] = row
        self.mutations[binding] = (request_digest, row, None, None)
        return SpeechReconciliationMutationResult(row, True)


class _Consents:
    def __init__(self, consent) -> None:
        self.consent = consent

    def get(self, principal, consent_id):
        assert consent_id == self.consent.consent_id
        return self.consent


class _Manifests:
    def __init__(self, admitted=True) -> None:
        self.admitted = admitted

    def resolve(self, principal, *, manifest_digest, consent):
        if not self.admitted:
            return None
        return AdmittedSpeechReconciliationManifest(
            manifest_digest,
            "b" * 64,
            f"artifact://speech-evidence/manifests/{manifest_digest}",
            (
                AdmittedSourceDuration("c" * 64, 60_000),
                AdmittedSourceDuration("c" * 64, 60_000),
            ),
        )


class _Tasks:
    def __init__(self) -> None:
        self.parents = []
        self.cancelled = []

    def materialize_parent(self, job, **scope):
        self.parents.append((job, scope))

    def cancel(self, task_id, *, reason_code):
        self.cancelled.append((task_id, reason_code))


def _request() -> dict:
    limits = {field: 1_000_000 for field in RESOURCE_FIELDS}
    limits["gpu_time_ms"] = 0
    limits["energy_millijoules"] = 0
    return {
        "consent_id": "consent-reconciliation",
        "consent_version": 1,
        "revocation_epoch": 0,
        "input_manifest_digest": DIGEST,
        "policy_digest": default_speech_reconciliation_policy_digest(),
        "research_policy_ref": None,
        "max_compute_factor": 10,
        "key_epoch": 1,
        "deadline_at_ms": NOW + 1_000_000,
        "resource_limits": limits,
    }


def _service(*, consent=None, admitted=True, admission_enabled=True, audit=None):
    repository = _Repository()
    tasks = _Tasks()
    service = SpeechReconciliationJobService(
        repository=repository,
        consents=_Consents(consent or _consent()),
        manifests=_Manifests(admitted),
        budgets=SpeechReconciliationBudgetService(),
        tasks=tasks,
        clock_ms=lambda: NOW,
        admission_enabled=lambda: admission_enabled,
        audit=audit,
    )
    return service, repository, tasks


def test_create_deduplicates_source_duration_checks_budget_and_projects_only_hub_parent() -> None:
    audit_repository = InMemorySemanticMediaAuditRepository()
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(audit_repository, clock_ms=lambda: NOW),
        secret=b"speech-offline-job-audit-key" * 2,
    )
    service, repository, tasks = _service(audit=audit)
    first = service.create(VoicePrincipal("tenant", "owner"), _request(), idempotency_key="request-key-1")
    replay = service.create(VoicePrincipal("tenant", "owner"), _request(), idempotency_key="request-key-1")
    assert first.created is True and replay.created is False
    assert first.job.source_duration_ms == 60_000
    assert first.budget.compute_equivalent_ms == 600_000
    assert len(repository.rows) == 1 and len(tasks.parents) == 2
    assert tasks.parents[0][0].attempt_id == "speech-reconciliation-unclaimed"
    paused = service.pause(
        VoicePrincipal("tenant", "owner"),
        first.job.id,
        expected_version=first.job.version,
        idempotency_key="pause-request-key-1",
    )
    rows, _ = audit_repository.page(
        tenant_digest=audit.digest("tenant", "tenant"),
        scope_digest=audit.digest("scope", f"speech-job:{first.job.id}"),
        after_event_id=None,
        limit=10,
        now_ms=NOW,
    )
    assert paused.state == "paused"
    assert [(row.event_type, row.transition) for row in rows] == [
        ("semantic_job", "created"),
        ("semantic_job", "paused"),
    ]


def test_stale_or_narrow_consent_manifest_policy_and_cross_tenant_reads_fail_closed() -> None:
    narrow = _consent(grants={"dataset_import": True})
    service, _, _ = _service(consent=narrow)
    with pytest.raises(SpeechReconciliationJobServiceError, match="consent_stale_or_narrow"):
        service.create(VoicePrincipal("tenant", "owner"), _request(), idempotency_key="request-key-2")

    service, _, _ = _service(admitted=False)
    with pytest.raises(SpeechReconciliationJobServiceError, match="manifest_not_admitted"):
        service.create(VoicePrincipal("tenant", "owner"), _request(), idempotency_key="request-key-3")

    service, _, _ = _service()
    invalid = _request()
    invalid["policy_digest"] = "f" * 64
    with pytest.raises(SpeechReconciliationJobServiceError, match="policy_not_admitted"):
        service.create(VoicePrincipal("tenant", "owner"), invalid, idempotency_key="request-key-4")
    admitted = service.create(VoicePrincipal("tenant", "owner"), _request(), idempotency_key="request-key-5")
    with pytest.raises(SpeechReconciliationJobServiceError, match="job_not_found"):
        service.get(VoicePrincipal("other", "owner"), admitted.job.id)


def test_create_and_resume_fail_closed_when_the_hub_feature_is_disabled() -> None:
    service, repository, _ = _service(admission_enabled=False)
    principal = VoicePrincipal("tenant", "owner")
    with pytest.raises(SpeechReconciliationJobServiceError, match="feature_disabled") as create_error:
        service.create(principal, _request(), idempotency_key="request-key-disabled")
    assert create_error.value.status_code == 403 and not repository.rows

    enabled, repository, tasks = _service()
    admission = enabled.create(principal, _request(), idempotency_key="request-key-enabled")
    paused = enabled.pause(
        principal,
        admission.job.id,
        expected_version=admission.job.version,
        idempotency_key="pause-before-disable",
    )
    disabled = SpeechReconciliationJobService(
        repository=repository,
        consents=_Consents(_consent()),
        manifests=_Manifests(),
        budgets=SpeechReconciliationBudgetService(),
        tasks=tasks,
        clock_ms=lambda: NOW,
        admission_enabled=lambda: False,
    )
    with pytest.raises(SpeechReconciliationJobServiceError, match="feature_disabled"):
        disabled.resume(
            principal,
            paused.id,
            expected_version=paused.version,
            idempotency_key="resume-while-disabled",
        )


def test_read_model_is_bounded_content_free_and_includes_vector_ledger() -> None:
    service, _, _ = _service()
    admission = service.create(VoicePrincipal("tenant", "owner"), _request(), idempotency_key="request-key-6")
    vector = SpeechResourceVector(**{field: 1 for field in RESOURCE_FIELDS})
    ledger = SimpleNamespace(
        allocated=vector,
        reserved=SpeechResourceVector(),
        consumed=SpeechResourceVector(),
        remaining=vector,
    )
    model = SpeechReconciliationReadModelService(lambda *_: ledger).project(admission.job)
    assert set(model["budget"]) == {"allocated", "reserved", "consumed", "remaining"}
    rendered = repr(model).casefold()
    assert "tenant" not in rendered and "owner" not in rendered and "artifact://" not in rendered


def test_all_mutations_replay_exactly_once_and_reject_divergent_payloads() -> None:
    audit_repository = InMemorySemanticMediaAuditRepository()
    audit = SemanticMediaAuditRecorder(
        SemanticMediaAuditService(audit_repository, clock_ms=lambda: NOW),
        secret=b"speech-offline-mutation-audit-key" * 2,
    )
    service, repository, tasks = _service(audit=audit)
    principal = VoicePrincipal("tenant", "owner")
    admission = service.create(principal, _request(), idempotency_key="mutation-create-key")
    repository.rows[admission.job.id] = replace(
        admission.job,
        state="running",
        stage="slow_asr",
        active_attempt_id="speech-reconciliation-attempt-live",
        fencing_epoch=4,
    )

    paused = service.pause(
        principal,
        admission.job.id,
        expected_version=admission.job.version,
        idempotency_key="mutation-pause-key",
    )
    pause_replay = service.pause(
        principal,
        admission.job.id,
        expected_version=admission.job.version,
        idempotency_key="mutation-pause-key",
    )
    assert pause_replay == paused
    assert len(tasks.cancelled) == 1

    resumed = service.resume(
        principal,
        admission.job.id,
        expected_version=paused.version,
        idempotency_key="mutation-resume-key",
    )
    resume_replay = service.resume(
        principal,
        admission.job.id,
        expected_version=paused.version,
        idempotency_key="mutation-resume-key",
    )
    assert resume_replay == resumed

    reduced = service.reduce(
        principal,
        admission.job.id,
        expected_version=resumed.version,
        max_compute_factor=5,
        idempotency_key="mutation-reduce-key",
    )
    reduce_replay = service.reduce(
        principal,
        admission.job.id,
        expected_version=resumed.version,
        max_compute_factor=5,
        idempotency_key="mutation-reduce-key",
    )
    assert reduce_replay == reduced

    cancelled = service.cancel(
        principal,
        admission.job.id,
        expected_version=reduced.version,
        idempotency_key="mutation-cancel-key",
    )
    cancel_replay = service.cancel(
        principal,
        admission.job.id,
        expected_version=reduced.version,
        idempotency_key="mutation-cancel-key",
    )
    assert cancel_replay == cancelled
    assert len(repository.mutations) == 4

    with pytest.raises(
        SpeechReconciliationJobServiceError,
        match="speech_reconciliation_idempotency_conflict",
    ) as conflict:
        service.pause(
            principal,
            admission.job.id,
            expected_version=cancelled.version,
            idempotency_key="mutation-pause-key",
        )
    assert conflict.value.status_code == 409
    with pytest.raises(
        SpeechReconciliationJobServiceError,
        match="speech_reconciliation_idempotency_conflict",
    ):
        service.reduce(
            principal,
            admission.job.id,
            expected_version=resumed.version,
            max_compute_factor=4,
            idempotency_key="mutation-reduce-key",
        )

    audit_rows, _ = audit_repository.page(
        tenant_digest=audit.digest("tenant", "tenant"),
        scope_digest=audit.digest("scope", f"speech-job:{admission.job.id}"),
        after_event_id=None,
        limit=20,
        now_ms=NOW,
    )
    assert [(row.event_type, row.transition) for row in audit_rows] == [
        ("semantic_job", "created"),
        ("semantic_job", "paused"),
        ("semantic_job", "queued"),
        ("semantic_budget", "reduced"),
        ("semantic_job", "cancel_requested"),
    ]


class _RouteService:
    def __init__(self, job) -> None:
        self.job = job
        vector = SpeechResourceVector(**{field: 1 for field in RESOURCE_FIELDS})
        self.budget = SimpleNamespace(compute_factor=10, compute_equivalent_ms=10, total=vector)

    def create(self, _principal, _body, *, idempotency_key):
        assert idempotency_key == "route-request-key"
        return SpeechReconciliationAdmission(self.job, True, self.budget)

    def list(self, _principal, *, offset, limit):
        assert offset == 0 and limit == 50
        return (self.job,)

    def get(self, _principal, _job_id):
        return self.job

    def pause(self, _principal, _job_id, *, expected_version, idempotency_key):
        assert expected_version == self.job.version
        assert idempotency_key == "route-mutation-key-accepted"
        return replace(self.job, state="paused", version=self.job.version + 1)

    resume = pause
    cancel = pause

    def reduce(
        self,
        _principal,
        _job_id,
        *,
        expected_version,
        max_compute_factor,
        idempotency_key,
    ):
        assert idempotency_key
        return replace(self.job, version=expected_version + 1, max_compute_factor=max_compute_factor)


def test_api_requires_auth_idempotency_and_matching_preconditions(client, app, auth_header) -> None:
    service, _, _ = _service()
    admission = service.create(VoicePrincipal("tenant", "owner"), _request(), idempotency_key="route-seed-key")
    app.extensions["speech_reconciliation_job_service"] = _RouteService(admission.job)
    app.extensions["speech_reconciliation_read_model"] = SpeechReconciliationReadModelService(lambda *_: None)

    path = "/v1/voice/speech-reconciliation"
    assert client.get(path).status_code == 401
    assert client.post(path, headers=auth_header, json=_request()).status_code == 400
    created = client.post(
        path,
        headers={**auth_header, "Idempotency-Key": "route-request-key"},
        json=_request(),
    )
    assert created.status_code == 201 and created.get_json()["data"]["job"]["budget_plan"]
    assert client.get(path, headers=auth_header).status_code == 200
    mutation = f"{path}/{admission.job.id}/pause"
    assert client.post(mutation, headers=auth_header, json={"expected_version": 1}).status_code == 428
    assert (
        client.post(
            mutation,
            headers={**auth_header, "If-Match": '"1"'},
            json={"expected_version": 1},
        ).status_code
        == 400
    )
    assert (
        client.post(
            mutation,
            headers={
                **auth_header,
                "Idempotency-Key": "route-mutation-key-stale",
                "If-Match": '"2"',
            },
            json={"expected_version": 1},
        ).status_code
        == 412
    )
    accepted = client.post(
        mutation,
        headers={
            **auth_header,
            "Idempotency-Key": "route-mutation-key-accepted",
            "If-Match": '"1"',
        },
        json={"expected_version": 1},
    )
    assert accepted.status_code == 200 and accepted.get_json()["data"]["job"]["state"] == "paused"


def test_real_api_persists_exact_mutation_replay_and_returns_409_for_divergence(
    client,
    app,
    auth_header,
) -> None:
    repository = SpeechReconciliationRepository()
    tasks = _Tasks()
    service = SpeechReconciliationJobService(
        repository=repository,
        consents=_Consents(
            _consent(
                owner="admin",
                tenant_id="admin",
                speaker_id="admin",
                recipient_id="admin",
            )
        ),
        manifests=_Manifests(),
        budgets=SpeechReconciliationBudgetService(),
        tasks=tasks,
        clock_ms=lambda: NOW,
    )
    principal = VoicePrincipal("admin", "admin")
    admission = service.create(
        principal,
        _request(),
        idempotency_key="real-api-create-key-v1",
    )
    app.extensions["speech_reconciliation_job_service"] = service
    app.extensions["speech_reconciliation_read_model"] = SpeechReconciliationReadModelService(lambda *_: None)
    mutation_url = f"/v1/voice/speech-reconciliation/{admission.job.id}/pause"
    headers = {
        **auth_header,
        "Idempotency-Key": "real-api-pause-key-v1",
        "If-Match": f'"{admission.job.version}"',
    }
    body = {"expected_version": admission.job.version}

    first = client.post(mutation_url, headers=headers, json=body)
    replay = client.post(mutation_url, headers=headers, json=body)
    assert first.status_code == 200 and replay.status_code == 200
    assert replay.get_json() == first.get_json()

    divergent = client.post(
        mutation_url,
        headers={
            **auth_header,
            "Idempotency-Key": "real-api-pause-key-v1",
            "If-Match": f'"{admission.job.version + 1}"',
        },
        json={"expected_version": admission.job.version + 1},
    )
    assert divergent.status_code == 409
    assert divergent.get_json()["error"]["code"] == "speech_reconciliation_idempotency_conflict"

    with Session(engine) as session:
        receipts = session.exec(
            select(SpeechReconciliationMutationDB).where(
                SpeechReconciliationMutationDB.job_id == admission.job.id,
                SpeechReconciliationMutationDB.operation == "pause",
            )
        ).all()
    assert len(receipts) == 1
    assert "real-api-pause-key-v1" not in repr(receipts[0])
