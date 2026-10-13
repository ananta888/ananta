"""Restart, idempotency, budget and evidence failures of the Hub search loop."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.db_models.harness_search import HarnessSearchRevisionDB
from agent.repositories.evidence_identity import SqlEvidenceIdentityRepository
from agent.repositories.harness_search import HarnessSearchConflict, HarnessSearchStore
from agent.services.harness_gates import GateEngine, security_gate
from agent.services.harness_hypotheses import HarnessHypothesisGenerator, HarnessTelemetry
from agent.services.harness_pareto import ParetoSelector
from agent.services.harness_sandbox_service import HarnessSandboxService
from agent.services.harness_search_service import HarnessSearchService, SearchDispatchReceipt, SearchResultReceipt
from agent.services.hub_evidence_gate_service import EvidenceGateRequest, HubEvidenceGateService
from agent.services.hub_evidence_registry_service import HubEvidenceRegistryService
from ananta_contracts.harness_search import HarnessSearchPolicy
from tests.harness.test_harness_sandbox import manifest

SCOPE = {"tenant_id": "tenant", "project_id": "project", "session_id": "search-test"}
TELEMETRY = HarnessTelemetry(truncation_events=2, safe_fusion_opportunities=3)


class QueueDouble:
    def __init__(self, registry):
        self.registry = registry
        self.receipts, self.results, self.cancelled = {}, {}, []
        self.submit_calls = 0
        self.dispatch_failures = 0
        self.pending = False
        self.fail_quality = False
        self.contained = True

    def submit(self, intent):
        self.submit_calls += 1
        key = intent["dispatch_key"]
        if key not in self.receipts:
            spec, suite = manifest()
            spec = replace(spec, genome_json=json.dumps(intent["genome"]))
            captured = {}
            quality_failed = self.fail_quality

            class WorkerDouble:
                def execute(self, manifest, assignment):
                    captured["assignment"] = assignment
                    return {
                        "passed": True,
                        "assignment": assignment,
                        "manifest_digest": manifest.digest,
                        "genome_id": manifest.genome.genome_id,
                        "suite_id": manifest.suite_id,
                        "observation": {
                            "metrics": {"task_correctness": 0.9, "token_input": 0},
                            "gate_inputs": {"security_ok": not quality_failed},
                            "completed_task_ids": ["explore", "holdout"],
                        },
                    }

            gate = HubEvidenceGateService(self.registry)
            service = HarnessSandboxService(
                evidence=gate,
                worker=WorkerDouble(),
                suite=suite,
                gates=GateEngine([security_gate("security:scope", "security_ok")]),
            )
            req = EvidenceGateRequest(
                "tenant",
                "project",
                key,
                key,
                "lease:" + key,
                spec.repository_revision,
                "a" * 64,
                "a" * 64,
                "a" * 64,
                "test",
                "local",
                key,
                (),
                True,
            )
            outcome = service.evaluate(spec, request=req)
            self.receipts[key] = SearchDispatchReceipt(key, spec.digest, captured["assignment"])
            self.results[key] = SearchResultReceipt(key, captured["assignment"], dict(outcome.evidence.execution))
        if self.dispatch_failures:
            self.dispatch_failures -= 1
            raise TimeoutError("acknowledgement lost after dispatch")
        return self.receipts[key]

    def poll(self, dispatch_key):
        return None if self.pending else self.results[dispatch_key]

    def cancel(self, dispatch_key):
        self.cancelled.append(dispatch_key)
        return self.contained


@pytest.fixture
def runtime():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(
        engine,
        tables=[
            HarnessSearchRevisionDB.__table__,
            HubRunEvidenceIdentityDB.__table__,
            HubSourceEvidenceIdentityDB.__table__,
        ],
    )
    registry = HubEvidenceRegistryService(SqlEvidenceIdentityRepository(engine))
    store = HarnessSearchStore(engine=lambda: engine)
    queue = QueueDouble(registry)
    clock = [1000.0]
    enabled = [True]

    def service():
        return HarnessSearchService(
            store=store,
            queue=queue,
            evidence=registry,
            selector=ParetoSelector(maximize=("task_correctness",), minimize=("token_input",)),
            clock=lambda: clock[0],
            enabled=lambda: enabled[0],
        )

    return store, queue, clock, enabled, service


def start(runtime, **policy):
    spec, _ = manifest()
    defaults = {"model_context_limit": 131072, "seed": 12, "exploration_rate": 0.0}
    defaults.update(policy)
    return runtime[-1]().create(
        **SCOPE,
        baseline=spec.genome,
        metrics={"task_correctness": 0.5, "token_input": 0},
        suite_id=spec.suite_id,
        policy=HarnessSearchPolicy(**defaults),
    )


def advance(runtime):
    # A fresh controller on every call verifies restart does not reset state.
    return runtime[-1]().advance(**SCOPE, telemetry=TELEMETRY)


def test_search_resumes_between_every_stage_and_keeps_baseline_immutable(runtime):
    initial = start(runtime)
    for _ in range(15):
        snapshot = advance(runtime)
        if snapshot["state"]["phase"] == "finished":
            break
    state = snapshot["state"]
    assert state["phase"] == "finished" and state["runs"] == 2
    # Only context ideas are possible: the baseline grants no tools.
    assert state["baseline"] == initial["state"]["baseline"]
    assert state["baseline"]["context"]["budget_tokens"] == 32768
    assert len(state["attempted_ideas"]) == len(set(state["attempted_ideas"]))
    assert all(event["hypothesis"]["rationale"] for event in state["events"] if event["event"] == "proposed")
    assert runtime[1].submit_calls == 2


def test_lost_dispatch_ack_retries_same_intent_without_double_charge(runtime):
    start(runtime, cost_per_run=0.1, max_cost=0.1, tokens_per_run=100, max_tokens=100)
    runtime[1].dispatch_failures = 1
    assert advance(runtime)["state"]["phase"] == "dispatch_pending"
    assert advance(runtime)["state"]["phase"] == "dispatch_pending"
    assert advance(runtime)["state"]["phase"] == "awaiting_result"
    assert len(runtime[1].receipts) == 1
    state = advance(runtime)["state"]
    assert state["charged_cost"] == 0.1 and state["charged_tokens"] == 100
    assert advance(runtime)["state"]["reason"] == "search_budget"


def test_failed_ideas_are_not_repeated_after_restart(runtime):
    start(runtime)
    runtime[1].fail_quality = True
    for _ in range(15):
        state = advance(runtime)["state"]
        if state["phase"] == "finished":
            break
    assert len(state["failed_ideas"]) == 2
    assert len(set(state["failed_ideas"])) == 2
    assert len(state["population"]) == 1
    assert state["runs"] == 2


@pytest.mark.parametrize("stop", ["kill", "session_deadline", "run_timeout"])
def test_stop_is_persisted_then_containment_is_acknowledged(runtime, stop):
    start(runtime)
    advance(runtime)
    advance(runtime)
    if stop == "kill":
        runtime[3][0] = False
    else:
        runtime[2][0] += 1000 if stop == "session_deadline" else 31
    assert advance(runtime)["state"]["phase"] == "cancel_pending"
    assert advance(runtime)["state"]["phase"] == "finished"
    assert len(runtime[1].cancelled) == 1
    assert runtime[1].submit_calls == 1


def test_failed_containment_returns_bounded_machine_state(runtime):
    start(runtime, max_dispatch_retries=2)
    advance(runtime)
    runtime[1].contained = False
    runtime[3][0] = False
    advance(runtime)
    assert advance(runtime)["state"]["phase"] == "cancel_pending"
    assert advance(runtime)["state"]["phase"] == "containment_failed"
    assert advance(runtime)["state"]["phase"] == "containment_failed"
    assert len(runtime[1].cancelled) == 2


def test_mutated_registered_result_requires_containment_and_never_selects(runtime):
    start(runtime)
    advance(runtime)
    advance(runtime)
    key, receipt = next(iter(runtime[1].results.items()))
    runtime[1].results[key] = replace(receipt, result={**receipt.result, "metrics": {"task_correctness": 1.0}})
    state = advance(runtime)["state"]
    assert state["phase"] == "cancel_pending" and state["reason"] == "unverified_result"
    assert len(state["population"]) == 1
    assert advance(runtime)["state"]["phase"] == "finished"


def test_pending_queue_poll_returns_immediately_without_new_dispatch(runtime):
    start(runtime)
    advance(runtime)
    before = advance(runtime)
    runtime[1].pending = True
    assert advance(runtime) == before
    assert runtime[1].submit_calls == 1


def test_scope_revision_and_immutable_policy_are_enforced(runtime):
    snapshot = start(runtime)
    store = runtime[0]
    with pytest.raises(ValueError, match="harness_search_unknown"):
        store.get(**{**SCOPE, "tenant_id": "other"})
    with pytest.raises(ValueError, match="harness_search_immutable_binding_changed"):
        store.append(**SCOPE, state={**snapshot["state"], "policy": {}}, expected_revision=0)
    advance(runtime)
    with pytest.raises(HarnessSearchConflict):
        store.append(**SCOPE, state=snapshot["state"], expected_revision=0)


def test_two_concurrent_steps_cannot_append_the_same_revision(runtime, tmp_path):
    state = start(runtime)["state"]
    engine = create_engine(f"sqlite:///{tmp_path / 'search-race.sqlite'}")
    SQLModel.metadata.create_all(engine, tables=[HarnessSearchRevisionDB.__table__])
    store = HarnessSearchStore(engine=lambda: engine)
    store.append(**SCOPE, state=state, expected_revision=-1)
    original_get = store.get
    barrier = Barrier(2)

    def concurrent_get(**scope):
        value = original_get(**scope)
        barrier.wait(timeout=3)
        return value

    store.get = concurrent_get

    def append():
        try:
            return store.append(**SCOPE, state=state, expected_revision=0)["revision"]
        except HarnessSearchConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: append(), range(2)))
    assert sorted(results, key=str) == [1, "conflict"]
    assert original_get(**SCOPE)["revision"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"max_runs": 0},
        {"max_cost": float("nan")},
        {"exploration_rate": 1.1},
        {"max_parallel": True},
        {"model_context_limit": 1},
        {"context_budgets": (32768, True)},
    ],
)
def test_invalid_policy_is_rejected_before_search(changes):
    with pytest.raises(ValueError):
        HarnessSearchPolicy(**changes).assert_valid()


def test_hypotheses_respect_actual_model_capability_and_seed():
    spec, _ = manifest()
    generator = HarnessHypothesisGenerator()
    assert not generator.propose(spec.genome, TELEMETRY, context_budgets=(65536, 131072), model_context_limit=32768)
    ideas = generator.propose(spec.genome, TELEMETRY, context_budgets=(65536, 131072), model_context_limit=131072)
    assert generator.choose(ideas, seed=7, attempt=2, exploration_rate=1) == generator.choose(
        ideas, seed=7, attempt=2, exploration_rate=1
    )
