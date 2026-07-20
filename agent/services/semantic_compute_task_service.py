"""Hub task gateway for optional trusted semantic compute."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Callable, Mapping, Protocol, Sequence

from agent.repositories.semantic_contract_repository import (
    SemanticContractRepository,
    SemanticContractRepositoryError,
    SemanticPrincipal,
)
from agent.repositories.semantic_lease_repository import SemanticLeaseRepository
from agent.services.semantic_compute_consent import (
    ComputeConsentContext,
    SemanticComputeConsentAuthorityPort,
    TrustedServerComputeConsentAuthority,
)
from agent.services.semantic_task_lease_authority import (
    HubSemanticTaskLeaseAuthority,
    SemanticTaskLeaseAuthorityError,
    SemanticTaskLeaseAuthorityPort,
)
from ananta_contracts.semantic_compute import (
    SemanticComputeContractError,
    SemanticComputeWorkerResult,
    SemanticComputeWorkerTask,
    canonical_json,
)


class SemanticComputeTaskError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SemanticTaskQueuePort(Protocol):
    def ingest_task(self, **kwargs) -> None: ...


class SemanticComputeTaskService:
    """The Hub alone binds contract, worker lease, child task and publication."""

    def __init__(
        self,
        *,
        contracts: SemanticContractRepository,
        leases: SemanticLeaseRepository,
        queue: SemanticTaskQueuePort,
        clock_ms: Callable[[], int] | None = None,
        task_is_active: Callable[[str], bool] | None = None,
        task_binding_lookup: Callable[[str], Mapping[str, Any] | None] | None = None,
        consent_authority: SemanticComputeConsentAuthorityPort | None = None,
        lease_authority: SemanticTaskLeaseAuthorityPort | None = None,
    ) -> None:
        self._contracts = contracts
        self._leases = leases
        self._queue = queue
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._task_is_active = task_is_active or self._default_task_is_active
        self._task_binding_lookup = task_binding_lookup or self._default_task_binding
        self._consent_authority = consent_authority or TrustedServerComputeConsentAuthority(contracts)
        self._lease_authority = lease_authority or HubSemanticTaskLeaseAuthority.from_environment(
            clock_ms=self._clock_ms
        )

    def create_server_task(
        self,
        principal: SemanticPrincipal,
        *,
        parent_task_id: str,
        contract_id: str,
        lease_id: str,
        task_type: str,
        audience: str,
        input_refs: Sequence[str],
        deadline_epoch_ms: int,
        resource_budget: Mapping[str, int],
        artifact_publish_ref: str,
    ) -> SemanticComputeWorkerTask:
        contract = self._contracts.get(principal, contract_id)
        payload = dict(contract.contract_payload or {})
        # Fail closed before task ID generation or queue persistence.
        if contract.status != "active":
            raise SemanticComputeTaskError("contract_not_active")
        if contract.security_mode == "strict_e2ee":
            raise SemanticComputeTaskError("strict_e2ee_server_compute_forbidden")
        if payload.get("trusted_compute_grant") is not True:
            raise SemanticComputeTaskError("trusted_compute_grant_missing")
        if task_type not in set(payload.get("task_types") or []):
            raise SemanticComputeTaskError("task_type_not_granted")
        if not self._task_is_active(parent_task_id):
            raise SemanticComputeTaskError("parent_task_not_active")
        now_ms = self._clock_ms()
        if deadline_epoch_ms <= now_ms or deadline_epoch_ms - now_ms > 20_000:
            raise SemanticComputeTaskError("deadline_invalid")
        lease = self._leases.get(lease_id)
        lease = self._leases.authorize_result(
            lease_id=lease_id,
            contract_digest=contract.digest,
            fencing_token=lease.fencing_token,
            session_id=contract.session_id,
            epoch=contract.epoch,
            task_type=task_type,
            audience=audience,
        )
        if lease.contract_id != contract.id:
            raise SemanticComputeTaskError("lease_binding_mismatch")
        if lease.role != "primary":
            raise SemanticComputeTaskError("primary_lease_required")
        if set(resource_budget) != {"cpu_ms", "memory_bytes", "artifact_bytes"}:
            raise SemanticComputeTaskError("resource_budget_invalid")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in resource_budget.values()
        ):
            raise SemanticComputeTaskError("resource_budget_invalid")
        lease_budget = dict(lease.resource_budget or {})
        if any(int(resource_budget[key]) > int(lease_budget.get(key, 0)) for key in resource_budget):
            raise SemanticComputeTaskError("resource_budget_exceeds_lease")
        if int(resource_budget["artifact_bytes"]) > int(payload.get("max_artifact_bytes", 0)):
            raise SemanticComputeTaskError("artifact_budget_exceeds_contract")
        if deadline_epoch_ms > int(lease.deadline_at * 1000):
            raise SemanticComputeTaskError("deadline_exceeds_lease")
        task = self.materialize_task(
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
        self._queue.ingest_task(
            task_id=task.task_id,
            status="assigned",
            title="Hub-delegated semantic compute",
            description="Execute exactly one bounded semantic-compute contract.",
            priority="medium",
            created_by="hub",
            source="semantic_compute",
            tags=["semantic_compute", task.task_type],
            event_type="semantic_compute_delegated",
            event_details={
                "contract_id": task.contract_id,
                "lease_id": task.lease_id,
                "fencing_token": task.fencing_token,
            },
            extra_fields={
                "task_kind": "semantic_compute",
                "parent_task_id": parent_task_id,
                "assigned_agent_url": lease.executor_id,
                "required_capabilities": [
                    "semantic_compute",
                    f"semantic_compute.{task.task_type}",
                ],
                "worker_execution_context": {"semantic_compute": task.to_dict()},
            },
        )
        return task

    def materialize_task(
        self,
        *,
        contract: object,
        lease: object,
        parent_task_id: str,
        task_type: str,
        audience: str,
        input_refs: Sequence[str],
        deadline_epoch_ms: int,
        resource_budget: Mapping[str, int],
        artifact_publish_ref: str,
    ) -> SemanticComputeWorkerTask:
        """Build the deterministic signed envelope used by receipt and queue."""

        seed = {
            "parent_task_id": parent_task_id,
            "lease_id": str(getattr(lease, "id")),
            "fencing_token": int(getattr(lease, "fencing_token")),
            "input_refs": list(input_refs),
            "deadline_epoch_ms": deadline_epoch_ms,
        }
        try:
            task_lease = self._lease_authority.issue(
                lease,
                room_id=getattr(contract, "room_id", None),
            )
        except SemanticTaskLeaseAuthorityError as exc:
            raise SemanticComputeTaskError(exc.reason_code) from exc
        task_id = f"semantic-compute-{hashlib.sha256(canonical_json(seed)).hexdigest()[:32]}"
        return SemanticComputeWorkerTask(
            task_id=task_id,
            parent_task_id=parent_task_id,
            contract_id=str(getattr(contract, "id")),
            contract_digest=str(getattr(contract, "digest")),
            lease_id=str(getattr(lease, "id")),
            fencing_token=int(getattr(lease, "fencing_token")),
            session_id=str(getattr(contract, "session_id")),
            epoch=int(getattr(contract, "epoch")),
            task_type=task_type,
            audience=audience,
            input_refs=tuple(input_refs),
            deadline_epoch_ms=deadline_epoch_ms,
            resource_budget=dict(resource_budget),
            artifact_publish_ref=artifact_publish_ref,
            task_lease=task_lease,
        )

    def authorize_execution(
        self,
        raw: object,
        *,
        expected_executor_id: str,
    ) -> SemanticComputeWorkerTask:
        """Revalidate one worker envelope without accepting any result data."""

        task = self._parse_task(raw)
        if not self._task_is_active(task.task_id):
            raise SemanticComputeTaskError("task_cancelled_or_terminal")
        binding = self._task_binding_lookup(task.task_id)
        if binding is None:
            raise SemanticComputeTaskError("task_binding_missing")
        delegated = self._parse_task(binding)
        if task.to_dict() != delegated.to_dict():
            raise SemanticComputeTaskError("task_binding_mismatch")
        lease = self._leases.authorize_result(
            lease_id=task.lease_id,
            contract_digest=task.contract_digest,
            fencing_token=task.fencing_token,
            session_id=task.session_id,
            epoch=task.epoch,
            task_type=task.task_type,
            audience=task.audience,
        )
        if lease.contract_id != task.contract_id or lease.role != "primary":
            raise SemanticComputeTaskError("lease_binding_mismatch")
        if lease.executor_id != expected_executor_id:
            raise SemanticComputeTaskError("worker_binding_mismatch")
        if task.task_lease is None:
            raise SemanticComputeTaskError("task_lease_required")
        try:
            verified_lease = self._lease_authority.verify(
                task.task_lease,
                lease=lease,
                expected_executor_id=expected_executor_id,
                expected_audience=task.audience,
            )
        except SemanticTaskLeaseAuthorityError as exc:
            raise SemanticComputeTaskError(exc.reason_code) from exc
        if self._clock_ms() >= task.deadline_epoch_ms:
            raise SemanticComputeTaskError("deadline_expired")
        try:
            contract = self._contracts.get(SemanticPrincipal(lease.tenant_id, lease.owner_subject), task.contract_id)
        except SemanticContractRepositoryError as exc:
            raise SemanticComputeTaskError("contract_not_active") from exc
        payload = dict(contract.contract_payload or {})
        if contract.status != "active" or contract.digest != task.contract_digest:
            raise SemanticComputeTaskError("contract_not_active")
        if contract.expires_at * 1000 <= self._clock_ms():
            raise SemanticComputeTaskError("contract_expired")
        if (
            contract.security_mode != "trusted_compute"
            or payload.get("trusted_compute_grant") is not True
            or task.task_type not in set(payload.get("task_types") or [])
        ):
            raise SemanticComputeTaskError("trusted_compute_grant_missing")
        expected_room = contract.room_id
        if verified_lease.get("room_id") != expected_room:
            raise SemanticComputeTaskError("task_lease_room_mismatch")
        if not self._consent_authority.authorized(
            ComputeConsentContext(
                tenant_id=lease.tenant_id,
                owner_subject=lease.owner_subject,
                contract_id=task.contract_id,
                contract_digest=task.contract_digest,
                session_id=task.session_id,
                room_id=expected_room,
                epoch=task.epoch,
                candidate_id=lease.executor_id,
                task_type=task.task_type,
                role=lease.role,
            )
        ):
            raise SemanticComputeTaskError("compute_consent_revoked")
        return delegated

    def authorize_result(self, raw: object, *, expected_executor_id: str | None = None) -> SemanticComputeWorkerResult:
        result = SemanticComputeWorkerResult.from_dict(raw)
        if not self._task_is_active(result.task_id):
            raise SemanticComputeTaskError("task_cancelled_or_terminal")
        binding = self._task_binding_lookup(result.task_id)
        if binding is None:
            raise SemanticComputeTaskError("task_binding_missing")
        delegated = self._parse_task(binding)
        if any(
            (
                result.contract_id != delegated.contract_id,
                result.contract_digest != delegated.contract_digest,
                result.lease_id != delegated.lease_id,
                result.fencing_token != delegated.fencing_token,
                result.session_id != delegated.session_id,
                result.epoch != delegated.epoch,
                result.task_type != delegated.task_type,
                result.audience != delegated.audience,
            )
        ):
            raise SemanticComputeTaskError("result_task_binding_mismatch")
        now_ms = self._clock_ms()
        lease = self._leases.authorize_result(
            lease_id=result.lease_id,
            contract_digest=result.contract_digest,
            fencing_token=result.fencing_token,
            session_id=result.session_id,
            epoch=result.epoch,
            task_type=result.task_type,
            audience=result.audience,
        )
        if result.contract_id != lease.contract_id:
            raise SemanticComputeTaskError("result_contract_binding_mismatch")
        if expected_executor_id is not None and lease.executor_id != expected_executor_id:
            raise SemanticComputeTaskError("worker_binding_mismatch")
        # Result admission repeats the full current contract/task/lease gate;
        # a once-valid envelope cannot outlive consent or a contract revision.
        self.authorize_execution(
            delegated.to_dict(),
            expected_executor_id=expected_executor_id or lease.executor_id,
        )
        if result.completed_at_ms > int(lease.deadline_at * 1000) or now_ms > int(lease.deadline_at * 1000):
            raise SemanticComputeTaskError("late_result")
        return result

    @staticmethod
    def _default_task_is_active(task_id: str) -> bool:
        from agent.repository import task_repo

        task = task_repo.get_by_id(task_id)
        return task is not None and str(task.status) in {"assigned", "in_progress"}

    @staticmethod
    def _default_task_binding(task_id: str) -> Mapping[str, Any] | None:
        from agent.repository import task_repo

        task = task_repo.get_by_id(task_id)
        context = getattr(task, "worker_execution_context", None) if task is not None else None
        if not isinstance(context, Mapping):
            return None
        binding = context.get("semantic_compute")
        return binding if isinstance(binding, Mapping) else None

    @staticmethod
    def _parse_task(raw: object) -> SemanticComputeWorkerTask:
        try:
            return SemanticComputeWorkerTask.from_dict(raw)
        except SemanticComputeContractError as exc:
            raise SemanticComputeTaskError(exc.reason_code) from exc


def get_semantic_compute_task_service() -> SemanticComputeTaskService:
    from agent.repositories.semantic_contract_repository import get_semantic_contract_repository
    from agent.repositories.semantic_lease_repository import get_semantic_lease_repository
    from agent.services.task_queue_service import get_task_queue_service

    return SemanticComputeTaskService(
        contracts=get_semantic_contract_repository(),
        leases=get_semantic_lease_repository(),
        queue=get_task_queue_service(),
    )


__all__ = [
    "SemanticComputeTaskError",
    "SemanticComputeTaskService",
    "SemanticTaskQueuePort",
    "get_semantic_compute_task_service",
]
