"""Hub-owned bounded DAG dispatch through the existing policy/execution gateway.

No worker scheduling, identity issuance, credential propagation or model-owned
policy. Receipts are shared through the Hub database; uncertain work is not retried.
"""

from __future__ import annotations

import concurrent.futures
import contextvars
import hashlib
import json
import math
import threading
import time
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping, Protocol

from agent.services.tiny_router.validation import validate_json_value
from ananta_contracts.multi_tool_plan import PLAN, PLAN_SCHEMA, MultiToolPlan, plan_fingerprint
from ananta_contracts.tool_plan_graph import ToolDependency, dependency_waves


class PlanReceipts(Protocol):
    def claim(self, scope_digest: str, request_digest: str) -> tuple[bool, dict]: ...
    def finish(self, scope_digest: str, request_digest: str, response: dict) -> None: ...


class PlanGateway(Protocol):
    def execute_direct(self, **kwargs: Any) -> dict: ...


class PlanAudit(Protocol):
    def record(self, event: Mapping[str, Any]) -> None: ...


@dataclass(frozen=True)
class ToolPlanScope:
    """Populated by Hub task lookup, never by model text or worker output."""

    tenant_id: str
    project_id: str
    task_id: str
    workspace_ref: str
    allowed_tools: tuple[str, ...]
    mutation_mode: str = "read_only"
    goal_id: str | None = None

    def validate(self) -> None:
        if any(
            not isinstance(v, str) or not v.strip() or len(v) > 200
            for v in (self.tenant_id, self.project_id, self.task_id)
        ):
            raise ValueError("plan_scope_required")
        if not self.workspace_ref or not self.allowed_tools:
            raise ValueError("plan_workspace_and_scope_required")


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class HubMultiToolExecutionService:
    def __init__(
        self,
        *,
        registry,
        policy,
        gateway: PlanGateway,
        receipts: PlanReceipts,
        audit: PlanAudit,
        max_inflight: int = 4,
        clock: Callable[[], float] = time.monotonic,
    ):
        if type(max_inflight) is not int or not 1 <= max_inflight <= 16:
            raise ValueError("plan_admission_invalid")
        self._registry, self._policy, self._gateway = registry, policy, gateway
        self._receipts, self._audit, self._clock = receipts, audit, clock
        self._slots = threading.BoundedSemaphore(max_inflight)

    def execute(
        self,
        plan: MultiToolPlan,
        *,
        scope: ToolPlanScope,
        agent_cfg: dict,
        dependencies: Mapping[str, ToolDependency] | None = None,
        timeout_seconds: float = 30,
        max_calls: int = 4,
        cancel_check: Callable[[], bool] = lambda: False,
    ) -> dict:
        graph = dict(dependencies or {})
        try:
            scope.validate()
            if (
                type(timeout_seconds) not in {int, float}
                or not math.isfinite(timeout_seconds)
                or not 0 < timeout_seconds <= 120
                or type(max_calls) is not int
                or not 1 <= max_calls <= 32
            ):
                raise ValueError("plan_budget_invalid")
            if plan.schema_version != PLAN_SCHEMA or plan.status != PLAN or not plan.calls():
                raise ValueError("plan_not_ready")
            if len(plan.calls()) > max_calls:
                raise ValueError("plan_max_calls_exceeded")
            steps = {step.tool: step for step in plan.calls()}
            waves = dependency_waves([step.tool for step in plan.calls()], graph)
            for name, step in steps.items():
                spec = self._registry.get_tool(name)
                if name not in scope.allowed_tools or spec is None:
                    raise ValueError("plan_tool_outside_scope")
                # Writes/external agents return to the existing single-call
                # approval lifecycle, even if a model claims high confidence.
                if spec.risk_class != "read" or spec.category != "read_only":
                    raise ValueError("plan_side_effect_requires_single_call_gate")
                if not graph.get(name, ToolDependency()).argument_sources:
                    if validate_json_value(dict(step.arguments), spec.argument_schema):
                        raise ValueError("plan_arguments_failed_schema")
            scope_digest = _digest([scope.tenant_id, scope.project_id, scope.task_id])
            request_digest = _digest(
                {
                    "scope": asdict(scope),
                    "plan": plan_fingerprint(plan),
                    "graph": {k: asdict(v) for k, v in graph.items()},
                    "config": agent_cfg,
                    "max_calls": max_calls,
                    "timeout": timeout_seconds,
                }
            )
            owned, replay = self._receipts.claim(scope_digest, request_digest)
            if not owned:
                return replay
        except (ValueError, TypeError, KeyError) as exc:
            return {"status": "blocked", "reason": str(exc)}
        except Exception:
            return {"status": "blocked", "reason": "plan_receipt_unavailable"}
        deadline = self._clock() + timeout_seconds
        try:
            from flask import current_app, has_app_context

            app = current_app._get_current_object() if has_app_context() else None
            self._event(scope, "plan_admitted", request_digest)
            result = self._execute_waves(steps, waves, graph, scope, agent_cfg, deadline, cancel_check, app)
            self._event(scope, "plan_completed", request_digest, status=result["status"])
            self._receipts.finish(scope_digest, request_digest, result)
            return result
        except Exception:
            # Audit or persistence failure never becomes a silent execute retry.
            return {"status": "blocked", "reason": "plan_audit_or_receipt_unavailable"}

    def _execute_waves(self, steps, waves, graph, scope, cfg, deadline, cancelled, app):
        results: dict[str, dict] = {}
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="hub-tool-plan")
        try:
            for wave in waves:
                pending = {}
                for name in wave:
                    if cancelled() or self._clock() >= deadline:
                        return self._aggregate(results, "blocked", "plan_cancelled_or_deadline")
                    dependency = graph.get(name, ToolDependency())
                    if any(results[parent].get("status") != "ok" for parent in dependency.parents()):
                        results[name] = {"status": "skipped", "reason": "dependency_failed"}
                        continue
                    try:
                        if any(
                            type(condition.source.read(results)) is not type(condition.equals)
                            or condition.source.read(results) != condition.equals
                            for condition in dependency.when
                        ):
                            results[name] = {"status": "skipped", "reason": "condition_false"}
                            continue
                        arguments = dict(steps[name].arguments)
                        arguments.update(
                            {key: reference.read(results) for key, reference in dependency.argument_sources.items()}
                        )
                    except (KeyError, ValueError, IndexError, TypeError):
                        results[name] = {"status": "blocked", "reason": "dependency_result_invalid"}
                        continue
                    spec = self._registry.get_tool(name)
                    if spec is None or spec.risk_class != "read" or spec.category != "read_only":
                        results[name] = {"status": "blocked", "reason": "plan_registry_changed"}
                        continue
                    if validate_json_value(arguments, spec.argument_schema):
                        results[name] = {"status": "blocked", "reason": "plan_arguments_failed_schema"}
                        continue
                    policy = self._policy.evaluate(
                        tool_name=name,
                        arguments=arguments,
                        allowed_tools=list(scope.allowed_tools),
                        mutation_mode=scope.mutation_mode,
                        task_id=scope.task_id,
                        goal_id=scope.goal_id,
                    )
                    if not policy.allowed:
                        results[name] = {"status": policy.decision, "reason": policy.reason}
                        continue
                    if not self._slots.acquire(blocking=False):
                        results[name] = {"status": "blocked", "reason": "plan_admission_full"}
                        continue
                    try:
                        self._event(scope, "tool_dispatch", _digest([name, arguments]), tool=name)
                        context = contextvars.copy_context()
                        future = executor.submit(
                            context.run, self._dispatch, name, arguments, scope, deepcopy(cfg), deadline, app
                        )
                    except Exception:
                        self._slots.release()
                        raise
                    future.add_done_callback(lambda _: self._slots.release())
                    pending[future] = name
                while pending:
                    if cancelled() or self._clock() >= deadline:
                        for future, name in pending.items():
                            future.cancel()
                            results[name] = {"status": "timeout", "reason": "plan_result_uncertain_no_retry"}
                        return self._aggregate(results, "blocked", "plan_cancelled_or_deadline")
                    done, _ = concurrent.futures.wait(
                        pending,
                        timeout=min(0.05, max(0, deadline - self._clock())),
                        return_when=concurrent.futures.FIRST_COMPLETED,
                    )
                    if cancelled() or self._clock() >= deadline:
                        continue
                    for future in done:
                        name = pending.pop(future)
                        try:
                            results[name] = self._bounded_result(future.result(), min(16000, 100000 // len(steps)))
                        except Exception:
                            results[name] = {"status": "error", "reason": "plan_dispatch_failed"}
                        self._event(
                            scope,
                            "tool_completed",
                            _digest(results[name]),
                            tool=name,
                            status=results[name].get("status"),
                        )
            status = "completed" if all(r.get("status") in {"ok", "skipped"} for r in results.values()) else "blocked"
            return self._aggregate(results, status, "plan_results_aggregated")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _dispatch(self, name, arguments, scope, cfg, deadline, app):
        from contextlib import nullcontext

        clean_cfg = cfg
        direct = {**clean_cfg.get("hub_direct_execution", {}), "allowed_tools": list(scope.allowed_tools)}
        clean_cfg["hub_direct_execution"] = direct
        remaining = deadline - self._clock()
        if remaining <= 0:
            return {"status": "timeout", "reason": "plan_dispatch_deadline"}
        # Runtime tools retain their own bounded deadlines. Late results are
        # rejected by the Hub and never cause an automatic repeated dispatch.
        direct["max_result_chars"] = min(int(direct.get("max_result_chars") or 8000), 8000)
        with app.app_context() if app is not None else nullcontext():
            result = self._gateway.execute_direct(
                tool_name=name,
                arguments=arguments,
                agent_cfg=clean_cfg,
                task_id=scope.task_id,
                goal_id=scope.goal_id,
                workspace_ref=scope.workspace_ref,
                mutation_mode=scope.mutation_mode,
                reason_code="hub_multi_tool_plan",
            )
        return result.get("tool_result") or {"status": "blocked", "reason": result.get("kind", "plan_gateway_invalid")}

    @staticmethod
    def _bounded_result(result, max_bytes=16000):
        raw = json.dumps(result, allow_nan=False)
        if len(raw.encode()) > max_bytes:
            return {"status": "error", "reason": "plan_result_too_large"}
        return json.loads(raw)

    @staticmethod
    def _aggregate(results, status, reason):
        return {
            "schema": "ananta.multi_tool_execution.v1",
            "status": status,
            "reason": reason,
            "results": results,
            "needs_reasoning": any(r.get("status") not in {"ok", "skipped"} for r in results.values()),
            "production_release_evidence": False,
        }

    def _event(self, scope, event, digest, **metadata):
        self._audit.record(
            {
                "event": event,
                "tenant_id": scope.tenant_id,
                "project_id": scope.project_id,
                "task_id": scope.task_id,
                "digest": digest,
                **metadata,
            }
        )
