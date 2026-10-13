"""Hub-only bounded retrieval/admission of Worker results; no command redispatch."""

import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable

from agent.services.native_result_task_projection import native_task_command
from agent.services.workflow_worker_service_auth import STRICT_WORKER_REGISTRATION_PROVENANCE
from ananta_contracts.native_task_execution import sign_native_execution_request


@dataclass(frozen=True)
class NativeResultRecoveryPorts:
    repository: object
    journal: object
    assignments: object
    workers: object
    signing_keys: Callable
    transport: object
    accept_result: Callable
    pi_assignment: Callable | None = None
    complete_pi: Callable | None = None


def recover_native_dispatch_results(*, ports, tenant_id: str, limit: int = 1, task_id=None, expected_scope=None):
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("native_result_recovery_limit_invalid")
    deadline = time.monotonic() + 20
    results = []
    for task_id in ports.journal.pending_task_ids(tenant_id=tenant_id, limit=limit, task_id=task_id):
        phase = "scope"
        try:
            if time.monotonic() >= deadline:
                raise ValueError("native_result_recovery_deadline")
            task = ports.repository.get_by_id(task_id)
            if task is None or task.tenant_id != tenant_id:
                raise ValueError("native_result_recovery_scope_denied")
            if expected_scope is not None and (task.goal_id, task.team_id) != expected_scope:
                raise ValueError("native_result_recovery_scope_changed")
            pi = task.task_kind == "pi_coding_agent"
            if pi and (ports.pi_assignment is None or ports.complete_pi is None):
                raise ValueError("pi_recovery_evidence_composition_required")
            try:
                if pi:
                    ports.complete_pi(task_id=task.id)
                ports.journal.reconcile(task=task)
            except ValueError:
                pass
            else:
                results.append({"task_id": task_id, "state": "accepted"})
                continue
            if (task.status != "waiting_for_review"
                    or task.status_reason_code != "native_autopilot_dispatch_blocked"):
                raise ValueError("native_result_recovery_transport_review_required")
            phase = "assignment"
            command = native_task_command(task)
            assignment = ports.assignments.get(tenant_id=task.tenant_id, run_id=command.run_id,
                                                step_id=command.node.node_id)
            if assignment is None:
                raise ValueError("native_result_recovery_assignment_required")
            ports.journal.require_recovery_assignment(task=task, assignment=assignment)
            worker = ports.workers.get_by_url(assignment.worker_url)
            if (worker is None or worker.name != assignment.worker_id or worker.role != "worker"
                    or worker.registration_validated is not True
                    or worker.registration_provenance != STRICT_WORKER_REGISTRATION_PROVENANCE
                    or "workflow.adapter.native" not in (worker.authorized_capabilities or [])):
                raise ValueError("native_result_recovery_worker_denied")
            payload = sign_native_execution_request(
                task.model_dump(mode="json"), key_ring=ports.signing_keys(),
                worker_id=assignment.worker_id, worker_url=assignment.worker_url,
                expires_at=min(time.time() + 30, command.authorization.expires_at),
                evidence_assignment=ports.pi_assignment(task_id=task.id) if pi else None,
            )
            phase = "fetch"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("native_result_recovery_deadline")
            response = ports.transport.forward_task(worker.url, "/api/internal/native-tasks/result", payload,
                                                      token=worker.token, timeout=min(5, remaining))
            phase = "admission"
            ports.accept_result(tid=task.id, response=response, task=task.model_dump(),
                                request_data=SimpleNamespace(command=None))
            if pi:
                ports.complete_pi(task_id=task.id)
            ports.journal.reconcile(task=ports.repository.get_by_id(task.id))
        except Exception as exc:
            results.append({"task_id": task_id, "state": "unresolved", "phase": phase,
                            "reason_code": "native_result_recovery_unresolved", "error_type": type(exc).__name__})
        else:
            results.append({"task_id": task_id, "state": "accepted"})
    return {"schema": "ananta.native-result-recovery.v1", "tenant_id": tenant_id, "results": results,
            "accepted": sum(item["state"] == "accepted" for item in results),
            "unresolved": sum(item["state"] == "unresolved" for item in results)}


def production_native_result_recovery_ports():
    from flask import current_app

    from agent.services._task_scoped_forwarding import persist_forwarded_execution
    from agent.services.native_dispatch_journal import production_native_dispatch_journal
    from agent.services.native_result_read_transport import NativeResultReadTransport
    from agent.services.repository_registry import get_repository_registry
    from agent.services.workflow_hub_task_gateway_runtime import get_workflow_authorization_key_ring
    from agent.services.workflow_worker_assignment_runtime import get_workflow_worker_assignment_store

    if str(current_app.config.get("ROLE") or "").strip().lower() != "hub":
        raise ValueError("native_result_recovery_hub_required")
    registry = get_repository_registry()
    from agent.services.pi_dispatch_runtime import get_pi_dispatch_service

    pi = get_pi_dispatch_service(current_app)
    return NativeResultRecoveryPorts(
        repository=registry.task_repo, journal=production_native_dispatch_journal(),
        assignments=get_workflow_worker_assignment_store(), workers=registry.agent_repo,
        signing_keys=get_workflow_authorization_key_ring, transport=NativeResultReadTransport(),
        accept_result=persist_forwarded_execution,
        pi_assignment=pi.reserved_assignment if pi is not None else None,
        complete_pi=pi.complete_reserved if pi is not None else None,
    )
