"""Optional trusted server compute, delegated only as a Hub child task."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

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
from agent.services.repository_registry import get_repository_registry
from agent.services.semantic_compute_consent import (
    TrustedServerComputeConsentAuthority,
)
from agent.services.semantic_compute_policy import ComputeCandidate
from agent.services.semantic_compute_scheduler import (
    ScheduleRequest,
    SemanticComputeScheduler,
    SemanticComputeSchedulingError,
)
from agent.services.semantic_compute_task_service import (
    SemanticComputeTaskError,
    SemanticComputeTaskService,
    get_semantic_compute_task_service,
)
from ananta_contracts.semantic_compute import (
    SemanticComputeContractError,
    canonical_json,
    validate_quality_contract,
)


class SemanticServerComputeError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class SemanticServerWorkerDirectoryPort(Protocol):
    def candidates(self, task_type: str) -> Sequence[ComputeCandidate]: ...


class SemanticInputAuthorizationPort(Protocol):
    def authorize(
        self,
        principal: SemanticPrincipal,
        *,
        parent_task_id: str,
        input_refs: Sequence[str],
    ) -> bool: ...


class RegisteredSemanticServerWorkerDirectory:
    """Uses only Hub-validated live Worker registrations, never client claims."""

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock

    def candidates(self, task_type: str) -> Sequence[ComputeCandidate]:
        result: list[ComputeCandidate] = []
        now = self._clock()
        required_capability = f"semantic_compute.{str(task_type).strip().lower()}"
        for worker in get_repository_registry().agent_repo.get_all() or ():
            advertised_capabilities = {
                str(value).strip().lower()
                for value in (getattr(worker, "capabilities", None) or [])
                if str(value).strip()
            }
            authorized_capabilities = {
                str(value).strip().lower()
                for value in (getattr(worker, "authorized_capabilities", None) or [])
                if str(value).strip()
            }
            if (
                str(getattr(worker, "role", "")).lower() != "worker"
                or str(getattr(worker, "status", "")).lower() != "online"
                or not bool(getattr(worker, "registration_validated", False))
                or list(getattr(worker, "validation_errors", None) or [])
                or float(getattr(worker, "last_seen", 0) or 0) <= now - 90
                or required_capability not in advertised_capabilities
                or (authorized_capabilities and required_capability not in authorized_capabilities)
            ):
                continue
            worker_url = str(getattr(worker, "url", "") or "").strip()
            parsed = urlsplit(worker_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            result.append(
                ComputeCandidate(
                    candidate_id=worker_url,
                    offered_roles=frozenset({"executor"}),
                    task_types=frozenset({task_type}),
                    self_capacity=1,
                    measured_capacity=1,
                    user_limit=1,
                    reserve_capacity=0,
                    recent_error_rate=0.0,
                    reputation=50,
                    active_assignments=0,
                    failure_domain=parsed.hostname or worker_url,
                    consent=True,
                )
            )
        return tuple(sorted(result, key=lambda item: item.candidate_id))


class RepositorySemanticInputAuthorization:
    """Allow only artifacts owned by the contract principal or parent task."""

    def authorize(
        self,
        principal: SemanticPrincipal,
        *,
        parent_task_id: str,
        input_refs: Sequence[str],
    ) -> bool:
        repositories = get_repository_registry()
        parent = repositories.task_repo.get_by_id(parent_task_id)
        if parent is None or str(getattr(parent, "status", "")) not in {"assigned", "in_progress"}:
            return False
        for reference in input_refs:
            artifact_id = reference.removeprefix("artifact:")
            artifact = repositories.artifact_repo.get_by_id(artifact_id)
            if artifact is None:
                return False
            metadata = dict(getattr(artifact, "artifact_metadata", None) or {})
            if (
                str(getattr(artifact, "created_by", "") or "") != principal.subject
                and str(metadata.get("task_id") or "") != parent_task_id
            ):
                return False
        return True


@dataclass(frozen=True, slots=True)
class SemanticServerDelegation:
    task: Mapping[str, Any]
    lease_id: str
    executor_id: str
    fencing_token: int
    idempotent_replay: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": dict(self.task),
            "lease_id": self.lease_id,
            "executor_id": self.executor_id,
            "fencing_token": self.fencing_token,
            "idempotent_replay": self.idempotent_replay,
            "authoritative_source": "hub",
        }


class SemanticServerComputeService:
    def __init__(
        self,
        *,
        contracts: SemanticContractRepository,
        leases: SemanticLeaseRepository,
        receipts: SemanticComputeScheduleRepository,
        tasks: SemanticComputeTaskService,
        workers: SemanticServerWorkerDirectoryPort,
        inputs: SemanticInputAuthorizationPort,
        scheduler: SemanticComputeScheduler | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._contracts = contracts
        self._leases = leases
        self._receipts = receipts
        self._tasks = tasks
        self._workers = workers
        self._inputs = inputs
        self._scheduler = scheduler or SemanticComputeScheduler(
            leases,
            consent_authority=TrustedServerComputeConsentAuthority(contracts),
        )
        self._clock = clock

    def delegate(
        self,
        principal: SemanticPrincipal,
        *,
        parent_task_id: str,
        contract_id: str,
        session_id: str,
        epoch: int,
        expected_revision: int,
        task_type: str,
        audience: str,
        input_refs: Sequence[str],
        sequence_start: int,
        sequence_end: int,
        deadline_epoch_ms: int,
        resource_budget: Mapping[str, int],
        idempotency_key: str,
    ) -> SemanticServerDelegation:
        try:
            self._contracts.require_membership(
                principal, session_id=session_id, epoch=epoch, permission="semantic_compute"
            )
            self._contracts.require_membership(
                SemanticPrincipal(principal.tenant_id, audience),
                session_id=session_id,
                epoch=epoch,
                permission="semantic_compute",
            )
            contract = self._contracts.get(principal, contract_id)
        except SemanticContractRepositoryError as exc:
            raise SemanticServerComputeError(exc.reason_code, status_code=404) from exc
        try:
            payload = validate_quality_contract(contract.contract_payload)
        except SemanticComputeContractError as exc:
            raise SemanticServerComputeError(exc.reason_code) from exc
        if payload["contract_digest"] != contract.digest:
            raise SemanticServerComputeError("contract_digest_mismatch")
        if contract.session_id != session_id or contract.epoch != epoch or contract.revision != expected_revision:
            raise SemanticServerComputeError("stale_contract", status_code=412)
        if contract.status != "active" or contract.expires_at <= self._clock():
            raise SemanticServerComputeError("contract_not_active")
        if payload["security_mode"] != "trusted_compute":
            raise SemanticServerComputeError("strict_e2ee_server_compute_forbidden")
        if payload["trusted_compute_grant"] is not True:
            raise SemanticServerComputeError("trusted_compute_grant_missing")
        if task_type not in set(payload["task_types"]):
            raise SemanticServerComputeError("task_type_not_granted", status_code=422)
        if not 1 <= len(input_refs) <= 16 or any(
            not isinstance(value, str) or not value.startswith("artifact:") or len(value) > 256 for value in input_refs
        ):
            raise SemanticServerComputeError("input_refs_invalid", status_code=400)
        if not self._inputs.authorize(principal, parent_task_id=parent_task_id, input_refs=input_refs):
            raise SemanticServerComputeError("input_ref_not_found", status_code=404)
        now_ms = int(self._clock() * 1000)
        if (
            deadline_epoch_ms <= now_ms
            or deadline_epoch_ms > now_ms + int(payload["deadline_ms"])
            or deadline_epoch_ms > int(payload["expires_at_ms"])
        ):
            raise SemanticServerComputeError("deadline_invalid", status_code=400)
        if int(resource_budget.get("artifact_bytes") or 0) > int(payload["max_artifact_bytes"]):
            raise SemanticServerComputeError("artifact_budget_exceeds_contract", status_code=422)
        request_values = {
            "kind": "trusted_server_compute",
            "parent_task_id": parent_task_id,
            "contract_id": contract_id,
            "contract_digest": contract.digest,
            "session_id": session_id,
            "epoch": epoch,
            "expected_revision": expected_revision,
            "task_type": task_type,
            "audience": audience,
            "input_refs": list(input_refs),
            "sequence_start": sequence_start,
            "sequence_end": sequence_end,
            "deadline_epoch_ms": deadline_epoch_ms,
            "resource_budget": dict(resource_budget),
        }
        request_digest = hashlib.sha256(canonical_json(request_values)).hexdigest()
        receipt_key = f"server:{idempotency_key}"
        artifact_publish_ref = (
            "artifact-publish:semantic-output-"
            + hashlib.sha256(
                canonical_json(
                    {
                        "parent_task_id": parent_task_id,
                        "contract_id": contract_id,
                        "inputs": list(input_refs),
                    }
                )
            ).hexdigest()[:32]
        )
        try:
            replay = self._receipts.replay(
                principal,
                contract_id=contract_id,
                idempotency_key=receipt_key,
                request_digest=request_digest,
            )
        except SemanticComputeScheduleRepositoryError as exc:
            raise SemanticServerComputeError(exc.reason_code) from exc
        if replay is not None:
            return self._ensure_server_task(
                principal,
                replay,
                parent_task_id=parent_task_id,
                contract_id=contract_id,
                task_type=task_type,
                audience=audience,
                input_refs=input_refs,
                deadline_epoch_ms=deadline_epoch_ms,
                resource_budget=resource_budget,
                artifact_publish_ref=artifact_publish_ref,
                replayed=True,
            )
        candidates = self._workers.candidates(task_type)
        if not candidates:
            raise SemanticServerComputeError("trusted_worker_unavailable", status_code=503)
        try:
            planned = self._scheduler.plan(
                ScheduleRequest(
                    tenant_id=principal.tenant_id,
                    owner_subject=principal.subject,
                    contract_id=contract_id,
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
                    lease_ttl_seconds=min(30.0, deadline_epoch_ms / 1000.0 - self._clock()),
                ),
                candidates,
            )
            committed = self._leases.schedule_once(
                tenant_id=principal.tenant_id,
                owner_subject=principal.subject,
                contract_id=contract_id,
                idempotency_key=receipt_key,
                request_digest=request_digest,
                requests=tuple(item.request for item in planned),
                result_payload={
                    "contract_id": contract_id,
                    "contract_revision": contract.revision,
                    "session_id": session_id,
                    "epoch": epoch,
                },
                result_factory=lambda leases: self._server_receipt_payload(
                    leases[0],
                    contract=contract,
                    parent_task_id=parent_task_id,
                    task_type=task_type,
                    audience=audience,
                    input_refs=input_refs,
                    deadline_epoch_ms=deadline_epoch_ms,
                    resource_budget=resource_budget,
                    artifact_publish_ref=artifact_publish_ref,
                ),
                expires_at=min(contract.expires_at, deadline_epoch_ms / 1000.0),
            )
        except SemanticComputeTaskError as exc:
            raise SemanticServerComputeError(exc.reason_code, status_code=503) from exc
        except (SemanticComputeSchedulingError, SemanticLeaseRepositoryError) as exc:
            raise SemanticServerComputeError(
                getattr(exc, "reason_code", "server_compute_delegation_failed"),
                status_code=422,
            ) from exc
        return self._ensure_server_task(
            principal,
            committed.result_payload,
            parent_task_id=parent_task_id,
            contract_id=contract_id,
            task_type=task_type,
            audience=audience,
            input_refs=input_refs,
            deadline_epoch_ms=deadline_epoch_ms,
            resource_budget=resource_budget,
            artifact_publish_ref=artifact_publish_ref,
            replayed=committed.replayed,
        )

    def _server_receipt_payload(
        self,
        lease,
        *,
        contract,
        parent_task_id: str,
        task_type: str,
        audience: str,
        input_refs: Sequence[str],
        deadline_epoch_ms: int,
        resource_budget: Mapping[str, int],
        artifact_publish_ref: str,
    ) -> Mapping[str, object]:
        task = self._tasks.materialize_task(
            contract=contract,
            lease=lease,
            parent_task_id=parent_task_id,
            task_type=task_type,
            audience=audience,
            input_refs=input_refs,
            deadline_epoch_ms=deadline_epoch_ms,
            resource_budget=resource_budget,
            artifact_publish_ref=artifact_publish_ref,
        )
        return {
            "task": task.to_dict(),
            "lease_id": lease.id,
            "executor_id": lease.executor_id,
            "fencing_token": lease.fencing_token,
        }

    def _ensure_server_task(
        self,
        principal: SemanticPrincipal,
        result: Mapping[str, Any],
        *,
        parent_task_id: str,
        contract_id: str,
        task_type: str,
        audience: str,
        input_refs: Sequence[str],
        deadline_epoch_ms: int,
        resource_budget: Mapping[str, int],
        artifact_publish_ref: str,
        replayed: bool,
    ) -> SemanticServerDelegation:
        try:
            task = self._tasks.create_server_task(
                principal,
                parent_task_id=parent_task_id,
                contract_id=contract_id,
                lease_id=str(result["lease_id"]),
                task_type=task_type,
                audience=audience,
                input_refs=input_refs,
                deadline_epoch_ms=deadline_epoch_ms,
                resource_budget=resource_budget,
                artifact_publish_ref=artifact_publish_ref,
            )
        except SemanticComputeTaskError as exc:
            raise SemanticServerComputeError(exc.reason_code, status_code=503) from exc
        if task.to_dict() != dict(result["task"]):
            raise SemanticServerComputeError("schedule_receipt_task_mismatch", status_code=409)
        return SemanticServerDelegation(
            task=task.to_dict(),
            lease_id=str(result["lease_id"]),
            executor_id=str(result["executor_id"]),
            fencing_token=int(result["fencing_token"]),
            idempotent_replay=replayed,
        )


_service: SemanticServerComputeService | None = None


def get_semantic_server_compute_service() -> SemanticServerComputeService:
    global _service
    if _service is None:
        leases = get_semantic_lease_repository()
        _service = SemanticServerComputeService(
            contracts=get_semantic_contract_repository(),
            leases=leases,
            receipts=get_semantic_compute_schedule_repository(),
            tasks=get_semantic_compute_task_service(),
            workers=RegisteredSemanticServerWorkerDirectory(),
            inputs=RepositorySemanticInputAuthorization(),
            scheduler=SemanticComputeScheduler(leases),
        )
    return _service


__all__ = [
    "RegisteredSemanticServerWorkerDirectory",
    "RepositorySemanticInputAuthorization",
    "SemanticInputAuthorizationPort",
    "SemanticServerComputeError",
    "SemanticServerComputeService",
    "SemanticServerDelegation",
    "SemanticServerWorkerDirectoryPort",
    "get_semantic_server_compute_service",
]
