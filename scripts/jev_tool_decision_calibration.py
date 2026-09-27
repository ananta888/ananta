#!/usr/bin/env python3
"""Calibrate decision-based tool choice against a live llama-server (JEVCPP-009).

Scores every labelled prompt of one or more ``ananta.tiny_tool_router_cases.v1``
files with ``POST /v1/decision`` over the Meet companion tool set, exactly as
the ``parallel_decision`` adapter asks, and prints a calibration report:
accuracy, calibration error, precision/coverage per threshold, generated
argument hits, and a threshold chosen on the validation split and measured
once on the untouched holdout split (``sha256(case_id)`` mod 5).

    python3 scripts/jev_tool_decision_calibration.py --url http://127.0.0.1:18150 \\
        --cases benchmarks/tiny_tool_router/decision_calibration.v1.json \\
        --cases benchmarks/tiny_tool_router/decision_calibration_hard.v1.json --compare-chat --out report.json

``--compare-chat`` asks the same model the same prompts through the normal
``/v1/chat/completions`` tool call (the System-2 path) for accuracy and latency.

With ``--from-shadow`` it scores nothing and evaluates the companion's shadow
log instead (``worker/meet_media/tool_decision_shadow.py``): the reference is
the first tool call that really ran in the meeting; a different tool the
model called itself counts as acceptable too.

    python3 scripts/jev_tool_decision_calibration.py \\
        --from-shadow data/meet-media/worker-state/tool-decision-shadow.jsonl

The cases are synthetic evaluation data: the report tunes a threshold, it is
never release evidence.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.services.tiny_router.benchmark import load_benchmark_cases  # noqa: E402
from agent.services.tiny_router.decision_calibration import (  # noqa: E402
    Observation,
    calibration_report,
    is_holdout,
    split_report,
)
from ananta_contracts.tool_decision import (  # noqa: E402
    DECISION_PATH,
    NO_TOOL,
    TEXT_FIELD,
    TOOL_FIELD,
    build_tool_decision_schema,
    request_body,
)
from scripts.jev_decision_smoke import post as post_json  # noqa: E402
from worker.meet_media.llm_tools import codecompass_toolbox, leaked_calls  # noqa: E402

DEFAULT_CASES = ROOT / "benchmarks/tiny_tool_router/decision_calibration.v1.json"
CHAT_SYSTEM = ("Decide how to handle the user's request with the tools below. Call the single best tool, "
               "or answer directly in one short sentence when no tool is needed.")


def companion_tools() -> list[dict]:
    """The companion's full tool set; the ports are never called here."""
    return codecompass_toolbox(retriever=lambda _query: [], hub=lambda _name, _arguments: {}).definitions()


def post(url: str, path: str, body: dict, timeout: float) -> tuple[dict, float]:
    status, payload, seconds = post_json(url, path, body, timeout)
    if status != 200:
        raise RuntimeError(f"{path} answered {status}: {payload.get('error')}")
    return payload, seconds


def expectation(case: dict) -> tuple[str, tuple[str, ...], list[str]]:
    expected = case.get("expected") or {}
    also = tuple(item or NO_TOOL for item in expected.get("also_acceptable") or [])
    return expected.get("tool_name") or NO_TOOL, also, list(expected.get("argument_contains") or [])


def argument_hit(text: Any, terms: list[str]) -> bool:
    return any(term.lower() in str(text or "").lower() for term in terms)


def chat_choice(url: str, prompt: str, tools: list[dict], model: str, timeout: float) -> tuple[str, str, float]:
    """The first tool the chat path calls (native or leaked as text) and its first argument, or ``none``."""
    response, seconds = post(url, "/v1/chat/completions", {
        "model": model, "messages": [{"role": "system", "content": CHAT_SYSTEM}, {"role": "user", "content": prompt}],
        "tools": tools, "tool_choice": "auto", "max_tokens": 160, "temperature": 0,
        "reasoning_effort": "none", "chat_template_kwargs": {"enable_thinking": False},
    }, timeout)
    message = ((response.get("choices") or [{}])[0].get("message")) or {}
    calls = message.get("tool_calls") or []
    if calls:
        function = calls[0].get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except ValueError:
            arguments = {}
        name = str(function.get("name") or NO_TOOL)
    else:
        leaked = leaked_calls(message.get("content") or "")
        name, arguments = (leaked[0] if leaked else (NO_TOOL, {}))
    first = next(iter(arguments.values()), "") if isinstance(arguments, dict) else ""
    return name, first if isinstance(first, str) else json.dumps(first), seconds


def shadow_observations(path: Path) -> list[Observation]:
    """Observations from shadow records that have a decision; errors and malformed lines are skipped."""
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("decision") and record.get("actual_first"):
            own = record.get("model_first")
            also = (own,) if own and own not in (NO_TOOL, record["actual_first"]) else ()
            rows.append(Observation(f"shadow-{number}", str(record["actual_first"]), str(record["decision"]),
                                    float(record.get("probability") or 0.0), also))
    return rows


