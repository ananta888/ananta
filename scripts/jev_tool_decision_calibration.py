#!/usr/bin/env python3
"""Calibrate decision-based tool choice against a live llama-server (JEVCPP-009).

Scores every labelled prompt of a ``ananta.tiny_tool_router_cases.v1`` file with
``POST /v1/decision`` over the Meet companion tool set, exactly as the
``parallel_decision`` adapter asks, and prints a calibration report: accuracy,
calibration error, precision/coverage per threshold and the lowest
``min_confidence`` that reaches the target precision.

    python3 scripts/jev_tool_decision_calibration.py --url http://127.0.0.1:18150 --out report.json

With ``--from-shadow`` it scores nothing and evaluates the companion's shadow
log instead (``worker/meet_media/tool_decision_shadow.py``): the reference is
the first tool call that really ran in the meeting, not a hand label.

    python3 scripts/jev_tool_decision_calibration.py \
        --from-shadow data/meet-media/worker-state/tool-decision-shadow.jsonl

The cases are synthetic evaluation data: the report tunes a threshold, it is
never release evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.services.tiny_router.benchmark import load_benchmark_cases  # noqa: E402
from agent.services.tiny_router.decision_calibration import Observation, calibration_report  # noqa: E402
from agent.services.tiny_router.parallel_decision import (  # noqa: E402
    DECISION_PATH,
    NO_TOOL,
    TOOL_FIELD,
    build_tool_decision_schema,
    decision_request_body,
)
from worker.meet_media.llm_tools import codecompass_toolbox  # noqa: E402

DEFAULT_CASES = ROOT / "benchmarks/tiny_tool_router/decision_calibration.v1.json"


def companion_tools() -> list[dict]:
    """The companion's full tool set; the ports are never called here."""
    return codecompass_toolbox(retriever=lambda _query: [], hub=lambda _name, _arguments: {}).definitions()


def score(url: str, body: dict, timeout: float) -> tuple[dict, float]:
    request = urllib.request.Request(url.rstrip("/") + DECISION_PATH, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read()), time.monotonic() - started


def shadow_observations(path: Path) -> list[Observation]:
    """Observations from shadow records that have a decision; errors and malformed lines are skipped."""
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("decision") and record.get("actual_first"):
            rows.append(Observation(f"shadow-{number}", str(record["actual_first"]), str(record["decision"]),
                                    float(record.get("probability") or 0.0)))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", default="http://127.0.0.1:18150")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--model", default="Ternary-Bonsai-2-27B-PQ2_0")
    parser.add_argument("--target-precision", type=float, default=0.95)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--from-shadow", type=Path, help="evaluate a companion shadow log instead of scoring")
    args = parser.parse_args(argv)
    if args.from_shadow:
        report = calibration_report(shadow_observations(args.from_shadow), target_precision=args.target_precision)
        text = json.dumps(report, indent=2, ensure_ascii=False)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
        print(json.dumps({key: report[key] for key in ("total", "accuracy", "expected_calibration_error",
                                                        "recommended_min_confidence")}, ensure_ascii=False))
        return 0

    decision = build_tool_decision_schema(companion_tools())
    observations, latencies, raw = [], [], []
    for case in load_benchmark_cases(args.cases):
        response, seconds = score(args.url, decision_request_body(decision, case["prompt"], model_id=args.model),
                                  args.timeout)
        field = response["results"][0]["fields"][TOOL_FIELD]
        expected = (case.get("expected") or {}).get("tool_name") or NO_TOOL
        observations.append(Observation(case["id"], expected, str(field["value"]), float(field["probability"])))
        latencies.append(seconds)
        raw.append({"id": case["id"], "value": field["value"], "probability": field["probability"],
                    "margin": field.get("margin"), "seconds": round(seconds, 3)})

    report = calibration_report(observations, target_precision=args.target_precision)
    ordered = sorted(latencies)
    report["latency_seconds"] = {"first": round(latencies[0], 3), "median": round(ordered[len(ordered) // 2], 3),
                                 "max": round(ordered[-1], 3)}
    report["cases_file"] = str(args.cases.relative_to(ROOT)) if args.cases.is_relative_to(ROOT) else str(args.cases)
    report["observations"] = raw
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    summary = {key: report[key] for key in ("total", "accuracy", "expected_calibration_error",
                                            "recommended_min_confidence", "latency_seconds")}
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
