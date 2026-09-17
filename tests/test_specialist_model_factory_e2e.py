"""GBMF-011: Gate/Benchmark -> Dataset -> ML-Intern job -> LoRA/QLoRA -> Eval -> Promotion -> Runtime.

The trainer and predictor are deterministic doubles behind the factory's
ports (the Hub delegates real training to isolated workers); the registry is
the real file-based ML-Intern adapter registry.
"""

import hashlib
import json

import pytest

from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.ml_intern_training_contract import CreateTrainingJobCommand
from agent.services.specialist_benchmark import Prediction, ResourceUsage
from agent.services.specialist_candidates import BaseModelCandidate, CandidatePlan, QualityThresholds, TrainingBudget
from agent.services.specialist_dataset_split import SpecialistSplitError, split_examples
from agent.services.specialist_model_factory import SpecialistModelFactory, TrainingOutcome
from agent.services.specialist_promotion_gate import SpecialistPromotionPolicy
from agent.services.specialist_retraining_queue import RetrainingCandidate, RetrainingCandidateQueue
from agent.services.specialist_training_examples import SpecialistExampleAdmission
from ananta_contracts.specialist_decision import OUTPUT_SCHEMA, builtin_contracts
from tests.test_specialist_model_factory import GATE, raw_example

pytestmark = pytest.mark.timeout(120)
TINY, MID = "local/tiny-0.5b", "local/mid-3b"


def _rule(text):
    return "deny" if any(word in text.lower() for word in ("delete", "drop")) else "allow"


class LookupTrainer:
    """Deterministic stand-in for a LoRA/QLoRA worker and its inference route.

    A "model" memorizes rule -> label from the training records. ``behaviour``
    per base model may ``forget`` labels (answering ``fallback`` instead) and
    sets the adapter size, so candidates differ in quality and cost.
    """

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.jobs = []
        self.models = {}

    def train(self, job, records):
        assert isinstance(job, CreateTrainingJobCommand) and job.request_spec["task_family"] == "specialist_decision"
        self.jobs.append(job)
        rules = {}
        for record in records:
            text = json.loads(record["instruction"])["input"]["diff_summary"]
            rules[_rule(text)] = json.loads(record["output"])["label"]
        behaviour = self.behaviour[job.base_model]
        digest = hashlib.sha256(json.dumps([job.base_model, sorted(rules.items()), sorted(behaviour.items())], sort_keys=True, default=sorted).encode()).hexdigest()
        self.models[digest] = (rules, behaviour)
        return TrainingOutcome(
            job_id=f"job-{len(self.jobs)}",
            adapter_digest=digest,
            base_model_digest=hashlib.sha256(job.base_model.encode()).hexdigest(),
            usage=ResourceUsage(train_seconds=5.0, train_steps=50, peak_vram_bytes=1_000, parameter_count=behaviour.get("parameters", 1_000), adapter_bytes=behaviour.get("adapter_bytes", 100)),
        )

    def predict(self, adapter_digest, instructions):
        rules, behaviour = self.models[adapter_digest]
        outputs = []
        for instruction in instructions:
            label = rules.get(_rule(json.loads(instruction)["input"]["diff_summary"]))
            if label is None or label in behaviour.get("forget", ()):
                label = behaviour.get("fallback", "escalate")
            outputs.append(json.dumps({"schema": OUTPUT_SCHEMA, "label": label, "confidence": 0.5 if label == "escalate" else 0.9}))
        return outputs


def gate_examples(count, *, offset=0):
    raws = []
    for i in range(count):
        risky = i % 3 == 0
        text = f"drop table users variant {offset + i}" if risky else f"refactor helper {offset + i} without behaviour change"
        raws.append(raw_example(offset + i, "deny" if risky else "allow", text=text))
    report = SpecialistExampleAdmission(GATE).admit_batch(raws)
    assert not report.rejected and not report.quarantined
    return report.admitted


POLICY = SpecialistPromotionPolicy(
    thresholds=QualityThresholds(min_accuracy=0.9, min_macro_f1=0.85, max_false_allow_rate=0.0, max_false_deny_rate=0.2),
    max_expected_calibration_error=0.5,
)
BUDGET = TrainingBudget(max_steps=50, max_seconds=600, max_vram_bytes=8_000_000_000)


def plan_for(examples, seed, *, frozen_holdout=None):
    manifest, _ = split_examples(examples, seed=seed, frozen_holdout=frozen_holdout)
    return CandidatePlan(
        specialist_id="code-risk-gate",
        dataset_digest=manifest.dataset_digest,
        holdout_digest=manifest.holdout.holdout_digest,
        benchmark_version="code-risk-bench-v1",
        budget=BUDGET,
        candidates=(BaseModelCandidate(TINY, "lora", 500_000_000), BaseModelCandidate(MID, "qlora", 3_000_000_000)),
    )


