"""Direct Hub dispatch of an already authorized Native command, without LLM proposals."""

import logging
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable, Protocol

from agent.common.native_task_result_binding import is_native_task
from agent.routes.route_dependency_seam import RouteDependencySeam
from agent.routes.tasks.autopilot_task_dispatcher_helpers import TaskDispatchResult
from ananta_contracts.native_task_execution import sign_native_execution_request


@dataclass(frozen=True)
class NativeDispatchPorts:
    command: Callable
    assignment: Callable
    signing_keys: Callable
    transport: Callable
    accept_result: Callable
    journal: Callable
    prepare_pi: Callable | None = None
    complete_pi: Callable | None = None
    retirement: 'NativeRetirementDispatchPort | None' = None
    prepare_task: Callable | None = None


class NativeRetirementDispatchPort(Protocol):
    def prepare(self, *, task_id, assignment): ...
    def confirm(self, *, task_id): ...


def production_native_dispatch_ports():
    from agent.common.gateways.worker_gateway import get_worker_gateway
    from agent.services._task_scoped_forwarding import persist_forwarded_execution
    from agent.services.native_dispatch_journal import production_native_dispatch_journal
    from agent.services.native_result_task_projection import native_task_command
    from agent.services.workflow_hub_task_gateway_runtime import get_workflow_authorization_key_ring
    from agent.services.workflow_worker_assignment_runtime import bind_dispatched_workflow_task

    return NativeDispatchPorts(native_task_command, bind_dispatched_workflow_task,
                               get_workflow_authorization_key_ring, get_worker_gateway, persist_forwarded_execution,
                               production_native_dispatch_journal)


NATIVE_DISPATCH_PORTS = RouteDependencySeam("ananta.autopilot_native_dispatch", production_native_dispatch_ports)


