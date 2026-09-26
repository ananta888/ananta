"""Read-only advisory admission using the Hub's actual start decision ports."""

from __future__ import annotations

from typing import Any, Protocol

from agent.services.workflow_backend import WorkflowRequest
from agent.services.workflow_control_service import RuntimeSelection, WorkflowPrincipal
from agent.services.workflow_runtime.execution_plan import ExecutionPlan, WorkflowRequestExecutionPlanAdapter


class WorkflowStartPreviewPort(Protocol):
    def preflight(self, **values: Any) -> tuple[RuntimeSelection, tuple[str, ...]]: ...


def workflow_start_plan(request: WorkflowRequest, *, tenant_id: str) -> ExecutionPlan:
    """Shared start/preflight adapter; no client projection substitutes validation."""
    errors = request.validate()
    if errors:
        raise ValueError("invalid_workflow_request:" + ",".join(errors))
    policy_version = str(
        request.metadata.get("policy_version")
        or request.policy_scope.get("policy_version")
        or "legacy-workflow-policy-v1"
    ).strip()
    return WorkflowRequestExecutionPlanAdapter.adapt(
        request,
        tenant_id=tenant_id,
        policy_version=policy_version,
    )


def assert_workflow_start_hashes(
    request: WorkflowRequest,
    plan: ExecutionPlan,
    *,
    expected_plan_hash: str | None = None,
    expected_definition_hash: str | None = None,
) -> None:
    """Optimistic editor preconditions, additional to full start revalidation."""
    if expected_plan_hash is not None and expected_plan_hash != plan.plan_hash:
        raise ValueError("workflow_start_plan_hash_mismatch")
    definition_hash = (request.execution_graph or {}).get("definition_hash")
    if expected_definition_hash is not None and expected_definition_hash != definition_hash:
        raise ValueError("workflow_start_definition_hash_mismatch")


class BpmnWorkflowPreflight:
    def __init__(self, control: WorkflowStartPreviewPort) -> None:
        self._control = control

    def evaluate(
        self,
        request: WorkflowRequest,
        *,
        principal: WorkflowPrincipal,
        preferred_runtime: str,
    ) -> dict[str, Any]:
        plan = workflow_start_plan(request, tenant_id=principal.tenant_id)
        plan.assert_valid()
        result: dict[str, Any] = {
            "ready": False,
            "advisory": True,
            "workflow_id": request.workflow_id,
            "definition_hash": (request.execution_graph or {}).get("definition_hash"),
            "plan_hash": plan.plan_hash,
            "runtime_id": None,
            "reason_codes": [],
            "required_capabilities": sorted(plan.capabilities),
            "rejected": [],
        }
        try:
            selection, errors = self._control.preflight(
                principal=principal,
                plan=plan,
                run_id=str(request.metadata.get("run_id") or request.workflow_id).strip(),
                preferred_runtime=preferred_runtime,
                allowed_runtimes=(preferred_runtime,),
            )
        except PermissionError as exc:
            # Do not expose another owner's existing workflow bindings.
            code = str(exc)
            result["reason_codes"] = [
                code
                if code.startswith("workflow_rollout_") and code.replace("_", "").isalnum()
                else "workflow_preflight_denied"
            ]
            return result
        except (RuntimeError, ValueError, LookupError) as exc:
            code = str(exc).split(":", 1)[0]
            result["reason_codes"] = [
                code
                if code.startswith(("workflow_", "runtime_")) and code.replace("_", "").isalnum()
                else "workflow_preflight_unavailable"
            ]
            return result
        result.update(
            ready=not errors,
            runtime_id=selection.runtime_id or None,
            reason_codes=list(
                dict.fromkeys(
                    [
                        *errors,
                        *([selection.reason_code] if errors else []),
                        *(item["reason_code"] for item in selection.rejected if errors),
                    ]
                )
            ),
            rejected=list(selection.rejected),
        )
        return result
