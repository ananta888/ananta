"""Bounded external intent evaluation with validation-only profile selection."""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, replace

from agent.services.decision_providers.calibration import CalibrationSample, fit_temperature, scale_probabilities
from agent.services.decision_providers.evaluation import Run, provider_report
from agent.services.decision_providers.types import DecisionProviderError, DecisionQuestion, DecisionRequest
from agent.services.public_decision_datasets import INTENTS, MASSIVE_REVISION
from agent.services.tiny_router.decision_calibration import threshold_table


def validate_corpus(corpus):
    if (
        corpus.get("schema") != "ananta.massive-intent-freeze.v1"
        or corpus.get("source_revision") != MASSIVE_REVISION
        or corpus.get("declared_license") != "CC-BY-4.0"
        or corpus.get("labels") != list(INTENTS)
    ):
        raise ValueError("public_intent_source_binding_invalid")
    records = corpus["records"]
    if not 40 <= len(records) <= 1600:
        raise ValueError("public_intent_corpus_budget")
    identities, texts, groups, pairs = set(), set(), {}, {}
    for row in records:
        key = (row["group"], row["language"])
        if (
            row["split"] not in {"validation", "holdout"}
            or row["language"] not in {"de", "en"}
            or row["label"] not in INTENTS
            or key in identities
            or not isinstance(row["text"], str)
            or not 1 <= len(row["text"]) <= 2000
        ):
            raise ValueError("public_intent_record_invalid")
        identities.add(key)
        digest = hashlib.sha256(" ".join(row["text"].casefold().split()).encode()).hexdigest()
        if digest != row["content_sha256"] or digest in texts:
            raise ValueError("public_intent_content_binding_invalid")
        texts.add(digest)
        if groups.setdefault(row["group"], (row["split"], row["label"])) != (row["split"], row["label"]):
            raise ValueError("public_intent_group_overlap")
        pairs.setdefault(row["group"], set()).add(row["language"])
    if any(languages != {"de", "en"} for languages in pairs.values()):
        raise ValueError("public_intent_translation_missing")
    for split in ("validation", "holdout"):
        if {row["label"] for row in records if row["split"] == split} != set(INTENTS):
            raise ValueError("public_intent_label_coverage_missing")
    return records


def run_public_intent(provider, corpus, *, max_seconds=900, clock=time.monotonic):
    records = validate_corpus(corpus)
    if type(max_seconds) not in {int, float} or not math.isfinite(max_seconds) or not 0 < max_seconds <= 3600:
        raise ValueError("public_intent_deadline_invalid")
    deadline = clock() + max_seconds
    labels = tuple(INTENTS)
    rows, models = [], set()
    for row in records:
        remaining = deadline - clock()
        if remaining <= 0:
            raise ValueError("public_intent_deadline_exceeded")
        language = row["language"]
        question = DecisionQuestion.choice(
            "intent",
            "Bestimme die Absicht der Äußerung." if language == "de" else "Identify the intent of the utterance.",
            {label: INTENTS[label][language == "en"] for label in labels},
        )
        start = clock()
        probabilities = ()
        try:
            result = provider.decide(
                DecisionRequest(
                    row["text"],
                    (question,),
                    purpose="public_intent_benchmark",
                    tenant_id="public-dataset-evaluation",
                    subject_id="massive",
                ),
                timeout_seconds=min(30, remaining),
            )
            answer = result.answers["intent"]
            models.add(result.model)
            run = Run(
                row["group"] + "/" + language,
                row["label"],
                answer.choice,
                answer.confidence,
                latency_ms=(clock() - start) * 1000,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                cost_usd=result.usage.cost_usd,
            )
            if set(answer.probabilities) == set(labels):
                probabilities = tuple(answer.probabilities[label] for label in labels)
        except DecisionProviderError as error:
            run = Run(
                row["group"] + "/" + language,
                row["label"],
                None,
                0,
                latency_ms=(clock() - start) * 1000,
                error=error.reason_code,
            )
        if clock() >= deadline:
            raise ValueError("public_intent_deadline_exceeded")
        rows.append((row, run, probabilities))
    reports = {}
    for language in ("de", "en"):
        subset = [(row, run, probabilities) for row, run, probabilities in rows if row["language"] == language]
        samples = tuple(
            CalibrationSample(run.case_id, "validation", probabilities, labels.index(run.expected))
            for row, run, probabilities in subset
            if row["split"] == "validation" and probabilities
        )
        fit = fit_temperature(samples) if samples else {"status": "not_run"}
        scaled = [
            (
                row,
                replace(
                    run, confidence=scale_probabilities(probabilities, fit["temperature"])[labels.index(run.predicted)]
                )
                if probabilities and "temperature" in fit and run.predicted in labels
                else run,
            )
            for row, run, probabilities in subset
        ]
        validation = [run for row, run in scaled if row["split"] == "validation"]
        thresholds = threshold_table([run.observation() for run in validation], (0.5, 0.7, 0.8, 0.9, 0.95, 0.99))
        threshold = min(
            (r.threshold for r in thresholds if r.accepted >= 20 and r.precision_lower_bound >= 0.95), default=None
        )
        reports[language] = {
            "calibration": fit,
            "threshold": threshold,
            "threshold_selection_split": "validation",
            "decision": "shadow",
            "raw_confidence": {},
            "temperature_scaled": {},
            "selected_profile": None,
        }
        for split in ("validation", "holdout"):
            reports[language]["raw_confidence"][split] = provider_report(
                [run for row, run, _ in subset if row["split"] == split], calibrated=False
            )
            reports[language]["temperature_scaled"][split] = provider_report(
                [run for row, run in scaled if row["split"] == split], calibrated=False
            )
            # These are the source's frozen partitions, not hash-derived splits.
            reports[language]["raw_confidence"][split].pop("split", None)
            reports[language]["temperature_scaled"][split].pop("split", None)
        if threshold is not None:
            reports[language]["selected_profile"] = {
                "threshold": threshold,
                "grants_permission": False,
                **{
                    split: asdict(
                        threshold_table(
                            [run.observation() for row, run in scaled if row["split"] == split], (threshold,)
                        )[0]
                    )
                    for split in ("validation", "holdout")
                },
            }
    return {
        "schema": "ananta.public-intent-report.v1",
        "classification": "external_dataset_technical_observation",
        "production_release_evidence": False,
        "dataset_revision": MASSIVE_REVISION,
        "dataset_digest": hashlib.sha256(json.dumps(corpus, sort_keys=True).encode()).hexdigest(),
        "dataset_rights": corpus["declared_license"],
        "provider": provider.provider_id,
        "models": sorted(models),
        "cases": len(rows),
        "evaluation_validity": "inference_failed" if any(run.error for _, run, _ in rows) else "complete",
        "reports": reports,
        "raw": [
            {"split": row["split"], "language": row["language"], **asdict(run), "probabilities": list(probabilities)}
            for row, run, probabilities in rows
        ],
        "decision": "shadow",
        "scope": "Eight-class public utterance intent task; not a production tool-permission gate",
    }
