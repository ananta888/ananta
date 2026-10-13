"""Bounded, restartable Hub search steps; workers never own the evolution loop."""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Protocol

from agent.services.harness_hypotheses import HarnessHypothesisGenerator, HarnessTelemetry
from agent.services.harness_mutation import MutationEngine, MutationError
from agent.services.harness_pareto import Candidate, ParetoSelector
from ananta_contracts.harness_genome import HarnessGenome, content_digest
from ananta_contracts.harness_search import HarnessSearchPolicy
from ananta_contracts.hub_evidence import validate_hub_evidence_assignment


class SearchStatePort(Protocol):
    def get(self, *, tenant_id: str, project_id: str, session_id: str) -> dict: ...
    def append(
        self, *, tenant_id: str, project_id: str, session_id: str, state: dict, expected_revision: int
    ) -> dict: ...


@dataclass(frozen=True, slots=True)
class SearchDispatchReceipt:
    dispatch_key: str
    input_digest: str
    assignment: Mapping


@dataclass(frozen=True, slots=True)
class SearchResultReceipt:
    dispatch_key: str
    assignment: Mapping
    result: Mapping


class HarnessSearchQueuePort(Protocol):
    """Hub-native, bounded task transport; no independent scheduling loop.

    submit is idempotent by dispatch_key and enforces persisted resource caps
    and absolute deadline before execution. poll returns immediately. cancel is
    idempotent, fences future submit for that key, and returns True only after
    the assignment is contained. Gateway policy remains mandatory at dispatch.
    """

    def submit(self, intent: Mapping) -> SearchDispatchReceipt: ...
    def poll(self, dispatch_key: str) -> SearchResultReceipt | None: ...
    def cancel(self, dispatch_key: str) -> bool: ...


class SearchEvidencePort(Protocol):
    def require_run_result(
        self,
        *,
        tenant_id,
        project_id,
        run_id,
        task_id,
        assignment_id,
        dispatch_lease_id,
        input_digest,
        source_ids,
        result_digest,
        expected_binding_digest,
        terminal_state,
    ): ...


