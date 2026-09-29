"""Tick-driven scheduling steps of the Native graph orchestrator: result collection,
conditional skips, merges, dispatch, and gates.
"""

from __future__ import annotations

from typing import Any

from agent.services.bpmn_control_nodes import decide_control
from agent.services.bpmn_event_wait_contracts import EventWaitError
from agent.services.bpmn_projection_control import project_control_result
from agent.services.native_graph_models import NATIVE_GRAPH_TERMINAL_STATUSES, NativeGraphRequest, NativeRunState
from agent.services.workflow_runtime._serialization import sha256_json
from agent.services.workflow_runtime.components import validate_compiled_component_output
from agent.services.workflow_runtime.execution_plan import ExecutionNode, ExecutionPlan
from agent.services.workflow_runtime.parallel import BranchResult

_TERMINAL = NATIVE_GRAPH_TERMINAL_STATUSES


class NativeGraphSchedulingMixin:
    """Advance a Native graph run by one scheduling tick; mixed into ``NativeGraphOrchestrator``."""

    def _tick(self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState) -> None:
        if state.status != "running":
            return
        if self._expire_bpmn_run(plan, request, state):
            return
        self._collect_results(plan, request, state)
        if state.status != "running":
            return
        self._resolve_conditional_skips(plan, request, state)
        if state.status != "running":
            return
        self._execute_ready_merges(plan, request, state)
        if state.status != "running":
            return
        self._reconcile_bpmn_events(plan, request, state)
        if state.status != "running":
            return
        self._dispatch_ready(plan, request, state)
        self._finish_if_terminal(plan, request, state)

    def _expire_bpmn_run(self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState) -> bool:
        if state.status in _TERMINAL or state.bpmn_deadline_at is None:
            return False
        if float(self._clock()) < state.bpmn_deadline_at:
            return False
        self._fail_run(plan, request, state, "bpmn_run_deadline_exceeded")
        return True

    def _collect_results(self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState) -> None:
        if not state.running:
            return
        results = self._queue.poll(
            tenant_id=plan.tenant_id,
            run_id=request.run_id,
            hub_task_ids=tuple(sorted(item["hub_task_id"] for item in state.running.values())),
        )
        nodes = {node.node_id: node for node in plan.nodes}
        for result in sorted(results, key=lambda item: (item.node_id, item.result_id)):
            running = state.running.get(result.node_id)
            if running is None:
                continue
            self._assert_result_binding(plan, request, result, running)
            node = nodes[result.node_id]
            if state.control_lease is not None:
                state.control_lease.ensure_valid()
            exceeded = self._budget_exceeded(plan, state, result)
            if exceeded:
                self._fail_run(plan, request, state, exceeded)
                return
            owner_values = {
                "tenant_id": plan.tenant_id,
                "run_id": request.run_id,
                "step_id": result.node_id,
                "attempt_id": result.attempt_id,
                "owner_id": str(running["owner_id"]),
                "fencing_token": result.fencing_token,
                "expected_revision": int(running["ownership_revision"]),
                "now": float(self._clock()),
            }
            if result.status == "completed":
                validate_compiled_component_output(node, result.output_data)
                self._validate_artifacts(node, result)
                if state.control_lease is not None:
                    state.control_lease.ensure_valid()
                acknowledge = self._ownership.acknowledge_result
                lease_values = {}
                if state.control_lease is not None:
                    acknowledge = getattr(self._ownership, "acknowledge_result_fenced", None)
                    if not callable(acknowledge):
                        raise RuntimeError("bpmn_result_recipient_fencing_unavailable")
                    lease_values["lease"] = state.control_lease
                acknowledged = acknowledge(
                    **owner_values,
                    **lease_values,
                    result_ack_key=(
                        "bpmn-result:" + sha256_json({**result.to_dict(), "output_data": result.output_data})
                        if state.control_lease is not None
                        else result.result_id
                    ),
                )
                state.running.pop(result.node_id, None)
                self._consume_budget(plan, state, result)
                state.completed.add(result.node_id)
                state.node_results[result.node_id] = dict(result.output_data)
                state.artifact_refs.update(result.artifact_refs)
                self._emit(
                    state,
                    plan=plan,
                    request=request,
                    step_id=result.node_id,
                    attempt=acknowledged.fencing_token,
                    event_type="workflow.step.completed",
                    dedupe_key=f"native:{request.run_id}:{result.node_id}:{result.attempt_id}:completed",
                    payload={
                        "artifact_ids": sorted(result.artifact_refs),
                        "budget_usage": dict(result.budget_usage),
                    },
                )
                self._emit_side_effect_if_present(plan, request, state, result, running)
                continue
            state.running.pop(result.node_id, None)
            failure = result.reason_code or f"native_node_{result.status}"
            fail_attempt = getattr(self._ownership, "fail_attempt", None)
            lease_values = {}
            if state.control_lease is not None:
                fail_attempt = getattr(self._ownership, "fail_attempt_fenced", None)
                lease_values["lease"] = state.control_lease
            if fail_attempt is None:
                raise RuntimeError("native_ownership_failure_transition_unavailable")
            failed_owner = fail_attempt(
                **owner_values,
                **lease_values,
                failure_code=failure,
                dead_letter=False,
            )
            self._consume_budget(plan, state, result)
            attempt_count = state.attempts.get(result.node_id, 1)
            maximum = (node.budget or plan.budget).max_attempts
            if attempt_count < maximum:
                self._emit(
                    state,
                    plan=plan,
                    request=request,
                    step_id=result.node_id,
                    attempt=failed_owner.fencing_token,
                    event_type="workflow.step.retry_scheduled",
                    dedupe_key=f"native:{request.run_id}:{result.node_id}:{result.attempt_id}:retry",
                    payload={"reason_code": failure, "next_attempt": attempt_count + 1},
                )
                continue
            state.failed[result.node_id] = failure
            self._emit(
                state,
                plan=plan,
                request=request,
                step_id=result.node_id,
                attempt=failed_owner.fencing_token,
                event_type="workflow.step.failed",
                dedupe_key=f"native:{request.run_id}:{result.node_id}:{result.attempt_id}:failed",
                payload={"reason_code": failure},
            )
            if str(node.metadata.get("failure_policy") or "fail") != "continue":
                self._fail_run(plan, request, state, failure)
                return

    def _resolve_conditional_skips(
        self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState
    ) -> None:
        terminal = state.completed | state.skipped | set(state.failed)
        incoming: dict[str, list[Any]] = {node.node_id: [] for node in plan.nodes}
        for edge in plan.edges:
            incoming[edge.target].append(edge)
        changed = True
        while changed:
            changed = False
            terminal = state.completed | state.skipped | set(state.failed)
            for node in sorted(plan.nodes, key=lambda item: item.node_id):
                if node.node_id in terminal or node.node_id in state.running or not incoming[node.node_id]:
                    continue
                if not all(edge.source in terminal for edge in incoming[node.node_id]):
                    continue
                evaluations = [self._edge_result(edge, state) for edge in incoming[node.node_id]]
                if any(result.value is None for result in evaluations):
                    reason = next(result.reason_code for result in evaluations if result.value is None)
                    self._fail_run(plan, request, state, reason)
                    return
                mode = str(node.metadata.get("join_mode") or "any")
                control = node.metadata.get("bpmn_control", {})
                active = sum(result.matches for result in evaluations)
                if control.get("kind") == "parallel" and 0 < active < len(evaluations):
                    self._fail_run(plan, request, state, "bpmn_parallel_incomplete_activation")
                    return
                if control.get("kind") == "exclusive" and active > 1:
                    self._fail_run(plan, request, state, "bpmn_exclusive_multiple_arrivals")
                    return
                route_matches = (
                    all(result.matches for result in evaluations)
                    if mode == "all"
                    else any(result.matches for result in evaluations)
                )
                if route_matches:
                    continue
                state.skipped.add(node.node_id)
                self._emit(
                    state,
                    plan=plan,
                    request=request,
                    step_id=node.node_id,
                    event_type="workflow.step.skipped",
                    dedupe_key=f"native:{request.run_id}:{node.node_id}:route-skipped",
                    payload={"reason_code": "native_route_not_selected"},
                )
                changed = True

    def _execute_ready_merges(self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState) -> None:
        incoming: dict[str, list[str]] = {node.node_id: [] for node in plan.nodes}
        for edge in plan.edges:
            incoming[edge.target].append(edge.source)
        terminal = state.completed | state.skipped | set(state.failed)
        for node in sorted(plan.nodes, key=lambda item: item.node_id):
            if node.node_type != "merge" or node.node_id in terminal or node.node_id in state.running:
                continue
            sources = incoming[node.node_id]
            if not sources or not set(sources).issubset(terminal):
                continue
            branches = [
                BranchResult(
                    node_id=source,
                    status="completed" if source in state.completed else "failed",
                    value=state.node_results.get(source),
                    reason_code=state.failed.get(source, "native_branch_skipped"),
                )
                for source in sources
            ]
            merged = self._merge.merge(
                branches,
                strategy=str(node.metadata.get("merge_strategy") or ""),
                partial_failure=str(node.metadata.get("partial_failure") or "fail"),
            )
            if merged.status != "completed":
                state.failed[node.node_id] = merged.reason_code
                self._fail_run(plan, request, state, merged.reason_code)
                return
            state.completed.add(node.node_id)
            state.node_results[node.node_id] = merged.value
            for artifact_id in node.output_artifacts:
                state.artifact_refs[artifact_id] = f"artifact://native/{request.run_id}/{node.node_id}/{artifact_id}"
            self._emit(
                state,
                plan=plan,
                request=request,
                step_id=node.node_id,
                event_type="workflow.step.completed",
                dedupe_key=f"native:{request.run_id}:{node.node_id}:merged",
                payload={
                    "merge_strategy": node.metadata.get("merge_strategy"),
                    "failed_branches": list(merged.failed_branches),
                },
            )
            terminal.add(node.node_id)

    def _dispatch_ready(self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState) -> None:
        batch = self._fan_out.select_ready(
            plan,
            completed_node_ids=state.completed | state.skipped,
            running_node_ids=set(state.running),
            waiting_node_ids=set(state.bpmn_waits) - state.completed,
            failed_node_ids=set(state.failed),
            tenant_limit=request.tenant_parallel_limit,
            worker_limit=request.worker_parallel_limit,
        )
        nodes = {node.node_id: node for node in plan.nodes}
        for candidate in batch.candidates:
            if self._expire_bpmn_run(plan, request, state):
                return
            node = nodes[candidate.node_id]
            if node.node_type == "merge":
                continue
            if not self._route_matches(plan, node, state):
                continue
            if node.gate_id and node.gate_id not in state.approved_gates:
                self._open_gate(plan, request, state, node)
                continue
            if node.node_type == "bpmn_wait":
                try:
                    view = self._bpmn_events.arm(**self._bpmn_event_hooks(plan, request, state), node=node)
                except EventWaitError as exc:
                    state.failed[node.node_id] = str(exc)
                    self._fail_run(plan, request, state, str(exc))
                    return
                self._emit(
                    state,
                    plan=plan,
                    request=request,
                    step_id=node.node_id,
                    event_type="workflow.bpmn.catch.waiting",
                    dedupe_key=view.wakeup_id + ":waiting",
                    payload={
                        "kind": view.kind,
                        "activation_id": view.target.activation_id,
                        "wakeup_id": view.wakeup_id,
                        "expires_at": view.expires_at,
                        "due_at": view.due_at,
                    },
                )
                continue
            if node.node_type == "bpmn_control":
                try:
                    context = self._condition_context(state, node=node)
                    output = (
                        project_control_result(
                            node,
                            input_data=state.input_data,
                            results=state.node_results,
                            completed_node_ids=state.completed,
                            skipped_node_ids=state.skipped,
                        )
                        if node.metadata["bpmn_control"]["kind"] == "projection"
                        else decide_control(node.metadata["bpmn_control"], context)
                    )
                except ValueError as exc:
                    self._fail_run(plan, request, state, str(exc))
                    return
                state.node_results[node.node_id] = output
                state.completed.add(node.node_id)
                self._emit(
                    state,
                    plan=plan,
                    request=request,
                    step_id=node.node_id,
                    event_type="workflow.step.completed",
                    dedupe_key=f"native:{request.run_id}:{node.node_id}:bpmn-control",
                    payload={
                        "control_kind": node.metadata["bpmn_control"]["kind"],
                        "selected_edge": (
                            output.get("selected_edge")
                            if node.metadata["bpmn_control"]["kind"] == "exclusive"
                            else None
                        ),
                    },
                )
                continue
            if node.node_type == "checkpoint":
                state.completed.add(node.node_id)
                state.node_results[node.node_id] = {"checkpoint": "hub-owned"}
                for artifact_id in node.output_artifacts:
                    state.artifact_refs[artifact_id] = (
                        f"checkpoint://{plan.tenant_id}/{request.run_id}/{request.control_task_id}"
                    )
                self._emit(
                    state,
                    plan=plan,
                    request=request,
                    step_id=node.node_id,
                    event_type="workflow.step.completed",
                    dedupe_key=f"native:{request.run_id}:{node.node_id}:checkpoint-node",
                    payload={"checkpoint_policy": "hub-owned"},
                )
                continue
            allowed, reason = self._policy.authorize_delegation(plan=plan, node=node, state=state)
            if not allowed:
                self._fail_run(plan, request, state, reason or "native_delegation_policy_denied")
                return
            self._submit_node(plan, request, state, node)

    def _submit_node(
        self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState, node: ExecutionNode
    ) -> None:
        if state.control_lease is not None:
            state.control_lease.ensure_valid()
            # Commit control decisions and input bindings before any task leaves
            # the Hub. A post-ingest interruption can then adopt the exact task.
            previous_owner = self._ownership.get(tenant_id=plan.tenant_id, run_id=request.run_id, step_id=node.node_id)
            if previous_owner is None or previous_owner.status not in {"active", "completed"}:
                self._save_checkpoint(plan, request, state)
        try:
            node_input = self._node_input(node, state)
        except ValueError as exc:
            self._fail_run(plan, request, state, str(exc))
            return
        self._delegation.submit(
            plan=plan,
            request=request,
            state=state,
            node=node,
            input_data=node_input,
            fail=self._fail_run,
            emit=self._emit,
        )

    def _open_gate(
        self, plan: ExecutionPlan, request: NativeGraphRequest, state: NativeRunState, node: ExecutionNode
    ) -> None:
        if node.gate_id in state.open_gates:
            return
        state.open_gates[node.gate_id] = node.node_id
        self._emit(
            state,
            plan=plan,
            request=request,
            step_id=node.node_id,
            event_type="workflow.approval.requested",
            dedupe_key=f"native:{request.run_id}:{node.gate_id}:requested",
            payload={"gate_id": node.gate_id},
        )
        gate = next(gate for gate in plan.gates if gate.gate_id == node.gate_id)
        if gate.gate_type == "resume":
            state.status = "paused"
            self._emit(
                state,
                plan=plan,
                request=request,
                step_id=node.node_id,
                event_type="workflow.run.paused",
                dedupe_key=f"native:{request.run_id}:{node.gate_id}:paused",
                payload={"gate_id": node.gate_id},
            )
        else:
            state.status = "waiting_for_approval"
