"""Compare local providers using the existing Specialist benchmark/split system."""

from __future__ import annotations

import math
import platform
import time
from collections.abc import Mapping

from agent.services.decision_providers.benchmark_corpus import VERSION, contract, corpus, question
from agent.services.decision_providers.calibration import CalibrationSample, fit_temperature
from agent.services.decision_providers.evaluation import Run, provider_report
from agent.services.decision_providers.types import DecisionProviderError, DecisionRequest
from agent.services.specialist_benchmark import Prediction, classification_metrics, safety_metrics
from agent.services.tiny_router.decision_calibration import Observation, threshold_table


def run_pilot(providers: Mapping, *, timeout_seconds: float = 20) -> dict:
    if not providers or len(providers) > 8 or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60:
        raise ValueError("pilot_config_invalid")
    reports = {}
    for task in ("tool_routing", "retrieval_relevance", "priority"):
        manifest, partitions = corpus(task)
        for provider_name, provider in providers.items():
            for language in ("de", "en"):
                split_runs = {}
                ordinal = []
                model = None
                calibration_samples = []
                for split in ("validation", "holdout"):
                    runs = []
                    for example in partitions[split]:
                        if example.input["language"] != language:
                            continue
                        request = DecisionRequest(
                            example.input["text"],
                            (question(task, language),),
                            purpose=task,
                            tenant_id="synthetic-benchmark",
                            subject_id="pilot",
                        )
                        started = time.monotonic()
                        try:
                            result = provider.decide(request, timeout_seconds=timeout_seconds)
                            answer = result.answers[task]
                            if answer.kind == "score":
                                predicted = request.questions[0].labels[answer.level]
                                ordinal.append(abs(answer.score - request.questions[0].labels.index(example.label)))
                            else:
                                predicted = answer.choice
                            predicted = contract(task).validate_label(predicted)
                            model = result.model
                            run = Run(
                                example.example_id,
                                example.label,
                                predicted,
                                answer.confidence,
                                latency_ms=result.latency_ms,
                                input_tokens=result.usage.input_tokens,
                            )
                            if split == "validation" and set(answer.probabilities) == set(request.questions[0].labels):
                                labels = request.questions[0].labels
                                calibration_samples.append(
                                    CalibrationSample(
                                        example.example_id,
                                        split,
                                        tuple(answer.probabilities[label] for label in labels),
                                        labels.index(example.label),
                                    )
                                )
                        except DecisionProviderError as error:
                            run = Run(
                                example.example_id,
                                example.label,
                                None,
                                0,
                                latency_ms=(time.monotonic() - started) * 1000,
                                error=error.reason_code,
                            )
                        runs.append(run)
                    split_runs[split] = runs
                # Threshold selection has access ONLY to validation rows. Its
                # Wilson bound prevents perfect results on 2 examples granting
                # an active profile. Raw confidence remains explicitly uncalibrated.
                candidates = threshold_table(
                    [
                        Observation(r.case_id, r.expected, r.predicted or "<error>", r.confidence)
                        for r in split_runs["validation"]
                    ],
                    (0.5, 0.7, 0.8, 0.9, 0.95, 0.99),
                )
                eligible = [r for r in candidates if r.accepted >= 20 and r.precision_lower_bound >= 0.95]
                selected = min((r.threshold for r in eligible), default=None)
                holdout = split_runs["holdout"]
                predictions = [
                    Prediction(r.case_id, r.expected, r.predicted, r.confidence, r.latency_ms, r.input_tokens)
                    for r in holdout
                ]
                reports[f"{task}/{language}/{provider_name}"] = {
                    "contract_digest": contract(task).digest,
                    "holdout_digest": manifest.holdout.holdout_digest,
                    "model": model,
                    "classification": classification_metrics(contract(task), predictions),
                    "safety": safety_metrics(contract(task), predictions),
                    "validation": _split_report(split_runs["validation"]),
                    "holdout": _split_report(holdout),
                    "ordinal_mae": sum(ordinal) / len(ordinal) if ordinal else None,
                    "threshold": selected,
                    "threshold_selection_split": "validation",
                    "calibration": fit_temperature(tuple(calibration_samples))
                    if calibration_samples
                    else {"status": "not_run"},
                    "decision": "shadow",
                    "reason": "Pilot size/evidence do not authorize promotion.",
                    "rows": {split: [r.__dict__ for r in rows] for split, rows in split_runs.items()},
                }
    return {
        "schema": "ananta.specialist-pilot-comparison.v1",
        "dataset_version": VERSION,
        "classification": "synthetic_technical_observation",
        "production_release_evidence": False,
        "hardware": {"platform": platform.platform(), "processor": platform.processor()},
        "reports": reports,
    }


def _split_report(rows):
    result = provider_report(rows, calibrated=False)
    result.pop("split", None)  # use the existing Specialist GROUP split, not the generic case-ID hash split
    return result
