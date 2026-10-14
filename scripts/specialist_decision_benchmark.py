#!/usr/bin/env python3
"""Headless DE/EN local pilot; never activates a profile or promotes an artifact."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.services.decision_providers.benchmark_corpus import corpus
from agent.services.decision_providers.jeff import JeffDecisionProvider
from agent.services.decision_providers.llamacpp import LlamaCppDecisionProvider
from agent.services.decision_providers.local_http import default_local_hosts
from agent.services.decision_providers.pilot_benchmark import run_pilot
from agent.services.decision_providers.types import DecisionAnswer, DecisionResult


class TrainingMajorityBaseline:
    provider_id = "training_majority"

    def decide(self, request, *, timeout_seconds=None):
        q = request.questions[0]
        _, parts = corpus(q.key)
        counts = Counter(e.label for e in parts["train"])
        label, n = counts.most_common(1)[0]
        confidence = n / sum(counts.values())
        answer = (
            DecisionAnswer(q.key, "score", score=q.labels.index(label), confidence=confidence)
            if q.kind == "score"
            else DecisionAnswer(q.key, "choice", choice=label, confidence=confidence)
        )
        return DecisionResult(self.provider_id, "train-split-majority-v1", {q.key: answer})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jeff-url")
    parser.add_argument("--jeff-key-file", type=Path)
    parser.add_argument("--llamacpp-url")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    providers = {"majority": TrainingMajorityBaseline()}
    if args.jeff_url:
        if not args.jeff_key_file:
            parser.error("--jeff-key-file required")
        providers["jeff"] = JeffDecisionProvider(
            base_url=args.jeff_url,
            allowed_hosts=default_local_hosts(args.jeff_url),
            api_key=lambda: args.jeff_key_file.read_text().strip(),
            model_revision="d0a4e53d09cebe6bc963dd9be319d4279084bb2d",
            timeout_seconds=20,
        )
    if args.llamacpp_url:
        providers["llamacpp"] = LlamaCppDecisionProvider(base_url=args.llamacpp_url)
    report = run_pilot(providers)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {"report": str(args.output), "profiles": len(report["reports"]), "production_release_evidence": False}
        )
    )


if __name__ == "__main__":
    main()