def factory_for(trainer, registry):
    return SpecialistModelFactory(GATE, trainer=trainer, predictor=trainer, registry=registry, policy=POLICY, benchmark_version="code-risk-bench-v1")


def test_full_loop_promotes_v1_and_v2_only_when_it_beats_v1_on_the_frozen_holdout(tmp_path):
    registry = MlInternAdapterRegistryService(tmp_path / "adapter_registry.json")
    examples = gate_examples(45)
    trainer = LookupTrainer({TINY: {"adapter_bytes": 300}, MID: {"adapter_bytes": 900}})
    factory = factory_for(trainer, registry)
    holdout_cases = split_examples(examples, seed=11)[1]["holdout"]
    prompt_stack = [Prediction(e.example_id, e.label, "allow", 0.5) for e in holdout_cases]
    cycle1 = factory.run_cycle(examples, version="v1", plan_candidates=plan_for(examples, 11), seed=11, baseline_predictions=prompt_stack)

    # Gate/Benchmark -> Dataset -> ML-Intern job (validated by the existing contract)
    assert cycle1.admission["admitted"] is True and cycle1.admission["leakage"]["leaked"] is False
    assert [job.request_spec["specialist"]["dataset_digest"] for job in trainer.jobs] == [cycle1.manifest.dataset_digest] * 2
    assert {job.request_spec["method"] for job in trainer.jobs} == {"lora", "qlora"} and {job.backend for job in trainer.jobs} == {"mock"}
    # -> LoRA/QLoRA -> Eval: both perfect, the smaller adapter wins on cost
    assert cycle1.ranking.winner == f"{TINY}:lora" and cycle1.winner.result.classification["accuracy"] == 1.0
    assert cycle1.winner.result.baselines["prompt_gate_stack"]["false_allow_rate_delta"] < 0
    assert cycle1.winner.result.calibration["schema"] == "ananta.specialist-calibration.v1"
    # -> Promotion -> Runtime (existing registry lifecycle, approval only by the gate)
    assert cycle1.decision.allowed and cycle1.registry_status == "approved" and cycle1.adapter_id == "specialist-code-risk-gate-v1"
    record = registry.get(cycle1.adapter_id)
    assert record.task_kinds == ["specialist:code-risk-gate"] and record.approved_by == "specialist-promotion-gate"
    assert record.dataset_hash == cycle1.manifest.dataset_digest and record.artifact_sha256 == cycle1.winner.lineage.adapter_digest
    assert record.eval_report_ref == cycle1.winner.result.to_mapping()["result_digest"]
    assert factory.active_version(TINY) == "v1"
    assert json.loads(json.dumps(cycle1.to_mapping()))["decision"]["improvement"] == "quality"

    # Second cycle from verified error cases keeps the frozen holdout.
    queue = RetrainingCandidateQueue(SpecialistExampleAdmission(GATE))
    for index in range(6):
        queue.enqueue(RetrainingCandidate(raw=raw_example(900 + index, "deny", text=f"DELETE FROM audit_log {index}", model_label="allow"), error_class="false_allow", verified=True))
    version2, examples_v2, hard_negatives = queue.curate(current=examples, holdout=cycle1.manifest.holdout, version=2)
    assert version2.added_error_classes == {"false_allow": 6} and len(hard_negatives) == 6
    plan2 = plan_for(examples_v2, 11, frozen_holdout=cycle1.manifest.holdout)
    assert plan2.holdout_digest == cycle1.manifest.holdout.holdout_digest and plan2.dataset_digest != cycle1.manifest.dataset_digest

    # v2 candidates are equal in quality but no smaller or faster -> not promoted.
    same = LookupTrainer({TINY: {"adapter_bytes": 300}, MID: {"adapter_bytes": 900}})
    cycle2 = factory_for(same, registry).run_cycle(
        examples_v2, version="v2", plan_candidates=plan2, seed=11, previous=cycle1.winner, frozen_holdout=cycle1.manifest.holdout
    )
    assert cycle2.manifest.holdout.holdout_digest == cycle1.manifest.holdout.holdout_digest
    assert cycle2.winner.result.baselines["previous_specialist"]["accuracy_delta"] == 0.0
    assert cycle2.decision.allowed is False and cycle2.decision.reason_codes == ("no_measurable_improvement",)
    assert cycle2.decision.rollback_target == "v1" and cycle2.registry_status == "rejected"
    assert registry.resolve_active_adapter(base_model=TINY, task_kind="specialist:code-risk-gate").version == "v1"

    # v3: same quality, measurably smaller adapter -> promoted; rollback restores v1.
    smaller = LookupTrainer({TINY: {"adapter_bytes": 120}, MID: {"adapter_bytes": 900}})
    cycle3 = factory_for(smaller, registry).run_cycle(
        examples_v2, version="v3", plan_candidates=plan2, seed=11, previous=cycle1.winner, frozen_holdout=cycle1.manifest.holdout
    )
    assert cycle3.decision.allowed and cycle3.decision.improvement == "efficiency" and cycle3.registry_status == "approved"
    assert registry.resolve_active_adapter(base_model=TINY, task_kind="specialist:code-risk-gate").version == "v3"
    assert factory.rollback("specialist-code-risk-gate-v3") == "specialist-code-risk-gate-v1"
    assert registry.resolve_active_adapter(base_model=TINY, task_kind="specialist:code-risk-gate").version == "v1"


