#!/usr/bin/env python3
"""Local tokenizer-boundary, repeated single/batch and resource observation."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.services.decision_providers.llamacpp import LlamaCppDecisionProvider
from agent.services.decision_providers.local_http import NoRedirect
from agent.services.decision_providers.types import DecisionProviderError, DecisionQuestion, DecisionRequest


def profile(url: str, *, repeats: int = 5) -> dict:
    if not 2 <= repeats <= 50:
        raise ValueError("decision_profile_repeats_invalid")
    provider = LlamaCppDecisionProvider(base_url=url)
    q = DecisionQuestion.choice(
        "action",
        "Select the requested read-only action.",
        {"read_only": "read", "read_write": "modify", "read_only_metadata": "read metadata only"},
    )
    requests = tuple(
        DecisionRequest(text, (q,), tenant_id="synthetic-profile", subject_id="operator")
        for text in ["Read only, do not modify anything.", "Only read the metadata; do not read document content."]
    )
    tokens = []
    for candidate in q.labels:
        for rendered in (json.dumps(candidate), " " + json.dumps(candidate), ": " + json.dumps(candidate)):
            http = urllib.request.Request(
                url.rstrip("/") + "/tokenize",
                data=json.dumps({"content": rendered, "add_special": False, "parse_special": False}).encode(),
                headers={"Content-Type": "application/json"},
            )
            # Reuse the adapter's no-redirect transport; prompts never escape
            # the explicitly local URL by a 30x response.
            with urllib.request.build_opener(NoRedirect()).open(http, timeout=5) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise DecisionProviderError("response_too_large")
            tokens.append({"candidate": candidate, "rendering": rendered, "tokens": json.loads(raw)["tokens"]})
    rows = []
    for batch_size in (1, 2):
        for index in range(repeats):
            start = time.monotonic()
            result = provider.decide_batch(requests[:batch_size])
            rows.append(
                {
                    "batch_size": batch_size,
                    "phase": "first_inference" if index == 0 else "warm",
                    "latency_ms": (time.monotonic() - start) * 1000,
                    "results": [r.to_mapping() for r in result],
                }
            )
    summaries = {}
    for batch_size in (1, 2):
        values = sorted(row["latency_ms"] for row in rows if row["batch_size"] == batch_size)
        summaries[str(batch_size)] = {
            "p50_ms": statistics.median(values),
            "p95_ms": values[round(0.95 * (len(values) - 1))],
            "p99_ms": values[round(0.99 * (len(values) - 1))],
            "decisions_per_second": batch_size * len(values) * 1000 / sum(values),
        }
    return {
        "schema": "ananta.parallel-decision-profile.v1",
        "classification": "synthetic_technical_observation",
        "production_release_evidence": False,
        "hardware": platform.platform(),
        "tokenizer_cases": tokens,
        "tokenizer_cases_digest": hashlib.sha256(json.dumps(tokens, sort_keys=True).encode()).hexdigest(),
        "summaries": summaries,
        "raw": rows,
        "cache_policy": "no cross-request cache; within-request sharing only",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    result = profile(args.url, repeats=args.repeats)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": "completed", "report": str(args.output), "production_release_evidence": False}))


if __name__ == "__main__":
    main()
