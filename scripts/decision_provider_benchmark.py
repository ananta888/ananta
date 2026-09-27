#!/usr/bin/env python3
"""Benchmark decision providers against Ananta's current decisions (DPRV).

Areas (labelled cases in ``benchmarks/``):

- ``tool_routing``: tool or no tool for a request, over the Meet companion tool
  set (``benchmarks/tiny_tool_router/decision_calibration*.v1.json``);
- ``companion``: companion route + "needs a knowledge lookup" (RAG yes/no);
- ``retrieval``: retrieval intent + "needs retrieval" (RAG yes/no);
- ``hub_direct``: which read-only tool the Hub may run directly, or not eligible;
- ``chat_intent``: operator TUI chat intent (no production caller today).

Providers:

- ``rules``: today's classifier of the area (for ``tool_routing``: none);
- ``current``: today's tool decision (``/v1/decision`` with the production
  contract, the companion fast path) -- ``tool_routing`` only;
- ``chat``: today's System-2 path, a chat tool call -- ``tool_routing`` only;
- ``jev``: TypeSafe Jev (needs ``TYPESAFE_API_KEY`` or ``TYPESAFE_API_KEY_FILE``);
- ``local_decision``: the local llama.cpp ``/v1/decision`` server, generic questions;
- ``llm``: the local chat model answering the same typed questions as JSON.

Cases sharing a text become one request with several questions, as in
production. Reports accuracy, macro-F1, calibration (ECE, Brier), confident
mistakes, latency, tokens, cost, threshold table (0.70-0.98) and simulated
cascades (accept provider A at a threshold, else B) with their fallback rate.
Every label here is synthetic evaluation data, never release evidence.

    TYPESAFE_API_KEY_FILE=~/.config/secrets/api-keys.env \\
      python3 scripts/decision_provider_benchmark.py --out data/decision-benchmarks/report.json
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from agent.services.decision_providers.evaluation import (  # noqa: E402
    THRESHOLDS,
    Run,
    provider_report,
    simulate_cascade,
)
from agent.services.decision_providers.llamacpp import LlamaCppDecisionProvider  # noqa: E402
from agent.services.decision_providers.llm import LLMDecisionProvider, OpenAICompatibleCompletion  # noqa: E402
from agent.services.decision_providers.tool_choice import TOOL_KEY, tool_questions  # noqa: E402
from agent.services.decision_providers.types import (  # noqa: E402
    DecisionProviderError,
    DecisionQuestion,
    DecisionRequest,
)
from agent.services.decision_providers.typesafe import TypeSafeJevProvider, read_api_key  # noqa: E402

BENCH = ROOT / "benchmarks/decision_providers"
TOOL_CASES = (ROOT / "benchmarks/tiny_tool_router/decision_calibration.v1.json",
              ROOT / "benchmarks/tiny_tool_router/decision_calibration_hard.v1.json")
GROUPS = {
    "companion": ("companion_route", "companion_knowledge"),
    "retrieval": ("retrieval_intent", "rag_needed"),
    "hub_direct": ("hub_direct",),
    "chat_intent": ("chat_intent",),
    "injection": ("prompt_injection",),
}
PAIRS = (("local_decision", "jev"), ("local_decision", "llm"), ("jev", "llm"))
CASCADES = (("jev", "rules"), ("jev", "llm"), ("jev", "chat"), ("jev", "local_decision"), ("local_decision", "rules"),
            ("local_decision", "llm"), ("local_decision", "chat"), ("current", "chat"), ("llm", "rules"))


@dataclass
class Case:
    case_id: str
    state: str
    lang: str
    split: str  # base | hard
    expected: dict[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)  # question key -> (label, also)
    terms: tuple[str, ...] = ()  # tool routing: terms one of which the text argument should contain


@dataclass
class Area:
    name: str
    questions: tuple[DecisionQuestion, ...]
    cases: list[Case]
    rules: Callable[[str], dict[str, str]] | None = None
    question_keys: dict[str, str] = field(default_factory=dict)  # dataset area -> question key


# --- datasets --------------------------------------------------------------------------------------


def _label(value: Any) -> str:
    return ("true" if value else "false") if isinstance(value, bool) else str(value)


def load_group(group: str) -> Area:
    questions, cases, keys = [], {}, {}
    for dataset in GROUPS[group]:
        for split, suffix in (("base", ""), ("hard", "_hard")):
            path = BENCH / f"{dataset}{suffix}.v1.json"
            if not path.exists():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            spec = data["question"]
            key = spec["key"]
            if key not in keys.values():
                keys[dataset] = key
                if spec["kind"] == "choice":
                    questions.append(DecisionQuestion.choice(key, spec["instructions"], spec["options"]))
                else:
                    questions.append(DecisionQuestion.noul(key, spec["instructions"]))
            for row in data["cases"]:
                case = cases.setdefault(row["state"], Case(row["id"], row["state"], row.get("lang", "?"), split))
                case.expected[key] = (_label(row["expected"]), tuple(_label(a) for a in row.get("acceptable") or []))
    return Area(group, tuple(questions), list(cases.values()), rules=RULES[group](), question_keys=keys)


def load_tool_routing() -> Area:
    from jev_tool_decision_calibration import companion_tools, expectation

    tools = companion_tools()
    _schema, request, _values = tool_questions(tools, "placeholder")
    cases = []
    for path in TOOL_CASES:
        split = "hard" if "hard" in path.name else "base"
        for row in json.loads(path.read_text(encoding="utf-8"))["cases"]:
            expected, also, terms = expectation(row)
            cases.append(Case(row["id"], row["prompt"], row.get("lang", "?"), split, {TOOL_KEY: (expected, also)},
                              tuple(terms)))
    area = Area("tool_routing", request.questions, cases, question_keys={"tool_routing": TOOL_KEY})
    area.tools = tools  # type: ignore[attr-defined]
    return area


# --- today's rules ---------------------------------------------------------------------------------


def _companion_rules():
    from worker.meet_media.companion_router import classify

    def rules(state):
        decision = classify(state)
        return {"companion_route": decision.route, "needs_code_knowledge_lookup": _label(bool(decision.knowledge))}
    return rules


def _retrieval_rules():
    from agent.services.retrieval_profile_service import classify_retrieval_intent

    def rules(state):
        intent = classify_retrieval_intent(state, None)[1]
        return {"retrieval_intent": intent, "needs_project_retrieval": _label(intent != "generic_chat")}
    return rules


def _hub_direct_rules():
    from agent.services.hub_direct_execution_router import HubDirectExecutionRouter

    tools = ["repo.list_files", "repo.read_file_range", "repo.grep", "git.status", "git.diff_readonly",
             "test.discover", "workspace.diff"]
    registry = type("NoAliases", (), {"match_intent_alias": staticmethod(lambda *a, **k: None)})()
    router = HubDirectExecutionRouter(dynamic_registry=registry)
    cfg = {"hub_direct_execution": {"enabled": True, "confidence_threshold": 0.8, "allowed_tools": tools}}

    def rules(state):
        decision = router.classify(state, task=None, agent_cfg=cfg)
        return {"hub_direct_tool": decision.tool_name if decision.eligible else "not_eligible"}
    return rules


def _chat_rules():
    from client_surfaces.operator_tui.chat_policy import classify_chat_intent

    return lambda state: {"chat_intent": classify_chat_intent(state)}


def _source_constant(path: str, name: str):
    """A constant (literal, or re.compile(...) of literals) read from source, without importing the app stack."""
    import ast
    import re

    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            value = node.value

            def build(item):
                if isinstance(item, ast.Call) and getattr(item.func, "attr", "") == "compile":
                    return re.compile(ast.literal_eval(item.args[0]))
                return ast.literal_eval(item)
            if isinstance(value, (ast.Tuple, ast.List)) and any(isinstance(e, ast.Call) for e in value.elts):
                return [build(e) for e in value.elts]
            return build(value)
    raise KeyError(f"{path}:{name}")


def _injection_rules():
    """Today's pattern checks (read from their modules), united: a hit is 'suspicious' (they know no type)."""
    phrases = [p.lower() for p in _source_constant("agent/services/planning_utils.py", "PROMPT_INJECTION_PATTERNS")]
    goal_phrases = ["ignore previous instructions", "jailbreak", "dan mode"]  # planning_utils.validate_goal
    remote = _source_constant("agent/services/remote_source_payload_store.py", "_PROMPT_INJECTION")
    scanner = _source_constant("agent/services/source_filesystem_scanner.py", "_INJECTION_PATTERNS")

    def rules(state):
        lower = state.lower()
        hit = (any(p in lower for p in phrases) or any(p in lower for p in goal_phrases)
               or bool(remote.search(state)) or any(p.search(state) for p in scanner))
        return {"intent": "instruction_override" if hit else "benign"}
    return rules