def test_regressed_v2_is_rejected_and_v1_stays_active(tmp_path):
    registry = MlInternAdapterRegistryService(tmp_path / "adapter_registry.json")
    examples = gate_examples(45)
    cycle1 = factory_for(LookupTrainer({TINY: {}, MID: {}}), registry).run_cycle(examples, version="v1", plan_candidates=plan_for(examples, 5), seed=5)
    assert cycle1.registry_status == "approved"
    # Both v2 candidates forget "deny" -> false allows -> hard minimums fail -> nothing registered.
    forgetful = LookupTrainer({TINY: {"forget": {"deny"}, "fallback": "allow"}, MID: {"forget": {"deny"}, "fallback": "allow"}})
    cycle2 = factory_for(forgetful, registry).run_cycle(
        examples, version="v2", plan_candidates=plan_for(examples, 5), seed=5, previous=cycle1.winner, frozen_holdout=cycle1.manifest.holdout
    )
    assert cycle2.winner is None and cycle2.adapter_id is None and cycle2.decision is None
    assert all("false_allow_rate_above_maximum" in problems for problems in cycle2.errors.values()) and len(cycle2.errors) == 2
    assert [record.adapter_id for record in registry.list_adapters()] == ["specialist-code-risk-gate-v1"]
    assert registry.resolve_active_adapter(base_model=TINY, task_kind="specialist:code-risk-gate").version == "v1"
    # A mid-quality v2 (escalates on deny) passes minimums? No: false-deny/accuracy limits reject it before registry approval.
    cautious = LookupTrainer({TINY: {"forget": {"deny"}, "fallback": "escalate"}, MID: {"forget": {"deny"}, "fallback": "escalate"}})
    cycle3 = factory_for(cautious, registry).run_cycle(
        examples, version="v3", plan_candidates=plan_for(examples, 5), seed=5, previous=cycle1.winner, frozen_holdout=cycle1.manifest.holdout
    )
    assert cycle3.winner is None and "accuracy_below_minimum" in cycle3.errors[f"{TINY}:lora"]


def test_frozen_holdout_cannot_be_contaminated_or_replaced(tmp_path):
    examples = gate_examples(45)
    cycle1 = factory_for(LookupTrainer({TINY: {}, MID: {}}), MlInternAdapterRegistryService(tmp_path / "r.json")).run_cycle(
        examples, version="v1", plan_candidates=plan_for(examples, 2), seed=2
    )
    holdout = cycle1.manifest.holdout
    leaking = split_examples(examples, seed=2)[1]["holdout"][0]
    variant = SpecialistExampleAdmission(GATE).admit({**raw_example(777, leaking.label), "input": {**leaking.input, "diff_summary": leaking.input["diff_summary"].upper()}})
    with pytest.raises(SpecialistSplitError, match="specialist_split_leakage"):
        split_examples(examples + [variant], seed=2, frozen_holdout=holdout)
    with pytest.raises(SpecialistSplitError, match="specialist_holdout_frozen_incomplete"):
        split_examples([e for e in examples if e.example_id != leaking.example_id], seed=2, frozen_holdout=holdout)
    with pytest.raises(ValueError, match="specialist_previous_requires_frozen_holdout"):
        factory_for(LookupTrainer({TINY: {}, MID: {}}), MlInternAdapterRegistryService(tmp_path / "r2.json")).run_cycle(
            examples, version="v2", plan_candidates=plan_for(examples, 2), seed=2, previous=cycle1.winner
        )


def test_every_builtin_specialist_uses_the_same_factory_without_bespoke_code(tmp_path):
    for name, contract in builtin_contracts().items():
        factory = SpecialistModelFactory(
            contract, trainer=LookupTrainer({}), predictor=LookupTrainer({}),
            registry=MlInternAdapterRegistryService(tmp_path / f"{name}.json"), policy=POLICY, benchmark_version="b",
        )
        assert factory.task_kind == f"specialist:{name}" and factory.adapter_id("v1") == f"specialist-{name}-v1"
        assert factory.strategy.job_request(dataset_id="d", base_model="m", method="lora", backend="mock", manifest=_manifest_stub(), benchmark_version="b")["specialist"]["specialist_id"] == name


def _manifest_stub():
    examples = gate_examples(12, offset=300)
    return split_examples(examples, seed=1)[0]