def dispatch_native_task(ctx, *, policy):
    if not is_native_task(ctx.task):
        return None
    result = TaskDispatchResult(task_id=ctx.task.id, dispatched=True)
    phase = "command"
    journal = None
    claim = None
    pi = False
    evidence_completed = False
    journal_completed = False
    retirement = None
    try:
        ports = NATIVE_DISPATCH_PORTS.resolve_for(ctx.app_ctx)
        repository = ctx.dependencies.repository_registry(ctx.app_ctx).task_repo
        task = repository.get_by_id(ctx.task.id)
        command = ports.command(task)
        pi = command.node.task_kind == "pi_coding_agent"
        if ctx.target_worker.role != "worker" or task.assigned_agent_url != ctx.target_worker.url:
            raise ValueError("native_autopilot_worker_binding_invalid")
        phase = "assignment"
        assignment = ports.assignment(task=task, worker=ctx.target_worker, config=ctx.app_ctx.config)
        if assignment is None:
            raise ValueError("native_autopilot_assignment_required")
        if command.node.task_kind == 'native_result_maintenance':
            from agent.services.native_result_maintenance_owner import require_registered_result_store_owner
            from ananta_contracts.native_result_maintenance import maintenance_input

            _, owner = maintenance_input(command.input_data.get('workflow_input'))
            if owner is not None:
                phase = 'maintenance_owner'
                registry = ctx.dependencies.repository_registry(ctx.app_ctx)
                registered = registry.agent_repo.get_by_url(assignment.worker_url)
                require_registered_result_store_owner(registered, owner, now=time.time())
            if command.input_data.get('workflow_input', {}).get('retire_expired') is True:
                phase = 'retirement_issuance'
                retirement_port = ports.retirement
                if retirement_port is None:
                    from agent.services.native_result_retirement_runtime import get_native_result_retirement_service

                    service = get_native_result_retirement_service(ctx.app_ctx)
                    retirement_port = service
                retirement_port.prepare(task_id=task.id, assignment=assignment)
                retirement = retirement_port.confirm
                task = repository.get_by_id(task.id)
        if ports.prepare_task is not None:
            from ananta_contracts.native_context_bundle import native_context_digest

            phase = 'task_preparation'
            before = (task.id, task.tenant_id, task.task_kind, task.assigned_agent_url)
            project = task.project_id
            ports.prepare_task(task_id=task.id, assignment=assignment)
            task = repository.get_by_id(task.id)
            if ((task.id, task.tenant_id, task.task_kind, task.assigned_agent_url) != before
                    or project is not None and task.project_id != project
                    or native_context_digest(ports.command(task).to_dict()) != native_context_digest(
                        command.to_dict())):
                raise ValueError('native_autopilot_preparation_binding_changed')
        # The Worker receives only this closed projection. Signature/lease validation
        # remains mandatory in its consumer and the Hub accepts the exact result.
        phase = "projection"
        journal = ports.journal()
        if pi:
            prepare_pi, complete_pi = ports.prepare_pi, ports.complete_pi
            if prepare_pi is None and complete_pi is None:
                from agent.services.pi_dispatch_runtime import get_pi_dispatch_service

                service = get_pi_dispatch_service(ctx.app_ctx)
                if service is not None:
                    prepare_pi, complete_pi = service.prepare, service.complete
            if prepare_pi is None or complete_pi is None:
                raise ValueError("pi_dispatch_evidence_composition_required")
            prepared = prepare_pi(task_id=task.id, assignment=assignment)
            payload, claim = prepared.payload, prepared.claim
        else:
            payload = sign_native_execution_request(
                task.model_dump(mode="json"), key_ring=ports.signing_keys(),
                worker_id=assignment.worker_id, worker_url=assignment.worker_url,
                expires_at=min(time.time() + 30, command.authorization.expires_at),
            )
            ctx.update_local_task_status(task.id, "in_progress")
            phase = "dispatch_journal"
            claim = journal.begin(task=task, assignment=assignment)
        phase = "transport"
        response = ports.transport().forward_task(
            ctx.target_worker.url, "/api/internal/native-tasks/execute", payload,
            token=ctx.target_worker.token, timeout=max(1, min(int(policy["execute_timeout"]), 30)),
        )
        if not isinstance(response, dict) or response.get("status") not in {"completed", "failed", "cancelled"}:
            raise ValueError("native_autopilot_transport_outcome_unknown")
        phase = "result"
        ports.accept_result(
            tid=task.id, response=response, task=task.model_dump(), request_data=SimpleNamespace(command=None),
        )
        accepted = repository.get_by_id(task.id)
        if pi:
            phase = "evidence_completion"
            complete_pi(task_id=task.id, assignment=payload["evidence_assignment"])
            evidence_completed = True
        journal.settle(task=accepted, claim=claim, accepted=True)
        journal_completed = True
        if retirement is not None and accepted.status == 'completed':
            retirement(task_id=task.id)
        result.completed = accepted.status == "completed"
        result.failed = not result.completed
        result.failure_type = None if result.completed else "native_autopilot_execution_non_success"
        ctx.append_trace_event(task.id, "autopilot_native_execution", status=accepted.status,
                               delegated_to=ctx.target_worker.url)
    except Exception:
        # Unknown transport outcomes must not become invented terminal receipts or
        # trigger a generic LLM/local-Hub fallback. Recovery is a separate Hub policy.
        try:
            current = ctx.dependencies.current_task_status(ctx.task.id, app=ctx.app_ctx)
        except Exception:
            current = ""
        from agent.services.task_status_service import TERMINAL_TASK_STATUSES, normalize_task_status

        if journal is not None and claim is not None:
            try:
                stored = repository.get_by_id(ctx.task.id)
                if normalize_task_status(current) in TERMINAL_TASK_STATUSES and (not pi or evidence_completed):
                    journal.reconcile(task=stored)
                    journal_completed = True
                else:
                    journal.settle(task=stored, claim=claim, accepted=False)
            except Exception:
                # A surviving sending row still prevents a duplicate transport.
                pass
        if normalize_task_status(current) in TERMINAL_TASK_STATUSES and (
                not pi or (evidence_completed and journal_completed)):
            result.completed = normalize_task_status(current) == "completed"
            result.failed = not result.completed
            result.failure_type = None if result.completed else "native_autopilot_execution_non_success"
            return result
        if current and normalize_task_status(current) not in TERMINAL_TASK_STATUSES:
            try:
                ctx.update_local_task_status(ctx.task.id, "waiting_for_review",
                                             status_reason_code="native_autopilot_dispatch_blocked")
            except Exception:
                # A corrupt authoritative row may itself fail the storage guard.
                # Do not bypass that guard to fabricate a quarantine/terminal state.
                pass
        result.failed = True
        result.failure_type = "native_autopilot_dispatch_blocked"
        logging.getLogger(__name__).warning("Native Autopilot blocked in %s for task %s", phase, ctx.task.id)
        try:
            ctx.append_trace_event(ctx.task.id, "autopilot_native_dispatch_blocked",
                                   reason_code=result.failure_type, phase=phase)
        except Exception:
            pass
    return result