class HarnessSearchService:
    def __init__(
        self,
        *,
        store: SearchStatePort,
        queue: HarnessSearchQueuePort,
        evidence: SearchEvidencePort,
        selector: ParetoSelector,
        clock: Callable[[], float] = time.time,
        enabled: Callable[[], bool] = lambda: True,
    ):
        self._store, self._queue, self._evidence, self._selector = store, queue, evidence, selector
        self._clock, self._enabled = clock, enabled
        self._hypotheses = HarnessHypothesisGenerator()

    def create(
        self,
        *,
        tenant_id: str,
        project_id: str,
        session_id: str,
        baseline: HarnessGenome,
        metrics: Mapping,
        suite_id: str,
        policy: HarnessSearchPolicy,
    ) -> dict:
        baseline.assert_valid()
        policy.assert_valid()
        # Baseline objective coverage is checked even for a one-element front.
        candidate = Candidate(baseline.genome_id, dict(metrics))
        self._selector.dominates(candidate, candidate)
        now = self._clock()
        if (
            not math.isfinite(now)
            or re.fullmatch(r"[0-9a-f]{64}", suite_id) is None
            or baseline.context.budget_tokens > policy.model_context_limit
        ):
            raise ValueError("harness_search_initial_binding_invalid")
        state = {
            "baseline": baseline.to_mapping(),
            "policy": policy.to_mapping(),
            "suite_id": suite_id,
            "created_at": now,
            "phase": "ready",
            "reason": None,
            "runs": 0,
            "attempts": 0,
            "charged_cost": 0.0,
            "charged_tokens": 0,
            "attempted_ideas": [],
            "failed_ideas": [],
            "population": [{"genome": baseline.to_mapping(), "metrics": dict(metrics)}],
            "current_genome": baseline.to_mapping(),
            "pending": None,
            "events": [],
        }
        return self._store.append(
            tenant_id=tenant_id, project_id=project_id, session_id=session_id, state=state, expected_revision=-1
        )

    def advance(self, *, tenant_id: str, project_id: str, session_id: str, telemetry: HarnessTelemetry) -> dict:
        scope = {"tenant_id": tenant_id, "project_id": project_id, "session_id": session_id}
        snapshot = self._store.get(**scope)
        state, revision = snapshot["state"], snapshot["revision"]
        if state["phase"] in {"finished", "containment_failed"}:
            return snapshot
        policy = HarnessSearchPolicy.from_mapping(state["policy"])
        now = self._clock()
        reason = None
        if not self._enabled():
            reason = "kill_switch"
        elif not math.isfinite(now) or now < state["created_at"]:
            reason = "clock_invalid"
        elif now >= state["created_at"] + policy.max_seconds:
            reason = "time_budget"
        elif state["pending"] and now >= state["pending"]["deadline"]:
            reason = "run_timeout"
        if reason and state["phase"] != "cancel_pending":
            state["reason"] = reason
            state["phase"] = "cancel_pending" if state["pending"] else "finished"
            return self._save(scope, state, revision, "stopped", reason=reason)
        if state["phase"] == "cancel_pending":
            pending = state["pending"]
            pending["cancel_attempts"] = pending.get("cancel_attempts", 0) + 1
            try:
                contained = self._queue.cancel(pending["dispatch_key"]) is True
            except Exception:
                contained = False
            if contained:
                state["phase"] = "finished"
            elif pending["cancel_attempts"] >= policy.max_dispatch_retries:
                state["phase"] = "containment_failed"
            return self._save(scope, state, revision, "cancelled" if contained else "cancel_retry")
        if state["phase"] == "dispatch_pending":
            return self._dispatch(scope, state, revision, policy)
        if state["phase"] == "awaiting_result":
            return self._collect(scope, state, revision, policy)

        if (
            state["runs"] >= policy.max_runs
            or state["attempts"] >= policy.max_runs * 4
            or state["charged_cost"] + policy.cost_per_run > policy.max_cost
            or state["charged_tokens"] + policy.tokens_per_run > policy.max_tokens
        ):
            state.update(phase="finished", reason="search_budget")
            return self._save(scope, state, revision, "stopped", reason="search_budget")
        parent = HarnessGenome.from_mapping(state["current_genome"])
        ideas = tuple(
            idea
            for idea in self._hypotheses.propose(
                parent,
                telemetry,
                context_budgets=policy.context_budgets,
                model_context_limit=policy.model_context_limit,
            )
            if idea.idea_key not in state["attempted_ideas"]
        )
        if not ideas:
            state.update(phase="finished", reason="hypotheses_exhausted")
            return self._save(scope, state, revision, "stopped", reason="hypotheses_exhausted")
        hypothesis, mode = self._hypotheses.choose(
            ideas, seed=policy.seed, attempt=state["attempts"], exploration_rate=policy.exploration_rate
        )
        state["attempts"] += 1
        state["attempted_ideas"].append(hypothesis.idea_key)
        try:
            mutation = MutationEngine().mutate(
                parent,
                operator_id=hypothesis.operator_id,
                params=json.loads(hypothesis.params_json),
                rationale=hypothesis.rationale,
            )
        except MutationError as exc:
            state["failed_ideas"].append(hypothesis.idea_key)
            return self._save(
                scope, state, revision, "mutation_rejected", hypothesis=hypothesis.to_mapping(), reason=str(exc)
            )
        child = replace(
            mutation.child_genome,
            parents=(parent.genome_id,),
            mutation_operators=(mutation.operator_id,),
            created_from=hypothesis.idea_key,
        )
        state["runs"] += 1
        # Reserve worst-case usage before any dispatch. Unknown/failed runs are
        # never refunded, so restart/retry cannot spend the same budget twice.
        state["charged_cost"] += policy.cost_per_run
        state["charged_tokens"] += policy.tokens_per_run
        state["pending"] = {
            "dispatch_key": "harness-search:"
            + content_digest({"session": session_id, "run": state["runs"], "tenant": tenant_id, "project": project_id}),
            "genome": child.to_mapping(),
            "suite_id": state["suite_id"],
            "hypothesis": hypothesis.to_mapping(),
            "max_cost": policy.cost_per_run,
            "max_tokens": policy.tokens_per_run,
            "deadline": min(now + policy.run_timeout_seconds, state["created_at"] + policy.max_seconds),
            "dispatch_attempts": 0,
        }
        state["phase"] = "dispatch_pending"
        return self._save(
            scope,
            state,
            revision,
            "proposed",
            hypothesis=hypothesis.to_mapping(),
            mode=mode,
            seed=policy.seed,
            child_genome_id=child.genome_id,
        )

    def _dispatch(self, scope, state, revision, policy):
        pending = state["pending"]
        pending["dispatch_attempts"] += 1
        try:
            receipt = self._queue.submit(pending)
            assignment = validate_hub_evidence_assignment(receipt.assignment)
            if (
                receipt.dispatch_key != pending["dispatch_key"]
                or re.fullmatch(r"[0-9a-f]{64}", receipt.input_digest) is None
            ):
                raise ValueError("harness_search_dispatch_binding_invalid")
            pending["receipt"] = {"input_digest": receipt.input_digest, "assignment": assignment}
            state["phase"] = "awaiting_result"
        except Exception:
            if pending["dispatch_attempts"] >= policy.max_dispatch_retries:
                state.update(phase="cancel_pending", reason="dispatch_failed")
            return self._save(scope, state, revision, "dispatch_retry")
        return self._save(scope, state, revision, "dispatched", run_id=assignment["run_id"])

    def _collect(self, scope, state, revision, policy):
        pending = state["pending"]
        # poll is a nonblocking Hub queue read, never an execution wait.
        try:
            receipt = self._queue.poll(pending["dispatch_key"])
        except Exception:
            pending["poll_errors"] = pending.get("poll_errors", 0) + 1
            if pending["poll_errors"] >= policy.max_dispatch_retries:
                state.update(phase="cancel_pending", reason="poll_failed")
            return self._save(scope, state, revision, "poll_retry")
        if receipt is None:
            return {"revision": revision, "state": state}
        expected = pending["receipt"]
        registered_terminal = False
        try:
            if receipt.dispatch_key != pending["dispatch_key"] or receipt.assignment != expected["assignment"]:
                raise ValueError("harness_search_result_binding_invalid")
            assignment = expected["assignment"]
            run = self._evidence.require_run_result(
                **{key: scope[key] for key in ("tenant_id", "project_id")},
                run_id=assignment["run_id"],
                task_id=assignment["task_id"],
                assignment_id=assignment["assignment_id"],
                dispatch_lease_id=assignment["dispatch_lease_id"],
                result_digest=content_digest(dict(receipt.result)),
                input_digest=expected["input_digest"],
                source_ids=assignment["source_ids"],
                expected_binding_digest=assignment["binding_digest"],
                terminal_state="succeeded" if receipt.result.get("passed") is True else "failed",
            )
            if run.input_digest != expected["input_digest"]:
                raise ValueError("harness_search_run_input_mismatch")
            registered_terminal = True
            result = receipt.result
            child = HarnessGenome.from_mapping(pending["genome"])
            if result.get("genome_id") != child.genome_id or result.get("suite_id") != state["suite_id"]:
                raise ValueError("harness_search_benchmark_binding_invalid")
            tokens, cost = result["model_tokens"], result["api_cost"]
            if (
                type(tokens) is not int
                or not 0 <= tokens <= policy.tokens_per_run
                or isinstance(cost, bool)
                or not isinstance(cost, (int, float))
                or not math.isfinite(cost)
                or not 0 <= cost <= policy.cost_per_run
            ):
                raise ValueError("harness_search_usage_invalid")
            if result.get("passed") is not True:
                raise ValueError("harness_search_gates_failed")
            candidate = Candidate(child.genome_id, dict(result["metrics"]))
            self._selector.dominates(candidate, candidate)
            state["population"].append(
                {
                    "genome": child.to_mapping(),
                    "metrics": dict(candidate.objectives),
                    "evidence": {
                        "run_id": run.run_id,
                        "source_ids": list(run.source_ids),
                        "binding_digest": run.binding_digest,
                        "result_digest": run.result_digest,
                        "scope": run.evidence_scope,
                        "synthetic": run.synthetic,
                    },
                    "usage": {"model_tokens": tokens, "api_cost": cost},
                }
            )
            population = [Candidate(item["genome"]["genome_id"], item["metrics"]) for item in state["population"]]
            selected = self._selector.select(population)
            state["current_genome"] = next(
                item["genome"] for item in state["population"] if item["genome"]["genome_id"] == selected.candidate_id
            )
            state["pareto_front"] = [item.candidate_id for item in self._selector.front(population)]
            event, reason = "evaluated", "ok"
        except (ValueError, KeyError, TypeError) as exc:
            state["failed_ideas"].append(pending["hypothesis"]["idea_key"])
            event, reason = "evaluation_rejected", type(exc).__name__
            if not registered_terminal:
                state.update(phase="cancel_pending", reason="unverified_result")
                return self._save(scope, state, revision, event, reason=reason)
        state.update(phase="ready", pending=None)
        return self._save(scope, state, revision, event, reason=reason)

    def _save(self, scope, state, revision, event, **details):
        state["events"].append({"event": event, **details})
        return self._store.append(**scope, state=state, expected_revision=revision)