def latency(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {"median": round(statistics.median(ordered), 3), "p90": round(ordered[int(0.9 * (len(ordered) - 1))], 3),
            "max": round(ordered[-1], 3)} if ordered else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", default="http://127.0.0.1:18150")
    parser.add_argument("--cases", type=Path, action="append", help="case file; repeatable")
    parser.add_argument("--model", default="Ternary-Bonsai-2-27B-PQ2_0")
    parser.add_argument("--target-precision", type=float, default=0.95)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--no-open-field", action="store_true",
                        help="score the tool only (servers without open fields)")
    parser.add_argument("--compare-chat", action="store_true", help="also run the normal chat tool call")
    parser.add_argument("--from-shadow", type=Path, help="evaluate a companion shadow log instead of scoring")
    args = parser.parse_args(argv)
    if args.from_shadow:
        observations = shadow_observations(args.from_shadow)
        report = {**calibration_report(observations, target_precision=args.target_precision),
                  "split": split_report(observations, target_precision=args.target_precision)}
        return emit(report, args.out, ("total", "accuracy", "expected_calibration_error",
                                       "recommended_min_confidence"))

    tools = companion_tools()
    decision = build_tool_decision_schema(tools, open_field=not args.no_open_field)
    cases = [case for path in (args.cases or [DEFAULT_CASES]) for case in load_benchmark_cases(path)]
    observations, raw, seconds_all, argument_checks = [], [], [], []
    chat_rows = []
    for case in cases:
        expected, also, terms = expectation(case)
        response, seconds = post(args.url, DECISION_PATH,
                                 request_body(decision, case["prompt"], model_id=args.model), args.timeout)
        fields = response["results"][0]["fields"]
        tool = fields[TOOL_FIELD]
        observation = Observation(case["id"], expected, str(tool["value"]), float(tool["probability"]), also)
        observations.append(observation)
        seconds_all.append(seconds)
        row = {"id": case["id"], "value": tool["value"], "probability": tool["probability"],
               "margin": tool.get("margin"), "correct": observation.correct, "seconds": round(seconds, 3),
               "holdout": is_holdout(case["id"])}
        text = fields.get(TEXT_FIELD)
        if isinstance(text, dict) and not text.get("skipped"):
            row.update(text=text.get("value"), text_truncated=text.get("truncated"))
            if terms and observation.correct:
                row["text_ok"] = argument_hit(text.get("value"), terms)
                argument_checks.append(row["text_ok"])
        raw.append(row)
        if args.compare_chat:
            name, first, chat_seconds = chat_choice(args.url, case["prompt"], tools, args.model, args.timeout)
            correct = name == expected or name in also
            chat_rows.append({"id": case["id"], "value": name, "correct": correct, "seconds": round(chat_seconds, 3),
                              "argument_ok": argument_hit(first, terms) if terms and correct else None})

    report = {
        **calibration_report(observations, target_precision=args.target_precision),
        "split": split_report(observations, target_precision=args.target_precision),
        # the same, counting only the single label as correct (no defensible alternatives)
        "split_strict": split_report([Observation(item.case_id, item.expected, item.predicted, item.probability)
                                      for item in observations], target_precision=args.target_precision),
        "latency_seconds": {"first": round(seconds_all[0], 3), **latency(seconds_all[1:])},
        "cases_files": [str(path) for path in (args.cases or [DEFAULT_CASES])],
        "observations": raw,
    }
    if argument_checks:
        report["text_argument"] = {"checked": len(argument_checks), "ok": sum(argument_checks),
                                   "rate": round(sum(argument_checks) / len(argument_checks), 4)}
    if chat_rows:
        checked = [row["argument_ok"] for row in chat_rows if row["argument_ok"] is not None]
        report["chat_baseline"] = {
            "accuracy": round(sum(row["correct"] for row in chat_rows) / len(chat_rows), 4),
            "argument_rate": round(sum(checked) / len(checked), 4) if checked else None,
            "latency_seconds": latency([row["seconds"] for row in chat_rows[1:]]),
            "errors": [row for row in chat_rows if not row["correct"]],
        }
    return emit(report, args.out, ("total", "accuracy", "expected_calibration_error", "recommended_min_confidence",
                                   "latency_seconds", "text_argument"))


def emit(report: dict, out: Path | None, keys: tuple[str, ...]) -> int:
    if out:
        out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = {key: report[key] for key in keys if key in report}
    split = report.get("split") or {}
    summary["split"] = {"recommended_on_validation": split.get("recommended_on_validation"),
                        "holdout": split.get("holdout")}
    if "split_strict" in report:
        strict = report["split_strict"]
        summary["split_strict"] = {"accuracy_validation": strict["validation"]["accuracy"],
                                   "recommended_on_validation": strict.get("recommended_on_validation"),
                                   "holdout": strict.get("holdout")}
    if "chat_baseline" in report:
        summary["chat_baseline"] = {key: value for key, value in report["chat_baseline"].items() if key != "errors"}
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