RULES = {"injection": _injection_rules, "companion": _companion_rules, "retrieval": _retrieval_rules,
         "hub_direct": _hub_direct_rules, "chat_intent": _chat_rules}


# --- running ---------------------------------------------------------------------------------------


def _runs_from(case: Case, labels: dict[str, tuple[str | None, float]], latency_ms: float, tokens: tuple[int, int],
               cost: float, error: str | None, argument: str | None = None,
               argument_confidence: float | None = None) -> dict[str, Run]:
    runs = {}
    share = max(1, len(case.expected))
    for key, (expected, also) in case.expected.items():
        predicted, confidence = labels.get(key, (None, 0.0))
        runs[key] = Run(case.case_id, expected, predicted, confidence, also, latency_ms,
                        tokens[0] // share, tokens[1] // share, cost / share, error, argument, argument_confidence)
    return runs


def typed_labels(result, questions) -> dict[str, tuple[str, float]]:
    labels = {}
    for question in questions:
        answer = result.answer(question.key)
        if question.kind == "noul":
            labels[question.key] = (_label(answer.probability >= 0.5), answer.confidence)
        elif question.kind == "choice":
            labels[question.key] = (answer.choice, answer.confidence)
    return labels


def run_typed(provider, area: Area, case: Case) -> dict[str, Run]:
    started = time.monotonic()
    try:
        request = DecisionRequest(state=case.state, questions=area.questions, purpose=area.name)
        result = provider.decide(request)
    except DecisionProviderError as error:
        return _runs_from(case, {}, (time.monotonic() - started) * 1000, (0, 0), 0.0, error.reason_code)
    return _runs_from(case, typed_labels(result, area.questions), result.latency_ms,
                      (result.usage.input_tokens, result.usage.output_tokens), result.usage.cost_usd, None)


def run_rules(area: Area, case: Case) -> dict[str, Run]:
    started = time.monotonic()
    labels = {key: (value, 1.0) for key, value in area.rules(case.state).items()}
    return _runs_from(case, labels, (time.monotonic() - started) * 1000, (0, 0), 0.0, None)


def run_current_tool_decision(url: str, area: Area, case: Case) -> dict[str, Run]:
    """Today's companion fast path: the production /v1/decision tool contract."""
    from jev_tool_decision_calibration import post

    from ananta_contracts.tool_decision import build_tool_decision_schema, read_tool_decision, request_body

    schema = build_tool_decision_schema(area.tools, open_field=True)  # type: ignore[attr-defined]
    try:
        response, seconds = post(url, "/v1/decision", request_body(schema, case.state), 60)
        decision = read_tool_decision(response, schema, min_confidence=0.0)
    except Exception as error:  # noqa: BLE001 -- a failed case is a measured error
        return _runs_from(case, {}, 0.0, (0, 0), 0.0, type(error).__name__)
    label = decision.tool or "none"
    usage = response.get("usage") or {}
    text = next(iter(decision.arguments.values()), None) if decision.arguments else None
    return _runs_from(case, {TOOL_KEY: (label, decision.confidence)}, seconds * 1000,
                      (int(usage.get("prompt_tokens") or 0) + int(usage.get("context_tokens") or 0), 0), 0.0, None,
                      text if isinstance(text, str) else None)


_CANDIDATES = None


def candidate_source():
    """Prompt spans plus a symbol index built from the working tree, like the Hub builds it from its index."""
    global _CANDIDATES
    if _CANDIDATES is None:
        from agent.services.decision_providers.argument_candidates import (
            CompositeCandidates,
            PromptSpanCandidates,
            SymbolIndexCandidates,
            symbol_names,
        )

        skip = ("data/", "node_modules", "vendor/", ".git/", "project-workspaces", "artifacts/")
        files = []
        for path in ROOT.rglob("*"):
            rel = str(path.relative_to(ROOT))
            if rel.startswith(skip) or "/node_modules/" in rel or path.suffix not in (".py", ".ts", ".js", ".java",
                                                                                      ".go", ".rs"):
                continue
            if path.is_file() and path.stat().st_size < 400_000:
                files.append((rel, path.read_text(encoding="utf-8", errors="ignore")))
        names = symbol_names(files)
        _CANDIDATES = CompositeCandidates([PromptSpanCandidates(), SymbolIndexCandidates(lambda: names)])
    return _CANDIDATES


def run_tool_choice(provider, area: Area, case: Case) -> dict[str, Run]:
    """Tool and text argument via typed questions, the text argument chosen among candidates."""
    from agent.services.decision_providers.tool_choice import read_tool_choice

    started = time.monotonic()
    try:
        schema, request, values = tool_questions(area.tools, case.state, candidates=candidate_source())  # type: ignore[attr-defined]
        result = provider.decide(request)
    except DecisionProviderError as error:
        return _runs_from(case, {}, (time.monotonic() - started) * 1000, (0, 0), 0.0, error.reason_code)
    answer = result.answer(TOOL_KEY)
    decision = read_tool_choice(schema, result, values, case.state, min_confidence=0.0)
    text = next((v for v in decision.arguments.values() if isinstance(v, str)), None)
    text_key = next((k for k, (item, _labels) in values.items()
                     if k.startswith("text_") and item.tool == answer.choice), None)
    text_confidence = result.answer(text_key).confidence if text_key else None
    return _runs_from(case, {TOOL_KEY: (answer.choice, answer.confidence)}, result.latency_ms,
                      (result.usage.input_tokens, result.usage.output_tokens), result.usage.cost_usd, None, text,
                      text_confidence)


def run_chat(url: str, model: str, area: Area, case: Case) -> dict[str, Run]:
    """Today's System-2 path: a chat tool call (no confidence: reported as 1.0, uncalibrated)."""
    from jev_tool_decision_calibration import chat_choice

    try:
        name, _argument, seconds = chat_choice(url, case.state, area.tools, model, 90)  # type: ignore[attr-defined]
    except Exception as error:  # noqa: BLE001
        return _runs_from(case, {}, 0.0, (0, 0), 0.0, type(error).__name__)
    return _runs_from(case, {TOOL_KEY: (name, 1.0)}, seconds * 1000, (0, 0), 0.0, None)


CACHE: Path | None = None


def _cache_file(area: str, name: str) -> Path | None:
    return None if CACHE is None else CACHE / f"{area}.{name}.json"


def run_provider(name: str, fn: Callable[[Case], dict[str, Run]], cases: list[Case], workers: int,
                 progress: bool, area: str = "") -> dict[str, dict[str, Run]]:
    """case id -> question key -> run; a finished (area, provider) is cached and reused."""
    cache = _cache_file(area, name) if area else None
    if cache is not None and cache.exists():
        stored = json.loads(cache.read_text(encoding="utf-8"))
        if set(stored) == {case.case_id for case in cases}:
            if progress:
                print(f"  {name}: cached", file=sys.stderr, flush=True)
            return {cid: {key: Run(**{**row, "acceptable": tuple(row["acceptable"])}) for key, row in runs.items()}
                    for cid, runs in stored.items()}
    results: dict[str, dict[str, Run]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, case): case for case in cases}
        for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
            results[futures[future].case_id] = future.result()
            if progress and done % 25 == 0:
                print(f"  {name}: {done}/{len(cases)}", file=sys.stderr, flush=True)
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({cid: {key: run.__dict__ for key, run in runs.items()}
                                     for cid, runs in results.items()}), encoding="utf-8")
    return results


