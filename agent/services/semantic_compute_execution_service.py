"""Hub-only composition for candidate scheduling and lease lifecycle APIs."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Callable, Mapping

from agent.repositories.semantic_compute_candidate_repository import (
    SemanticComputeCandidateRepository,
    SemanticComputeCandidateRepositoryError,
    get_semantic_compute_candidate_repository,
)
from agent.repositories.semantic_compute_schedule_repository import (
    SemanticComputeScheduleRepository,
    SemanticComputeScheduleRepositoryError,
    get_semantic_compute_schedule_repository,
)
from agent.repositories.semantic_contract_repository import (
    SemanticContractRepository,
    SemanticContractRepositoryError,
    SemanticPrincipal,
    get_semantic_contract_repository,
)
from agent.repositories.semantic_lease_repository import (
    SemanticLeaseRepository,
    SemanticLeaseRepositoryError,
    get_semantic_lease_repository,
)
from agent.services.semantic_compute_consent import (
    CapabilityGrantComputeConsentAuthority,
    DenySemanticComputeConsentAuthority,
    SemanticComputeConsentAuthorityPort,
)
from agent.services.semantic_compute_policy import ComputeCandidate
from agent.services.semantic_compute_scheduler import (
    ScheduleRequest,
    SemanticComputeScheduler,
    SemanticComputeSchedulingError,
)
from agent.services.semantic_task_lease_authority import (
    HubSemanticTaskLeaseAuthority,
    SemanticTaskLeaseAuthorityError,
    SemanticTaskLeaseAuthorityPort,
)
from ananta_contracts.semantic_compute import (
    SemanticComputeContractError,
    canonical_json,
    validate_quality_contract,
)


class SemanticComputeExecutionError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class SemanticComputeExecutionService:
    """Revalidates Hub truth before delegating to the pure scheduler."""

    def __init__(
        self,
        *,
        contracts: SemanticContractRepository,
        candidates: SemanticComputeCandidateRepository,
        leases: SemanticLeaseRepository,
        receipts: SemanticComputeScheduleRepository,
        scheduler: SemanticComputeScheduler | None = None,
        clock: Callable[[], float] = time.time,
        feature_enabled: Callable[[], bool] | None = None,
        security_confirmed: Callable[[], bool] | None = None,
        consent_authority: SemanticComputeConsentAuthorityPort | None = None,
        lease_authority: SemanticTaskLeaseAuthorityPort | None = None,
    ) -> None:
        self._contracts = contracts
        self._candidates = candidates
        self._leases = leases
        self._receipts = receipts
        self._consent_authority = consent_authority or DenySemanticComputeConsentAuthority()
        self._scheduler = scheduler or SemanticComputeScheduler(
            leases,
            consent_authority=self._consent_authority,
        )
        self._lease_authority = lease_authority or HubSemanticTaskLeaseAuthority.from_environment()
        self._clock = clock
        self._feature_enabled = feature_enabled or self._default_feature_enabled
        self._security_confirmed = security_confirmed or self._default_security_confirmed

    def register_candidate_key(
        self,
        principal: SemanticPrincipal,
        *,
        session_id: str,
        epoch: int,
        key_id: str,
        public_key_b64: str,
        expires_at_ms: int,
    ) -> dict[str, Any]:
        self._require_membership(principal, session_id=session_id, epoch=epoch)
        try:
            item = self._candidates.register_key(
                principal,
                session_id=session_id,
                epoch=epoch,
                key_id=key_id,
                public_key_b64=public_key_b64,
                expires_at_ms=expires_at_ms,
            )
        except SemanticComputeCandidateRepositoryError as exc:
            raise self._candidate_error(exc) from exc
        return {
            "key_id": item.key_id,
            "session_id": item.session_id,
            "epoch": item.epoch,
            "status": item.status,
            "expires_at_ms": int(item.expires_at * 1000),
            "version": item.version,
            "authoritative_source": "hub",
        }

    def advertise_candidate(
        self,
        principal: SemanticPrincipal,
        *,
        advertisement: Mapping[str, Any],
    ) -> dict[str, Any]:
        session_id = str(advertisement.get("session_id") or "")
        epoch = advertisement.get("epoch")
        if isinstance(epoch, bool) or not isinstance(epoch, int):
            raise SemanticComputeExecutionError("epoch_invalid", status_code=400)
        self._require_membership(principal, session_id=session_id, epoch=epoch)
        try:
            item, replayed = self._candidates.put_advertisement(principal, raw=advertisement)
        except SemanticComputeCandidateRepositoryError as exc:
            raise self._candidate_error(exc) from exc
        return {
            "advertisement_id": item.advertisement_id,
            "session_id": item.session_id,
            "epoch": item.epoch,
            "sender_id": item.sender_subject,
            "status": item.status,
            "expires_at_ms": int(item.expires_at * 1000),
            "eligible_capacity": item.observed_capacity,
            "idempotent_replay": replayed,
            "authoritative": False,
            "scheduler_authority": False,
        }

    def schedule(
        self,
        principal: SemanticPrincipal,
        *,
        contract_id: str,
        session_id: str,
        epoch: int,
        expected_revision: int,
        task_type: str,
        audience: str,
        sequence_start: int,
        sequence_end: int,
        resource_budget: Mapping[str, int],
        deadline_epoch_ms: int,
        validator_count: int,
        hot_standby: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        contract, payload = self._active_contract(
            principal,
            contract_id=contract_id,
            session_id=session_id,
            epoch=epoch,
            expected_revision=expected_revision,
        )
        # Audience membership is Hub truth and deliberately returns the same
        # hidden 404 semantics as a missing session.
        self._require_membership(
            SemanticPrincipal(principal.tenant_id, audience),
            session_id=session_id,
            epoch=epoch,
        )
        self._validate_schedule_values(
            payload=payload,
            task_type=task_type,
            sequence_start=sequence_start,
            sequence_end=sequence_end,
            resource_budget=resource_budget,
            deadline_epoch_ms=deadline_epoch_ms,
            validator_count=validator_count,
            hot_standby=hot_standby,
        )
        request_values = {
            "contract_id": contract_id,
            "contract_digest": contract.digest,
            "session_id": session_id,
            "epoch": epoch,
            "expected_revision": expected_revision,
            "task_type": task_type,
            "audience": audience,
            "sequence_start": sequence_start,
            "sequence_end": sequence_end,
            "resource_budget": dict(resource_budget),
            "deadline_epoch_ms": deadline_epoch_ms,
            "validator_count": validator_count,
            "hot_standby": hot_standby,
        }
        request_digest = hashlib.sha256(canonical_json(request_values)).hexdigest()
        try:
            replay = self._receipts.replay(
                principal,
                contract_id=contract_id,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
            )
        except SemanticComputeScheduleRepositoryError as exc:
            raise SemanticComputeExecutionError(exc.reason_code) from exc
        if replay is not None:
            return self._hydrate_receipt(principal, replay, idempotent_replay=True)

        rows = self._candidates.current(
            principal,
            session_id=session_id,
            epoch=epoch,
            room_id=contract.room_id,
        )
        try:
            active_assignments = self._leases.active_assignment_counts(
                tenant_id=principal.tenant_id,
                session_id=session_id,
                epoch=epoch,
                executor_ids={row.sender_subject for row in rows},
            )
        except SemanticLeaseRepositoryError as exc:
            raise self._lease_error(exc) from exc
        candidates: list[ComputeCandidate] = []
        for row in rows:
            try:
                self._contracts.require_membership(
                    SemanticPrincipal(principal.tenant_id, row.sender_subject),
                    session_id=session_id,
                    epoch=epoch,
                    permission="semantic_compute",
                )
            except SemanticContractRepositoryError:
                continue
            advertisement = dict(row.normalized_payload or {})
            if int(advertisement.get("max_artifact_bytes") or 0) < int(resource_budget["artifact_bytes"]) or int(
                advertisement.get("max_delay_ms") or 0
            ) < deadline_epoch_ms - int(self._clock() * 1000):
                continue
            candidates.append(
                self._candidate(
                    row,
                    advertisement,
                    active_assignments=active_assignments.get(row.sender_subject, 0),
                )
            )
        if not candidates:
            raise SemanticComputeExecutionError("no_eligible_primary", status_code=422)

        try:
            planned = self._scheduler.plan(
                ScheduleRequest(
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    contract_id=contract.id,
                    contract_digest=contract.digest,
                    session_id=session_id,
                    room_id=contract.room_id,
                    epoch=epoch,
                    task_type=task_type,
                    audience=audience,
                    sequence_start=sequence_start,
                    sequence_end=sequence_end,
                    resource_budget=dict(resource_budget),
                    deadline_at=deadline_epoch_ms / 1000.0,
                    lease_ttl_seconds=min(
                        30.0,
                        max(1.0, deadline_epoch_ms / 1000.0 - self._clock()),
                    ),
                    validator_count=validator_count,
                    hot_standby=hot_standby,
                    minimum_capacity=1,
                ),
                candidates,
            )
        except SemanticComputeSchedulingError as exc:
            raise SemanticComputeExecutionError(
                getattr(exc, "reason_code", "schedule_failed"), status_code=422
            ) from exc
        result_base = {
            "contract_id": contract.id,
            "contract_digest": contract.digest,
            "contract_revision": contract.revision,
            "session_id": session_id,
            "epoch": epoch,
            "authoritative_source": "hub",
        }
        try:
            committed = self._leases.schedule_once(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                contract_id=contract_id,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                requests=tuple(item.request for item in planned),
                result_payload=result_base,
                result_factory=lambda leases: {
                    "task_leases": {
                        lease.id: self._lease_authority.issue(
                            lease,
                            room_id=contract.room_id,
                        )
                        for lease in leases
                    }
                },
                expires_at=min(contract.expires_at, deadline_epoch_ms / 1000.0),
            )
        except SemanticTaskLeaseAuthorityError as exc:
            raise SemanticComputeExecutionError(
                exc.reason_code,
                status_code=503,
            ) from exc
        except SemanticLeaseRepositoryError as exc:
            raise SemanticComputeExecutionError(exc.reason_code, status_code=409) from exc
        return self._hydrate_receipt(
            principal,
            committed.result_payload,
            idempotent_replay=committed.replayed,
        )

    def list_candidate_claims(
        self,
        principal: SemanticPrincipal,
        *,
        session_id: str,
        epoch: int,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_membership(principal, session_id=session_id, epoch=epoch)
        try:
            rows = self._candidates.current(
                principal,
                session_id=session_id,
                epoch=epoch,
                room_id=room_id,
            )
        except SemanticComputeCandidateRepositoryError as exc:
            raise self._candidate_error(exc) from exc
        return {
            "items": [
                {
                    "advertisement_id": row.advertisement_id,
                    "sender_id": row.sender_subject,
                    "resource_profile": dict((row.normalized_payload or {}).get("resource_profile") or {}),
                    "expires_at_ms": int(row.expires_at * 1000),
                    "authoritative": False,
                    "scheduler_authority": False,
                }
                for row in rows
            ],
            "authoritative": False,
        }

    def list_leases(
        self,
        principal: SemanticPrincipal,
        *,
        session_id: str,
        epoch: int,
        contract_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        self._require_membership(principal, session_id=session_id, epoch=epoch)
        if contract_id is not None:
            self._contract(
                principal,
                contract_id=contract_id,
                session_id=session_id,
                epoch=epoch,
            )
        try:
            rows = self._leases.list_for_principal(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                session_id=session_id,
                epoch=epoch,
                contract_id=contract_id,
                limit=limit,
            )
        except SemanticLeaseRepositoryError as exc:
            raise self._lease_error(exc) from exc
        try:
            items = [
                self._lease_view(
                    item,
                    task_lease=(
                        self._lease_authority.issue(
                            item,
                            room_id=self._lease_room_id(principal, item),
                        )
                        if item.status == "active"
                        else None
                    ),
                )
                for item in rows
            ]
        except SemanticTaskLeaseAuthorityError as exc:
            raise SemanticComputeExecutionError(
                exc.reason_code,
                status_code=503,
            ) from exc
        return {"items": items, "authoritative_source": "hub"}

    def revoke_lease(
        self,
        principal: SemanticPrincipal,
        *,
        lease_id: str,
        session_id: str,
        epoch: int,
        expected_version: int,
        fencing_token: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_membership(principal, session_id=session_id, epoch=epoch)
        try:
            current = self._leases.get_scoped(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                lease_id=lease_id,
            )
            if current.session_id != session_id or current.epoch != epoch:
                raise SemanticLeaseRepositoryError("lease_not_found")
            request_digest = hashlib.sha256(
                canonical_json(
                    {
                        "lease_id": lease_id,
                        "session_id": session_id,
                        "epoch": epoch,
                        "expected_version": expected_version,
                        "fencing_token": fencing_token,
                    }
                )
            ).hexdigest()
            item, replayed = self._leases.revoke_scoped_idempotent(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                lease_id=lease_id,
                fencing_token=fencing_token,
                expected_version=expected_version,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
            )
        except SemanticLeaseRepositoryError as exc:
            raise self._lease_error(exc) from exc
        return {**self._lease_view(item), "idempotent_replay": replayed}

    def reduce_lease(
        self,
        principal: SemanticPrincipal,
        *,
        lease_id: str,
        session_id: str,
        epoch: int,
        expected_version: int,
        fencing_token: int,
        resource_budget: Mapping[str, int],
        expires_at_ms: int | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_membership(principal, session_id=session_id, epoch=epoch)
        try:
            current = self._leases.get_scoped(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                lease_id=lease_id,
            )
            if current.session_id != session_id or current.epoch != epoch:
                raise SemanticLeaseRepositoryError("lease_not_found")
            request_digest = hashlib.sha256(
                canonical_json(
                    {
                        "lease_id": lease_id,
                        "session_id": session_id,
                        "epoch": epoch,
                        "expected_version": expected_version,
                        "fencing_token": fencing_token,
                        "resource_budget": dict(resource_budget),
                        "expires_at_ms": expires_at_ms,
                    }
                )
            ).hexdigest()
            item, replayed = self._leases.reduce_idempotent(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                lease_id=lease_id,
                fencing_token=fencing_token,
                expected_version=expected_version,
                resource_budget=resource_budget,
                expires_at=expires_at_ms / 1000.0 if expires_at_ms is not None else None,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
            )
        except SemanticLeaseRepositoryError as exc:
            raise self._lease_error(exc) from exc
        return {**self._lease_view(item), "idempotent_replay": replayed}

    def _active_contract(self, principal: SemanticPrincipal, **values: Any):
        expected_revision = int(values.pop("expected_revision"))
        item, payload = self._contract(principal, **values)
        if item.revision != expected_revision:
            raise SemanticComputeExecutionError("stale_revision", status_code=412)
        if item.status != "active":
            raise SemanticComputeExecutionError("contract_not_active")
        if not self._feature_enabled():
            raise SemanticComputeExecutionError("feature_disabled")
        if not self._security_confirmed():
            raise SemanticComputeExecutionError("security_unconfirmed")
        if item.expires_at <= self._clock():
            raise SemanticComputeExecutionError("contract_expired")
        if int(payload["consent_version"]) < 1:
            raise SemanticComputeExecutionError("consent_missing")
        return item, payload

    def _contract(
        self,
        principal: SemanticPrincipal,
        *,
        contract_id: str,
        session_id: str,
        epoch: int,
    ):
        self._require_membership(principal, session_id=session_id, epoch=epoch)
        try:
            item = self._contracts.get(principal, contract_id)
        except SemanticContractRepositoryError as exc:
            raise self._contract_error(exc) from exc
        if item.session_id != session_id or item.epoch != epoch:
            raise SemanticComputeExecutionError("contract_not_found", status_code=404)
        try:
            payload = validate_quality_contract(item.contract_payload)
        except SemanticComputeContractError as exc:
            raise SemanticComputeExecutionError(exc.reason_code) from exc
        if payload["contract_digest"] != item.digest:
            raise SemanticComputeExecutionError("contract_digest_mismatch")
        return item, payload

    def _require_membership(self, principal: SemanticPrincipal, *, session_id: str, epoch: int) -> None:
        try:
            self._contracts.require_membership(
                principal,
                session_id=session_id,
                epoch=epoch,
                permission="semantic_compute",
            )
        except SemanticContractRepositoryError as exc:
            raise self._contract_error(exc) from exc

    def _hydrate_receipt(
        self,
        principal: SemanticPrincipal,
        raw: Mapping[str, Any],
        *,
        idempotent_replay: bool,
    ) -> dict[str, Any]:
        values = dict(raw)
        task_leases = values.pop("task_leases", {})
        if not isinstance(task_leases, Mapping):
            raise SemanticComputeExecutionError("schedule_receipt_stale", status_code=409)
        leases = []
        for lease_id in values.pop("lease_ids", []):
            try:
                item = self._leases.get_scoped(
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    lease_id=str(lease_id),
                )
            except SemanticLeaseRepositoryError as exc:
                raise SemanticComputeExecutionError("schedule_receipt_stale", status_code=409) from exc
            signed = task_leases.get(item.id)
            try:
                if signed is None:
                    signed = self._lease_authority.issue(
                        item,
                        room_id=self._lease_room_id(principal, item),
                    )
                self._lease_authority.verify(
                    signed,
                    lease=item,
                    expected_executor_id=item.executor_id,
                    expected_audience=item.audience,
                )
            except SemanticTaskLeaseAuthorityError as exc:
                raise SemanticComputeExecutionError(
                    exc.reason_code,
                    status_code=409,
                ) from exc
            leases.append(self._lease_view(item, task_lease=signed))
        return {**values, "leases": leases, "idempotent_replay": idempotent_replay}

    @staticmethod
    def _candidate(
        row,
        payload: Mapping[str, Any],
        *,
        active_assignments: int,
    ) -> ComputeCandidate:
        self_capacity = {"unknown": 0, "low": 1, "medium": 2, "high": 3}.get(
            str((payload.get("resource_profile") or {}).get("cpu")), 0
        )
        return ComputeCandidate(
            candidate_id=row.sender_subject,
            offered_roles=frozenset(payload.get("roles") or ()),
            task_types=frozenset(payload.get("task_types") or ()),
            self_capacity=self_capacity,
            measured_capacity=row.observed_capacity,
            user_limit=row.user_limit,
            reserve_capacity=row.reserve_capacity,
            recent_error_rate=row.recent_error_rate,
            reputation=row.reputation,
            active_assignments=max(int(row.active_assignments), active_assignments),
            failure_domain=row.failure_domain,
            available=row.status == "active",
        )

    def _validate_schedule_values(
        self,
        *,
        payload: Mapping[str, Any],
        task_type: str,
        sequence_start: int,
        sequence_end: int,
        resource_budget: Mapping[str, int],
        deadline_epoch_ms: int,
        validator_count: int,
        hot_standby: bool,
    ) -> None:
        if task_type not in set(payload.get("task_types") or ()):
            raise SemanticComputeExecutionError("task_type_not_granted", status_code=422)
        if (
            isinstance(sequence_start, bool)
            or isinstance(sequence_end, bool)
            or not isinstance(sequence_start, int)
            or not isinstance(sequence_end, int)
            or sequence_start < 0
            or sequence_end < sequence_start
            or sequence_end - sequence_start > 10_000
        ):
            raise SemanticComputeExecutionError("sequence_range_invalid", status_code=400)
        if isinstance(validator_count, bool) or validator_count not in {0, 1, 2}:
            raise SemanticComputeExecutionError("validator_count_invalid", status_code=400)
        if not isinstance(hot_standby, bool):
            raise SemanticComputeExecutionError("hot_standby_invalid", status_code=400)
        required = {"cpu_ms", "memory_bytes", "artifact_bytes"}
        if set(resource_budget) != required or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in resource_budget.values()
        ):
            raise SemanticComputeExecutionError("resource_budget_invalid", status_code=400)
        if int(resource_budget["artifact_bytes"]) > int(payload["max_artifact_bytes"]):
            raise SemanticComputeExecutionError("artifact_budget_exceeds_contract", status_code=422)
        now_ms = int(self._clock() * 1000)
        maximum_deadline = min(now_ms + int(payload["deadline_ms"]), int(payload["expires_at_ms"]))
        if deadline_epoch_ms <= now_ms or deadline_epoch_ms > maximum_deadline:
            raise SemanticComputeExecutionError("deadline_invalid", status_code=400)

    @staticmethod
    def _lease_view(
        item,
        *,
        task_lease: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "lease_id": item.id,
            "contract_id": item.contract_id,
            "contract_digest": item.contract_digest,
            "session_id": item.session_id,
            "epoch": item.epoch,
            "task_type": item.task_type,
            "audience": item.audience,
            "role": item.role,
            "executor_id": item.executor_id,
            "sequence_start": item.sequence_start,
            "sequence_end": item.sequence_end,
            "fencing_token": item.fencing_token,
            "resource_budget": dict(item.resource_budget or {}),
            "status": item.status,
            "issued_at_ms": int(item.issued_at * 1000),
            "expires_at_ms": int(item.expires_at * 1000),
            "deadline_at_ms": int(item.deadline_at * 1000),
            "version": item.version,
            "authoritative_source": "hub",
            **({"task_lease": dict(task_lease)} if task_lease is not None else {}),
        }

    def _lease_room_id(self, principal: SemanticPrincipal, item) -> str | None:
        try:
            contract = self._contracts.get(principal, item.contract_id)
        except SemanticContractRepositoryError as exc:
            raise SemanticComputeExecutionError(
                "schedule_receipt_stale",
                status_code=409,
            ) from exc
        return contract.room_id

    @staticmethod
    def _contract_error(exc: SemanticContractRepositoryError) -> SemanticComputeExecutionError:
        code = 404 if exc.reason_code in {"contract_not_found", "session_not_found"} else 409
        return SemanticComputeExecutionError(exc.reason_code, status_code=code)

    @staticmethod
    def _candidate_error(
        exc: SemanticComputeCandidateRepositoryError,
    ) -> SemanticComputeExecutionError:
        code = 409 if "conflict" in exc.reason_code or "substitution" in exc.reason_code else 400
        return SemanticComputeExecutionError(exc.reason_code, status_code=code)

    @staticmethod
    def _lease_error(exc: SemanticLeaseRepositoryError) -> SemanticComputeExecutionError:
        code = 404 if exc.reason_code == "lease_not_found" else 409
        return SemanticComputeExecutionError(exc.reason_code, status_code=code)

    @staticmethod
    def _default_feature_enabled() -> bool:
        import os

        from agent.services.semantic_media_feature_flags import (
            resolve_semantic_media_feature_flags,
        )

        flags = resolve_semantic_media_feature_flags(os.environ)
        return bool(flags.get("semantic_visual_capture") or flags.get("semantic_speech_runtime"))

    @staticmethod
    def _default_security_confirmed() -> bool:
        import os

        return str(os.environ.get("ANANTA_SEMANTIC_COMPUTE_SECURITY_CONFIRMED", "")).lower() in {
            "1",
            "true",
            "yes",
            "on",
        }


_service: SemanticComputeExecutionService | None = None


def get_semantic_compute_execution_service() -> SemanticComputeExecutionService:
    global _service
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            configured = current_app.extensions.get("semantic_compute_execution_service")
            if isinstance(configured, SemanticComputeExecutionService):
                return configured
            audit = current_app.extensions.get("semantic_media_audit_recorder")
            leases = get_semantic_lease_repository(audit=audit)
            permission_service = current_app.extensions.get("semantic_media_permission_service")
            consent_authority = (
                CapabilityGrantComputeConsentAuthority(permission_service)
                if permission_service is not None
                else DenySemanticComputeConsentAuthority()
            )
            configured = SemanticComputeExecutionService(
                contracts=get_semantic_contract_repository(),
                candidates=get_semantic_compute_candidate_repository(),
                leases=leases,
                receipts=get_semantic_compute_schedule_repository(),
                scheduler=SemanticComputeScheduler(
                    leases,
                    audit=audit,
                    consent_authority=consent_authority,
                ),
                consent_authority=consent_authority,
                lease_authority=HubSemanticTaskLeaseAuthority.from_environment(),
                feature_enabled=lambda: bool(
                    (current_app.extensions.get("semantic_media_feature_flags") or {}).get("semantic_visual_capture")
                    or (current_app.extensions.get("semantic_media_feature_flags") or {}).get("semantic_speech_runtime")
                ),
                security_confirmed=lambda: current_app.config.get("SEMANTIC_COMPUTE_SECURITY_CONFIRMED") is True,
            )
            current_app.extensions["semantic_compute_execution_service"] = configured
            return configured
    except RuntimeError:
        pass
    if _service is None:
        leases = get_semantic_lease_repository()
        _service = SemanticComputeExecutionService(
            contracts=get_semantic_contract_repository(),
            candidates=get_semantic_compute_candidate_repository(),
            leases=leases,
            receipts=get_semantic_compute_schedule_repository(),
            scheduler=SemanticComputeScheduler(
                leases,
                consent_authority=DenySemanticComputeConsentAuthority(),
            ),
            consent_authority=DenySemanticComputeConsentAuthority(),
            lease_authority=HubSemanticTaskLeaseAuthority.from_environment(),
        )
    return _service


__all__ = [
    "SemanticComputeExecutionError",
    "SemanticComputeExecutionService",
    "get_semantic_compute_execution_service",
]
