"""Hub dispatch admission for task-scoped propose/execute steps.

Split out of ``_task_scoped_step_orchestrator`` (SRP): admitting one
task-scoped dispatch (lease, assignment and run-evidence binding) before a
propose or execute step may run. ``_task_scoped_step_orchestrator`` re-exports
``_admit_task_scoped_dispatch``, which tests patch on that module.
"""

from __future__ import annotations

from agent.services._task_scoped_step_policies import _dispatch_admission_error


def _admit_task_scoped_dispatch(
    *,
    tid: str,
    task: dict,
    request_data,
    phase: str,
):
    """Issue on the Hub and consume on the Worker one recovery dispatch lease."""

    from agent.config import settings
    from agent.services.recovery_dispatch_gate_service import (
        RecoveryDispatchGateDecision,
        get_recovery_dispatch_gate_service,
        recovery_dispatch_request_fingerprint,
    )

    gate = get_recovery_dispatch_gate_service()
    provided_token = str(
        getattr(request_data, "dispatch_lease_token", None) or ""
    ).strip()
    role = str(settings.role or "").strip().lower()
    recovery_child = gate.is_recovery_child(task)
    run_evidence_context = getattr(
        request_data,
        "recovery_run_evidence_context",
        None,
    )
    if recovery_child and role == "hub":
        from agent.services.recovery_hub_run_evidence_service import (
            RecoveryHubRunEvidenceError,
            get_recovery_hub_run_evidence_service,
        )

        try:
            request_data.recovery_run_evidence_context = (
                get_recovery_hub_run_evidence_service()
                .bind_request_context(
                    task=task,
                    value=run_evidence_context,
                )
            )
        except RecoveryHubRunEvidenceError as exc:
            decision = RecoveryDispatchGateDecision(
                False,
                str(exc),
                source_task_id=str(
                    task.get("source_task_id") or ""
                )
                or None,
                plan_id=str(task.get("plan_id") or "") or None,
            )
            return {
                "error": _dispatch_admission_error(
                    tid=tid,
                    phase=phase,
                    decision=decision,
                ),
                "token": provided_token or None,
                "worker_url": None,
                "guard_local_result": False,
                "replayed": False,
                "request_fingerprint": "",
            }
    elif recovery_child and run_evidence_context is not None:
        from ananta_contracts.recovery_run_evidence import (
            RecoveryRunEvidenceContractError,
            validate_recovery_tool_run_context,
        )

        try:
            request_data.recovery_run_evidence_context = (
                validate_recovery_tool_run_context(
                    run_evidence_context,
                    task_id=tid,
                )
            )
        except RecoveryRunEvidenceContractError as exc:
            decision = RecoveryDispatchGateDecision(
                False,
                str(exc),
                source_task_id=str(
                    task.get("source_task_id") or ""
                )
                or None,
                plan_id=str(task.get("plan_id") or "") or None,
            )
            return {
                "error": _dispatch_admission_error(
                    tid=tid,
                    phase=phase,
                    decision=decision,
                ),
                "token": provided_token or None,
                "worker_url": None,
                "guard_local_result": False,
                "replayed": False,
                "request_fingerprint": "",
            }
    elif not recovery_child and run_evidence_context is not None:
        decision = RecoveryDispatchGateDecision(
            False,
            "recovery_tool_run_context_unexpected",
        )
        return {
            "error": _dispatch_admission_error(
                tid=tid,
                phase=phase,
                decision=decision,
            ),
            "token": provided_token or None,
            "worker_url": None,
            "guard_local_result": False,
            "replayed": False,
            "request_fingerprint": "",
        }
    if (
        role == "hub"
        and str(phase or "").strip().lower() == "execute"
        and recovery_child
    ):
        from agent.services.recovery_worker_result_service import (
            RecoveryWorkerResultError,
            get_recovery_worker_result_service,
        )

        try:
            bound_context = (
                get_recovery_worker_result_service()
                .bind_execute_proposal_context(
                    task=task,
                    value=getattr(
                        request_data,
                        "recovery_proposal_context",
                        None,
                    ),
                )
            )
        except RecoveryWorkerResultError as exc:
            decision = RecoveryDispatchGateDecision(
                False,
                str(exc),
                source_task_id=str(
                    task.get("source_task_id") or ""
                )
                or None,
                plan_id=str(task.get("plan_id") or "") or None,
            )
            return {
                "error": _dispatch_admission_error(
                    tid=tid,
                    phase=phase,
                    decision=decision,
                ),
                "token": provided_token or None,
                "worker_url": None,
                "guard_local_result": False,
                "replayed": False,
                "request_fingerprint": "",
            }
        request_data.recovery_proposal_context = bound_context
    request_fingerprint = recovery_dispatch_request_fingerprint(
        phase,
        request_data,
    )
    if role != "hub":
        decision = gate.admit_incoming_dispatch(
            task=task,
            token=provided_token or None,
            phase=phase,
            request_fingerprint=request_fingerprint,
        )
        return {
            "error": (
                None
                if decision.allowed
                else _dispatch_admission_error(
                    tid=tid,
                    phase=phase,
                    decision=decision,
                )
            ),
            "token": provided_token or None,
            "worker_url": None,
            "guard_local_result": False,
            "replayed": (
                decision.reason_code
                == "recovery_dispatch_worker_readmitted"
            ),
            "request_fingerprint": request_fingerprint,
        }

    if provided_token:
        local_url = str(
            settings.agent_url or f"http://localhost:{settings.port}"
        ).strip().rstrip("/")
        assigned_url = str(
            task.get("assigned_agent_url") or local_url
        ).strip().rstrip("/")
        decision = (
            gate.admit_dispatch_lease(
                tid,
                token=provided_token,
                phase=phase,
                worker_url=local_url,
                request_fingerprint=request_fingerprint,
                trusted_local=True,
            )
            if assigned_url == local_url
            else gate.validate_dispatch_lease(
                tid,
                token=provided_token,
                phase=phase,
                request_fingerprint=request_fingerprint,
            )
        )
        return {
            "error": (
                None
                if decision.allowed
                else _dispatch_admission_error(
                    tid=tid,
                    phase=phase,
                    decision=decision,
                )
            ),
            "token": provided_token,
            "worker_url": assigned_url,
            "guard_local_result": assigned_url == local_url,
            "replayed": (
                decision.reason_code
                == "recovery_dispatch_worker_readmitted"
            ),
            "request_fingerprint": request_fingerprint,
        }
    if not gate.is_recovery_child(task):
        if gate.is_recovery_source(task):
            decision = gate.evaluate_task(task)
            return {
                "error": _dispatch_admission_error(
                    tid=tid,
                    phase=phase,
                    decision=decision,
                ),
                "token": None,
                "worker_url": None,
                "guard_local_result": False,
                "replayed": False,
                "request_fingerprint": request_fingerprint,
            }
        return {
            "error": None,
            "token": None,
            "worker_url": None,
            "guard_local_result": False,
            "replayed": False,
            "request_fingerprint": request_fingerprint,
        }

    # Recovery capabilities are minted only by the Hub claim/dispatcher
    # path.  An authenticated API caller cannot create its own permit.
    return {
        "error": _dispatch_admission_error(
            tid=tid,
            phase=phase,
            decision=RecoveryDispatchGateDecision(
                False,
                "recovery_dispatch_lease_missing",
                source_task_id=str(
                    task.get("source_task_id") or ""
                )
                or None,
                plan_id=str(task.get("plan_id") or "") or None,
            ),
        ),
        "token": None,
        "worker_url": None,
        "guard_local_result": False,
        "replayed": False,
        "request_fingerprint": request_fingerprint,
    }
