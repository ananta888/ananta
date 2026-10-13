"""Closed Gate/Benchmark → Dataset → ML-Intern → LoRA/QLoRA → Eval → Promotion loop (GBMF-011).

Hub-side orchestration only. Training and inference happen behind two small
ports (``SpecialistTrainerPort``, ``SpecialistPredictorPort``). Production
composition must connect these ports to Hub-owned isolated worker jobs;
the loop itself never touches trainer CLIs.
Registry entries, approval and rollback use the existing ML-Intern adapter
registry, so a specialist version is a normal versioned adapter record.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from agent.services.ml_intern_adapter_registry_service import MlInternAdapterRegistryService
from agent.services.ml_intern_training_contract import CreateTrainingJobCommand
from agent.services.specialist_benchmark import (
    BenchmarkResult,
    Prediction,
    ResourceUsage,
    benchmark,
    predictions_from_outputs,
)
from agent.services.specialist_calibration import SpecialistCalibrationError, calibration_report
from agent.services.specialist_candidates import (
    ArtifactLineage,
    CandidatePlan,
    CandidateRanking,
    QualityThresholds,
    compare_candidates,
)
from agent.services.specialist_dataset_split import (
    FrozenHoldout,
    SplitManifest,
    admit_training_partitions,
    split_examples,
)
from agent.services.specialist_evaluation_receipt import SpecialistEvaluationReceipt, SpecialistPartitionOutputs
from agent.services.specialist_promotion_gate import PromotionDecision, SpecialistPromotionPolicy, decide_promotion
from agent.services.specialist_training_examples import TrainingExample
from agent.services.specialist_training_task_family import SpecialistTrainingTaskFamilyStrategy
from ananta_contracts.specialist_dataset_admission import AdmittedSpecialistDataset
from ananta_contracts.specialist_decision import SpecialistDecisionContract

APPROVER = "specialist-promotion-gate"


@dataclass(frozen=True)
class TrainingOutcome:
    job_id: str
    adapter_digest: str
    base_model_digest: str
    usage: ResourceUsage
    adapter_id: str | None = None


class SpecialistTrainerPort(Protocol):
    def train(self, job: CreateTrainingJobCommand, records: Sequence[Mapping[str, Any]]) -> TrainingOutcome: ...


class SpecialistPredictorPort(Protocol):
    def predict(self, adapter_digest: str, instructions: Sequence[str]) -> Sequence[str]: ...


class SpecialistPartitionPredictorPort(Protocol):
    def predict_partition(
        self, training: CreateTrainingJobCommand, outcome: TrainingOutcome, manifest: SplitManifest,
        train_records: Sequence[Mapping[str, Any]], examples: Sequence[TrainingExample], *, partition: str,
    ) -> Sequence[str]: ...


class SpecialistDatasetAdmissionPort(Protocol):
    def admit(self, manifest: SplitManifest, train: Sequence[Mapping[str, Any]],
              validation: Sequence[Mapping[str, Any]]) -> AdmittedSpecialistDataset: ...


@dataclass(frozen=True)
class CandidateOutcome:
    candidate_id: str
    job: CreateTrainingJobCommand
    outcome: TrainingOutcome
    result: BenchmarkResult
    lineage: ArtifactLineage
    evaluations: tuple[tuple[str, SpecialistEvaluationReceipt], ...] = ()
    split_manifest_digest: str | None = None


@dataclass(frozen=True)
class FactoryCycle:
    version: str
    manifest: SplitManifest
    admission: Mapping[str, Any]
    candidates: tuple[CandidateOutcome, ...]
    ranking: CandidateRanking
    winner: CandidateOutcome | None
    decision: PromotionDecision | None
    adapter_id: str | None
    registry_status: str | None
    errors: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "manifest_digest": self.manifest.manifest_digest,
            "holdout_digest": self.manifest.holdout.holdout_digest,
            "admission": dict(self.admission),
            "ranking": self.ranking.to_mapping(),
            "winner": self.winner.candidate_id if self.winner else None,
            "decision": self.decision.to_mapping() if self.decision else None,
            "adapter_id": self.adapter_id,
            "registry_status": self.registry_status,
            "evaluations": {
                candidate.candidate_id: {role: asdict(receipt) for role, receipt in candidate.evaluations}
                for candidate in self.candidates if candidate.evaluations
            },
            "errors": {key: list(value) for key, value in self.errors.items()},
        }


class SpecialistCandidateRegistrationPort(Protocol):
    def register(self, winner: CandidateOutcome, decision: PromotionDecision) -> tuple[str, str]: ...


class SpecialistLifecyclePort(Protocol):
    def active_version(self, base_model: str, *, task_kind: str) -> str | None: ...
    def rollback(self, adapter_id: str, *, task_kind: str) -> str | None: ...


class SpecialistModelFactory:
    def __init__(
        self,
        contract: SpecialistDecisionContract,
        *,
        trainer: SpecialistTrainerPort,
        predictor: SpecialistPredictorPort | None = None,
        registry: MlInternAdapterRegistryService,
        policy: SpecialistPromotionPolicy,
        benchmark_version: str,
        backend: str = "mock",
        mode: str = "dry_run",
        dataset_id_prefix: str = "specialist",
        dataset_admission: SpecialistDatasetAdmissionPort | None = None,
        partition_predictor: SpecialistPartitionPredictorPort | None = None,
        candidate_registration: SpecialistCandidateRegistrationPort | None = None,
        lifecycle: SpecialistLifecyclePort | None = None,
    ) -> None:
        self.contract = contract
        self.strategy = SpecialistTrainingTaskFamilyStrategy(contract)
        self._trainer = trainer
        self._predictor = predictor
        self._partition_predictor = partition_predictor
        if predictor is None and partition_predictor is None:
            raise ValueError("specialist_predictor_required")
        self._registry = registry
        self._policy = policy
        self._benchmark_version = benchmark_version
        self._backend = backend
        self._mode = mode
        self._dataset_id_prefix = dataset_id_prefix
        self._dataset_admission = dataset_admission
        self._candidate_registration = candidate_registration
        self._lifecycle = lifecycle

    # -- helpers ------------------------------------------------------------

    @property
    def task_kind(self) -> str:
        return f"specialist:{self.contract.specialist_id}"

    def adapter_id(self, version: str) -> str:
        return f"specialist-{self.contract.specialist_id}-{version}"

    def _predict(self, adapter_digest: str, examples: Sequence[TrainingExample]) -> list[Prediction]:
        if self._predictor is None:
            raise ValueError("specialist_predictor_required")
        instructions = [self.strategy.instruction(example.input) for example in examples]
        outputs = list(self._predictor.predict(adapter_digest, instructions))
        if len(outputs) != len(examples):
            raise ValueError("specialist_predictor_output_count_mismatch")
        return predictions_from_outputs(
            self.contract, [(e.example_id, e.label, raw) for e, raw in zip(examples, outputs)]
        )

    def _predict_partition(
        self, job: CreateTrainingJobCommand, outcome: TrainingOutcome, manifest: SplitManifest,
        train_records: Sequence[Mapping[str, Any]], examples: Sequence[TrainingExample], *, partition: str,
    ) -> tuple[list[Prediction], SpecialistEvaluationReceipt | None]:
        if self._partition_predictor is None:
            return self._predict(outcome.adapter_digest, examples), None
        batch = self._partition_predictor.predict_partition(
            job, outcome, manifest, train_records, examples, partition=partition,
        )
        outputs = list(batch)
        if len(outputs) != len(examples):
            raise ValueError("specialist_predictor_output_count_mismatch")
        predictions = predictions_from_outputs(
            self.contract, [(e.example_id, e.label, raw) for e, raw in zip(examples, outputs)]
        )
        return predictions, batch.receipt if isinstance(batch, SpecialistPartitionOutputs) else None

    def _calibration(self, validation: Sequence[Prediction], holdout: Sequence[Prediction]) -> Mapping[str, Any] | None:
        if not self.contract.confidence.enabled:
            return None
        limit = self._policy.thresholds.max_false_allow_rate
        try:
            return calibration_report(validation, holdout, max_error_above=limit if limit is not None else 0.1)
        except SpecialistCalibrationError:
            return None

    # -- one cycle ----------------------------------------------------------

    def run_cycle(
        self,
        examples: Iterable[TrainingExample],
        *,
        version: str,
        plan_candidates: CandidatePlan | None,
        seed: int,
        previous: CandidateOutcome | None = None,
        baseline_predictions: Sequence[Prediction] | None = None,
        thresholds: QualityThresholds | None = None,
        frozen_holdout: FrozenHoldout | None = None,
    ) -> FactoryCycle:
        """One cycle; pass the previous cycle's ``manifest.holdout`` to keep the benchmark frozen."""
        thresholds = thresholds if thresholds is not None else self._policy.thresholds
        if frozen_holdout is None and previous is not None:
            raise ValueError("specialist_previous_requires_frozen_holdout")
        if previous is not None:
            if frozen_holdout.holdout_digest != previous.result.holdout_digest:
                raise ValueError("specialist_previous_holdout_mismatch")
            if previous.result.benchmark_version != self._benchmark_version:
                raise ValueError("specialist_previous_benchmark_mismatch")
            if (previous.lineage.specialist_id != self.contract.specialist_id
                    or previous.lineage.contract_version != self.contract.contract_version):
                raise ValueError("specialist_previous_contract_mismatch")
        manifest, partitions = split_examples(list(examples), seed=seed, frozen_holdout=frozen_holdout)
        admission = admit_training_partitions(manifest, partitions, expected_holdout_digest=manifest.holdout.holdout_digest)
        if plan_candidates is None:
            raise ValueError("specialist_candidate_plan_required")
        if (plan_candidates.specialist_id != self.contract.specialist_id
                or plan_candidates.benchmark_version != self._benchmark_version):
            raise ValueError("specialist_candidate_plan_contract_mismatch")
        if plan_candidates.dataset_digest != manifest.dataset_digest or plan_candidates.holdout_digest != manifest.holdout.holdout_digest:
            raise ValueError("specialist_candidate_plan_mismatch")
        records = self.strategy.training_records(partitions["train"])
        dataset_id = f"{self._dataset_id_prefix}-{manifest.dataset_digest[:16]}"
        dataset_extra = {}
        if self._dataset_admission is not None:
            admitted_dataset = self._dataset_admission.admit(
                manifest, records, self.strategy.training_records(partitions["validation"])
            )
            if not isinstance(admitted_dataset, AdmittedSpecialistDataset):
                raise ValueError("specialist_dataset_admission_id_invalid")
            dataset_id = admitted_dataset.dataset_id
            dataset_extra = {"expected_dataset_version": admitted_dataset.version}
        baseline = None
        if baseline_predictions is not None:
            baseline = benchmark(
                self.contract, list(baseline_predictions),
                benchmark_version=self._benchmark_version, holdout_digest=manifest.holdout.holdout_digest,
                candidate_id="prompt_gate_stack", base_model="none", method="lora",
            )
        outcomes: list[CandidateOutcome] = []
        errors: dict[str, tuple[str, ...]] = {}
        for candidate in plan_candidates.candidates:
            job_request = self.strategy.job_request(
                dataset_id=dataset_id,
                base_model=candidate.base_model,
                method=candidate.method,
                backend=self._backend,
                manifest=manifest,
                benchmark_version=self._benchmark_version,
                mode=self._mode,
                hyperparameters=plan_candidates.hyperparameters(),
                output_name=self.adapter_id(version),
                extra=dataset_extra,
            )
            job = CreateTrainingJobCommand.from_mapping(job_request)  # existing ML-Intern contract validates
            outcome = self._trainer.train(job, records)
            if outcome.adapter_id is not None and self._candidate_registration is None:
                raise ValueError("specialist_runtime_candidate_registration_required")
            validation, validation_receipt = self._predict_partition(
                job, outcome, manifest, records, partitions["validation"], partition="validation"
            )
            holdout, holdout_receipt = self._predict_partition(
                job, outcome, manifest, records, partitions["holdout"], partition="holdout"
            )
            baselines = {}
            if baseline is not None:
                baselines["prompt_gate_stack"] = baseline
            if previous is not None and previous.result.holdout_digest == manifest.holdout.holdout_digest:
                baselines["previous_specialist"] = previous.result
            result = benchmark(
                self.contract, holdout,
                benchmark_version=self._benchmark_version, holdout_digest=manifest.holdout.holdout_digest,
                candidate_id=candidate.candidate_id, base_model=candidate.base_model, method=candidate.method,
                usage=outcome.usage, calibration=self._calibration(validation, holdout), baselines=baselines,
            )
            lineage = ArtifactLineage(
                specialist_id=self.contract.specialist_id,
                contract_version=self.contract.contract_version,
                version=version,
                base_model=candidate.base_model,
                base_model_digest=outcome.base_model_digest,
                adapter_digest=outcome.adapter_digest,
                dataset_digest=manifest.dataset_digest,
                holdout_digest=manifest.holdout.holdout_digest,
                training_job_id=outcome.job_id,
                method=candidate.method,
            ).with_evaluation(result)
            receipts = tuple((role, receipt) for role, receipt in (
                ("validation", validation_receipt), ("holdout", holdout_receipt)
            ) if receipt is not None)
            outcomes.append(CandidateOutcome(
                candidate.candidate_id, job, outcome, result, lineage, receipts, manifest.manifest_digest
            ))
        ranking = compare_candidates(plan_candidates, [o.result for o in outcomes], thresholds)
        winner = next((o for o in outcomes if o.candidate_id == ranking.winner), None)
        decision = adapter_id = status = None
        if winner is not None:
            previous_result = previous.result if previous is not None and previous.result.holdout_digest == manifest.holdout.holdout_digest else None
            decision = decide_promotion(
                candidate=winner.result,
                lineage=winner.lineage,
                policy=self._policy,
                previous=previous_result,
                previous_version=previous.lineage.version if previous is not None else None,
                baseline=baseline,
            )
            adapter_id, status = self._register(winner, decision)
            if decision.allowed and status != "approved":
                errors[winner.candidate_id] = ("specialist_production_promotion_pending",)
        else:
            errors = {cid: tuple(problems) for cid, problems in ranking.violations.items() if problems}
        return FactoryCycle(version, manifest, admission, tuple(outcomes), ranking, winner, decision, adapter_id, status, errors)

    # -- registry -----------------------------------------------------------

    def _register(self, winner: CandidateOutcome, decision: PromotionDecision) -> tuple[str, str]:
        """Versioned registry entry via the existing lifecycle; approval only through the gate."""
        if winner.outcome.adapter_id is not None:
            if self._candidate_registration is None:
                raise ValueError("specialist_runtime_candidate_registration_required")
            return self._candidate_registration.register(winner, decision)
        adapter_id = self.adapter_id(winner.lineage.version)
        self._registry.register(
            adapter_id=adapter_id,
            display_name=f"{self.contract.specialist_id} {winner.lineage.version}",
            version=winner.lineage.version,
            base_model=winner.lineage.base_model,
            method=winner.lineage.method,
            dataset_hash=winner.lineage.dataset_digest,
            artifact_sha256=winner.lineage.adapter_digest,
            config_hash=winner.lineage.lineage_digest,
            task_kinds=[self.task_kind],
            notes=f"contract={self.contract.contract_key} benchmark={self._benchmark_version} job={winner.lineage.training_job_id}",
        )
        self._registry.transition(adapter_id, "training")
        self._registry.transition(adapter_id, "trained")
        self._registry.set_eval_report(
            adapter_id,
            eval_report_ref=winner.result.to_mapping()["result_digest"],
            eval_score=float(winner.result.classification["macro_f1"]),
        )
        if decision.allowed:
            record = self._registry.approve(
                adapter_id,
                approved_by=APPROVER,
                reason=f"{decision.improvement} improvement; decision {decision.to_mapping()['decision_digest'][:16]}",
                minimum_eval_score=self._policy.thresholds.min_macro_f1,
            )
        else:
            record = self._registry.reject(adapter_id, reason=",".join(decision.reason_codes))
        return adapter_id, record.status

    def active_version(self, base_model: str) -> str | None:
        if self._lifecycle is not None:
            return self._lifecycle.active_version(base_model, task_kind=self.task_kind)
        if self._candidate_registration is not None:
            raise ValueError("specialist_scoped_lifecycle_required")
        record = self._registry.resolve_active_adapter(base_model=base_model, task_kind=self.task_kind)
        return record.version if record is not None else None

    def rollback(self, adapter_id: str) -> str | None:
        """Deprecate the active specialist; the previous approved version becomes active."""
        if self._lifecycle is not None:
            return self._lifecycle.rollback(adapter_id, task_kind=self.task_kind)
        if self._candidate_registration is not None:
            raise ValueError("specialist_scoped_lifecycle_required")
        _deprecated, target = self._registry.rollback(adapter_id)
        return target.adapter_id if target is not None else None
