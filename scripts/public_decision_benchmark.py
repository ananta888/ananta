#!/usr/bin/env python3
"""Run a frozen public intent corpus against an explicitly local decision worker."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.services.decision_providers.jeff import JeffDecisionProvider
from agent.services.decision_providers.llamacpp import LlamaCppDecisionProvider
from agent.services.decision_providers.local_http import default_local_hosts
from agent.services.decision_providers.public_benchmark import run_public_intent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--provider", choices=["jeff", "llamacpp"], required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--model", default="")
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--max-seconds", type=float, default=900)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.corpus.stat().st_size > 2 * 1024 * 1024:
        parser.error("public corpus exceeds 2 MiB")
    corpus = json.loads(args.corpus.read_text())
    if args.provider == "jeff":
        if args.key_file is None:
            parser.error("--key-file is required for Jeff")
        key = args.key_file.read_text().strip()
        provider = JeffDecisionProvider(
            base_url=args.url,
            model_revision="d0a4e53d09cebe6bc963dd9be319d4279084bb2d",
            allowed_hosts=default_local_hosts(args.url),
            api_key=lambda: key,
            timeout_seconds=30,
            max_retries=0,
        )
    else:
        provider = LlamaCppDecisionProvider(base_url=args.url, model=args.model, require_cache_isolation=True)
    report = run_public_intent(provider, corpus, max_seconds=args.max_seconds)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "cases": report["cases"],
                "evaluation_validity": report["evaluation_validity"],
                "production_release_evidence": False,
            }
        )
    )
    return 0 if report["evaluation_validity"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