# --- reporting ---------------------------------------------------------------------------------------


UNCALIBRATED = {"rules", "chat", "llm"}


def area_report(area: Area, outputs: dict[str, dict[str, dict[str, Run]]]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for dataset, key in area.question_keys.items():
        per_split: dict[str, Any] = {}
        for split in ("base", "hard", "all"):
            ids = [c.case_id for c in area.cases if key in c.expected and (split == "all" or c.split == split)]
            if not ids:
                continue
            providers = {}
            for provider, runs in outputs.items():
                rows = [runs[i][key] for i in ids if i in runs and key in runs[i]]
                if rows:
                    providers[provider] = provider_report(rows, calibrated=provider not in UNCALIBRATED)
            cascades = {}
            for first, second in CASCADES:
                if first in outputs and second in outputs:
                    a = {i: outputs[first][i][key] for i in ids if i in outputs[first]}
                    b = {i: outputs[second][i][key] for i in ids if i in outputs[second]}
                    if a and set(a) == set(b):
                        cascades[f"{first}->{second}"] = simulate_cascade(a, b)
            per_split[split] = {"cases": len(ids), "providers": providers, "cascades": cascades}
            if area.name == "tool_routing":
                per_split[split]["arguments"] = argument_report(area, outputs, ids, key)
            if area.name == "injection":
                per_split[split]["detection"] = detection_report(outputs, ids, key)
        report[dataset] = per_split
    return report


def argument_report(area: Area, outputs: dict[str, dict[str, dict[str, Run]]], ids: list[str],
                    key: str) -> dict[str, Any]:
    """Text-argument quality where the right tool was chosen and the case names expected terms.

    ``hit``: the argument contains an expected term; ``precise``: a hit that is also short (not the whole
    request). Providers that record no argument pass the whole request (their behaviour)."""
    by_id = {c.case_id: c for c in area.cases}
    report = {}
    for name, runs in outputs.items():
        rows = [(by_id[i], runs[i][key]) for i in ids if i in runs and by_id[i].terms and runs[i][key].correct
                and runs[i][key].predicted not in ("none", None)]
        if not rows:
            continue
        hits = precise = 0
        for case, run in rows:
            argument = (run.argument if run.argument is not None else case.state).strip().lower()
            hit = any(term.lower() in argument for term in case.terms)
            hits += hit
            precise += hit and len(argument) <= 2 * max(len(t) for t in case.terms) + 12
        report[name] = {"cases": len(rows), "hit": round(hits / len(rows), 4), "precise": round(precise / len(rows), 4)}
    return report


def _flag(run: Run, threshold: float) -> bool:
    """Screen semantics: benign only when the answer is benign with enough confidence."""
    return not (run.predicted == "benign" and run.confidence >= threshold)


def _detection(flags: dict[str, bool], truth: dict[str, bool]) -> dict[str, Any]:
    attacks = [i for i in truth if truth[i]]
    benign = [i for i in truth if not truth[i]]
    caught = sum(flags[i] for i in attacks)
    false_alarms = sum(flags[i] for i in benign)
    flagged = caught + false_alarms
    return {"recall": round(caught / len(attacks), 4) if attacks else None,
            "false_positive_rate": round(false_alarms / len(benign), 4) if benign else None,
            "precision": round(caught / flagged, 4) if flagged else None,
            "missed": [i for i in attacks if not flags[i]], "false_alarms": [i for i in benign if flags[i]]}


def detection_report(outputs: dict[str, dict[str, dict[str, Run]]], ids: list[str], key: str) -> dict[str, Any]:
    """Binary screening (attack vs benign) per provider and for agreement pairs, per threshold."""
    truth = {i: next(iter(outputs.values()))[i][key].expected != "benign" for i in ids}
    report: dict[str, Any] = {"providers": {}, "agreement_screen": {}, "agreement_routing": {}}
    for name, runs in outputs.items():
        thresholds = (0.0,) if name == "rules" else THRESHOLDS
        report["providers"][name] = {str(t): _detection({i: _flag(runs[i][key], t) for i in ids}, truth)
                                     for t in thresholds}
    for first, second in PAIRS:
        if first in outputs and second in outputs:
            a, b = outputs[first], outputs[second]
            label = f"{first}+{second}"
            report["agreement_screen"][label] = {
                str(t): _detection({i: _flag(a[i][key], t) or _flag(b[i][key], t) for i in ids}, truth)
                for t in THRESHOLDS}
            rows = {}
            for t in THRESHOLDS:
                accepted = [i for i in ids if a[i][key].predicted is not None
                            and a[i][key].predicted == b[i][key].predicted
                            and min(a[i][key].confidence, b[i][key].confidence) >= t]
                correct = sum(a[i][key].correct for i in accepted)
                rows[str(t)] = {"coverage": round(len(accepted) / len(ids), 4),
                                "precision": round(correct / len(accepted), 4) if accepted else None,
                                "wrong": [i for i in accepted if not a[i][key].correct]}
            report["agreement_routing"][label] = rows
    return report


def summary_lines(report: dict[str, Any]) -> list[str]:
    lines = ["| Bereich | Set | Provider | n | Acc | F1 | ECE | Fehler>=0.9 | p50 ms | $/1k |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for area, datasets in report["areas"].items():
        for dataset, splits in datasets.items():
            for split, data in splits.items():
                for provider, row in data["providers"].items():
                    lines.append(f"| {dataset} | {split} | {provider} | {row['cases']} | {row['accuracy']:.3f} | "
                                 f"{row['macro_f1']:.3f} | {row['ece']:.3f} | {row['confident_mistakes']['count']} | "
                                 f"{row['latency_ms']['p50']:.0f} | {row['cost_usd']['per_1000_decisions']:.4f} |")
    return lines


# --- main --------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--areas", default="tool_routing,companion,retrieval,hub_direct,chat_intent,injection")
    parser.add_argument("--providers", default="rules,current,chat,jev,jev_cand,local_decision,local_cand,llm")
    parser.add_argument("--local-url", default=os.environ.get("ANANTA_PARALLEL_DECISION_URL", "http://127.0.0.1:18150"))
    parser.add_argument("--llm-model", default="")
    parser.add_argument("--jev-model", default="jev-latest")
    parser.add_argument("--jev-workers", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0, help="at most N cases per area (smoke)")
    parser.add_argument("--out", type=Path, default=ROOT / "data/decision-benchmarks/report.json")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--cache", type=Path, default=ROOT / "data/decision-benchmarks/cache",
                        help="per (area, provider) results; a rerun only runs what is missing ('' = off)")
    args = parser.parse_args(argv)
    wanted = [p.strip() for p in args.providers.split(",") if p.strip()]
    global CACHE
    CACHE = None if str(args.cache) in ("", ".") or args.limit else args.cache

    areas = []
    for name in [a.strip() for a in args.areas.split(",") if a.strip()]:
        area = load_tool_routing() if name == "tool_routing" else load_group(name)
        if args.limit:
            area.cases = area.cases[: args.limit]
        areas.append(area)

    skipped: dict[str, str] = {}
    jev = (TypeSafeJevProvider(model=args.jev_model, timeout_seconds=15.0)
           if {"jev", "jev_cand"} & set(wanted) else None)
    if jev is not None and not read_api_key():
        skipped["jev"] = "api_key_missing (TYPESAFE_API_KEY / TYPESAFE_API_KEY_FILE)"
        jev = None
    local = LlamaCppDecisionProvider(base_url=args.local_url, timeout_seconds=60.0)
    local_ok = local.is_available()[0]
    for name in ("current", "chat", "local_decision", "llm"):
        if name in wanted and not local_ok:
            skipped[name] = f"local server unreachable at {args.local_url}"
    model = args.llm_model
    if local_ok and not model:
        from jev_tool_decision_calibration import post

        try:
            model = (post(args.local_url, "/v1/models", {}, 10)[0].get("data") or [{}])[0].get("id", "")
        except Exception:  # noqa: BLE001
            model = ""
    llm = LLMDecisionProvider(OpenAICompatibleCompletion(base_url=args.local_url + "/v1", model=model),
                              model=model, timeout_seconds=90.0)

    report: dict[str, Any] = {"schema": "ananta.decision_provider_benchmark.v1", "started_at": time.time(),
                              "thresholds": list(THRESHOLDS), "skipped": skipped, "areas": {},
                              "evidence": "synthetic evaluation labels; never release evidence",
                              "models": {"jev": args.jev_model, "local": model}}
    for area in areas:
        if not args.quiet:
            print(f"== {area.name}: {len(area.cases)} cases", file=sys.stderr, flush=True)
        outputs: dict[str, dict[str, dict[str, Run]]] = {}
        local_jobs: list[tuple[str, Callable[[Case], dict[str, Run]], int]] = []
        if "rules" in wanted and area.rules is not None:
            outputs["rules"] = run_provider("rules", lambda c, a=area: run_rules(a, c), area.cases, 1, False, area.name)
        if area.name == "tool_routing" and local_ok and "local_cand" in wanted:
            local_jobs.append(("local_cand", lambda c, a=area: run_tool_choice(local, a, c), 2))
        if area.name == "tool_routing" and local_ok:
            if "current" in wanted:
                local_jobs.append(("current", lambda c, a=area: run_current_tool_decision(args.local_url, a, c), 2))
            if "chat" in wanted:
                local_jobs.append(("chat", lambda c, a=area: run_chat(args.local_url, model, a, c), 1))
        if local_ok and "local_decision" in wanted:
            local_jobs.append(("local_decision", lambda c, a=area: run_typed(local, a, c), 2))
        if local_ok and "llm" in wanted:
            local_jobs.append(("llm", lambda c, a=area: run_typed(llm, a, c), 1))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            jev_future = pool.submit(run_provider, "jev", lambda c, a=area: run_typed(jev, a, c), area.cases,
                                     args.jev_workers, not args.quiet, area.name) \
                if jev is not None and "jev" in wanted else None
            for name, fn, workers in local_jobs:  # one at a time: they share the GPU
                outputs[name] = run_provider(name, fn, area.cases, workers, not args.quiet, area.name)
            if jev_future is not None:
                outputs["jev"] = jev_future.result()
            if jev is not None and area.name == "tool_routing" and "jev_cand" in wanted:
                outputs["jev_cand"] = run_provider("jev_cand", lambda c, a=area: run_tool_choice(jev, a, c),
                                                   area.cases, args.jev_workers, not args.quiet, area.name)
        report["areas"][area.name] = area_report(area, outputs)
    report["finished_at"] = time.time()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print("\n".join(summary_lines(report)))
    if skipped:
        print("\nskipped: " + "; ".join(f"{k}: {v}" for k, v in skipped.items()))
    print(f"\nreport: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
