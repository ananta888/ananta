#!/usr/bin/env python3
"""Live end-to-end benchmark of the long-context strategies (LCTX-010).

Real Ananta material (docs and source files) is turned into tasks larger than the
32k window; the live Hub decides and acts (``context_strategy.mode = active`` for
the run, restored afterwards) and the autopilot processes the tasks through
real workers. Per case: the decided strategy, split steps, wall time, whether it
completed, the size of the result and the truncations the Hub recorded.

    python3 scripts/long_context_e2e.py --hub http://localhost:5000 --token-file <file> \\
        --out data/decision-benchmarks/long-context-e2e.json

The token is read from the file and never printed. Cases: ``ordered`` (docs to
summarize, expected sequential), ``parts`` (source files, map_reduce),
``compact`` (slightly too large), ``corpus`` (a question over a large body:
retrieve). Evaluation data only.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CHARS_PER_TOKEN = 4
WINDOW_TOKENS = 32768


class Hub:
    def __init__(self, base: str, token: str) -> None:
        self._base = base.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def call(self, method: str, path: str, body: Any = None, timeout: float = 60) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self._base + path, data=data, method=method, headers=self._headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"{method} {path}: HTTP {error.code} {error.read()[:300]!r}") from None
        return payload.get("data", payload) if isinstance(payload, dict) else payload


def _read(paths: list[Path]) -> list[tuple[str, str]]:
    return [(str(p.relative_to(ROOT)), p.read_text(encoding="utf-8", errors="ignore")) for p in paths if p.is_file()]


def _take(files: list[tuple[str, str]], tokens: int) -> list[tuple[str, str]]:
    taken, used = [], 0
    for name, text in files:
        if used >= tokens:
            break
        taken.append((name, text))
        used += len(text) // CHARS_PER_TOKEN
    return taken


def cases(scale: float) -> list[dict[str, Any]]:
    docs = _read(sorted((ROOT / "docs").glob("*.md")))
    sources = _read(sorted((ROOT / "agent/services").glob("context_*.py")) + sorted(
        (ROOT / "agent/services").glob("long_context_*.py")) + sorted((ROOT / "agent/cli_backends").glob("*.py")))
    ordered = "\n\n".join(f"# {name}\n{text}" for name, text in _take(docs, int(WINDOW_TOKENS * 2.5 * scale)))
    compact = "\n\n".join(f"# {name}\n{text}" for name, text in _take(docs[40:], int(WINDOW_TOKENS * 1.15)))
    corpus = "\n\n".join(f"# {name}\n{text}" for name, text in _take(docs[10:], int(WINDOW_TOKENS * 4 * scale)))
    parts = [{"id": name, "text": text} for name, text in _take(sources, int(WINDOW_TOKENS * 2.2 * scale))]
    return [
        {"case": "ordered", "expected": "sequential", "title": "Architektur-Dokumente zusammenfassen",
         "description": ordered, "context": {"context_input_kind": "ordered", "context_goal":
                                            "Fasse die folgenden Ananta-Dokumente zu einer Architekturübersicht "
                                            "in höchstens 20 Stichpunkten zusammen."}},
        {"case": "parts", "expected": "map_reduce", "title": "Öffentliche Funktionen je Datei",
         "description": "Liste für jede Datei die öffentlichen Funktionen und Klassen mit je einem Satz Zweck.",
         "context": {"context_parts": parts}},
        {"case": "compact", "expected": "compact", "title": "Doku-Abschnitt prüfen",
         "description": compact, "context": {"context_input_kind": "ordered", "context_goal":
                                            "Nenne die drei wichtigsten Konfigurationsschalter aus diesem Material."}},
        {"case": "corpus", "expected": "retrieve", "title": "Wo wird das Kontextfenster konfiguriert?",
         "description": corpus, "context": {"context_input_kind": "corpus", "context_goal":
                                           "Wo und mit welchem Schalter wird das 32k-Kontextfenster konfiguriert?"}},
    ]


_EVENTS = {"long_context_split": "split", "long_context_externalized": "externalized",
           "long_context_escalated": "escalated", "long_context_completed": "completed"}


def _details(task: dict[str, Any]) -> dict[str, Any]:
    """The long-context handling from the task's history (the read API does not expose status_reason_details)."""
    found: dict[str, Any] = {}
    for event in task.get("history") or []:
        kind = _EVENTS.get(str(event.get("event_type") or ""))
        if kind:
            details = dict(event.get("details") or event.get("event_details") or {})
            found.setdefault("events", []).append(kind)
            if details.get("strategy"):
                found["strategy"] = details["strategy"]
            if details.get("steps"):
                found["steps"] = int(details["steps"])
    return found


def _sources(case: dict[str, Any]) -> list[str]:
    """The source files of a case's material (for evidence coverage)."""
    parts = [str(part.get("id") or "") for part in case["context"].get("context_parts", [])]
    headings = [line[2:].strip() for line in str(case["description"]).splitlines() if line.startswith("# docs/")]
    return [name for name in parts + headings if name]


def _stored(task_id: str) -> tuple[str | None, dict[str, Any]]:
    """Result and long-context record from the Hub DB when running inside the Hub container.

    The read API omits ``last_output`` and ``status_reason_details`` and strips history details.
    """
    try:
        from sqlalchemy import text

        from agent.database import engine

        with engine.connect() as connection:
            row = connection.execute(text("select last_output, status_reason_details from tasks where id = :id"),
                                     {"id": task_id}).first()
    except Exception:  # noqa: BLE001 -- outside the Hub container: unknown
        return None, {}
    if not row:
        return None, {}
    details = row[1]
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except ValueError:
            details = {}
    record = dict((details or {}).get("long_context") or {}) if isinstance(details, dict) else {}
    return str(row[0] or ""), record


def _truncations(base_urls: list[str], token: str) -> float:
    """Sum of ``context_truncation_total`` over the Hub and worker processes (counters are per process)."""
    total = 0.0
    for base in base_urls:
        request = urllib.request.Request(base.rstrip("/") + "/metrics", headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                text = response.read().decode("utf-8", "replace")
        except (OSError, urllib.error.URLError):
            continue
        total += sum(float(line.rsplit(" ", 1)[1]) for line in text.splitlines()
                     if line.startswith("context_truncation_total{"))
    return total


def _case_stats(task_id: str, sources: list[str], output: str | None) -> dict[str, Any]:
    """Model calls, prompt sizes and evidence coverage of one case, from the Hub DB (in the Hub container)."""
    try:
        from sqlalchemy import text

        from agent.database import engine

        with engine.connect() as connection:
            rows = connection.execute(text("select id, last_proposal from tasks where id = :id or id like :steps"),
                                      {"id": task_id, "steps": f"{task_id}-lc-%"}).all()
    except Exception:  # noqa: BLE001 -- outside the Hub container: unknown
        return {}
    calls, prompts, completions = 0, [], 0
    for _id, proposal in rows:
        data = json.loads(proposal) if isinstance(proposal, str) else (proposal or {})
        for entry in ((data or {}).get("cli_result") or {}).get("llm_call_profile") or []:
            calls += 1
            if entry.get("prompt_tokens"):
                prompts.append(int(entry["prompt_tokens"]))
            completions += int(entry.get("completion_tokens") or 0)
    lowered = (output or "").lower()
    names = [Path(source).stem.lower() for source in sources]
    covered = sum(1 for name in names if name and name in lowered)
    return {"model_calls": calls, "max_prompt_tokens": max(prompts) if prompts else None,
            "total_prompt_tokens": sum(prompts), "completion_tokens": completions,
            "evidence_coverage": round(covered / len(names), 3) if names else None,
            "evidence_sources": len(names)}


def run(hub: Hub, selected: list[str], scale: float, timeout_s: float, tick_s: float,
        context_profile: str | None = None, metrics_urls: list[str] | None = None, token: str = "") -> dict[str, Any]:
    config = hub.call("GET", "/config")
    previous_window = dict(config.get("context_window") or {})
    previous = dict(config.get("context_strategy") or {})
    if previous.get("mode") == "active":
        previous["mode"] = "shadow"  # never "restore" a leftover active mode from an aborted run
    # the autopilot only picks up tasks of its team
    team_id = str((hub.call("GET", "/tasks/autopilot/status") or {}).get("team_id") or "") or None
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # run the finally block (restore) on SIGTERM
    hub.call("POST", "/config", {"context_strategy": {**previous, "mode": "active"}})
    if context_profile:
        hub.call("POST", "/config", {"context_window": {"profile": context_profile}})
    window = hub.call("GET", "/config/context-window")
    window_tokens = int(((window or {}).get("budgets") or {}).get("available_tokens") or (WINDOW_TOKENS - 1024))
    truncations_before = _truncations(metrics_urls or [], token)
    run_id = uuid.uuid4().hex[:8]
    report: dict[str, Any] = {"schema": "ananta.long_context_e2e.v1", "run_id": run_id, "cases": [],
                              "context_window": {"configured": (window or {}).get("configured"),
                                                 "effective": (window or {}).get("effective"),
                                                 "detected_limits": (window or {}).get("detected_limits"),
                                                 "available_tokens": window_tokens}}
    tasks: dict[str, dict[str, Any]] = {}
    try:
        for case in [c for c in cases(scale) if c["case"] in selected]:
            task_id = f"lctx-e2e-{run_id}-{case['case']}"
            hub.call("POST", "/tasks", {"id": task_id, "title": case["title"], "description": case["description"],
                                        "status": "todo", "task_kind": "analysis", "team_id": team_id, "priority": "high",
                                        "worker_execution_context": case["context"]}, timeout=120)
            tasks[task_id] = {**case, "task_id": task_id, "started": time.time(), "sources": _sources(case),
                              "size_tokens": (len(case["description"]) + sum(len(p["text"]) for p in case["context"]
                                              .get("context_parts", []))) // CHARS_PER_TOKEN}
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            try:
                hub.call("POST", "/tasks/autopilot/tick", {}, timeout=600)
            except RuntimeError as error:
                print(f"tick: {error}", file=sys.stderr)
            open_tasks = 0
            for task_id, meta in tasks.items():
                if meta.get("finished"):
                    continue
                task = hub.call("GET", f"/tasks/{task_id}")
                status = str(task.get("status") or "")
                meta["status"] = status
                output, record = _stored(task_id)
                reason = str((record.get("decision") or {}).get("reason") or "")
                meta["long_context"] = {**_details(task), **({"strategy": record["strategy"]} if record.get(
                    "strategy") else {}), **({"steps": len(record["steps"])} if isinstance(record.get("steps"), list)
                                             else {}), **({"decided": reason.split("_as_split", 1)[0]}
                                                          if "_as_split" in reason else {})}
                if status in {"completed", "failed", "paused", "cancelled", "verification_failed"}:
                    meta["finished"] = time.time()
                    meta["output_chars"] = None if output is None else len(output)
                    meta["output_head"] = (output or "")[:400]
                    meta["stats"] = _case_stats(task_id, meta["sources"], output)
                else:
                    open_tasks += 1
            print(json.dumps({t: (m.get("status"), (m.get("long_context") or {}).get("strategy"))
                              for t, m in tasks.items()}), file=sys.stderr, flush=True)
            if not open_tasks:
                break
            time.sleep(tick_s)
    finally:
        hub.call("POST", "/config", {"context_strategy": previous or {"mode": "shadow"}})
        if context_profile:
            hub.call("POST", "/config", {"context_window": previous_window})
    report["truncations"] = _truncations(metrics_urls or [], token) - truncations_before
    for meta in tasks.values():
        lc = meta.get("long_context") or {}
        report["cases"].append({
            "case": meta["case"], "task_id": meta["task_id"], "expected_strategy": meta["expected"],
            "decided": lc.get("decided") or lc.get("strategy"), "strategy": lc.get("strategy"),
            "events": lc.get("events"),
            "size_tokens": meta["size_tokens"], "ratio": round(meta["size_tokens"] / window_tokens, 2),
            "steps": lc.get("steps") or 0, "status": meta.get("status"),
            "seconds": round((meta.get("finished") or time.time()) - meta["started"], 1),
            "output_chars": meta.get("output_chars"), "output_head": meta.get("output_head"),
            **(meta.get("stats") or {})})
    return report


FULL_DEFAULTS = {"cases": "ordered,parts,compact,corpus", "scale": 1.0, "timeout": 3600.0}
# 'ordered' is 2.5 x WINDOW_TOKENS x scale; the split works against the available budget (about half the
# 32k window). 0.3 gives ~24k tokens, ~1.5x that budget: the smallest material that still has to be split.
SMOKE_DEFAULTS = {"cases": "ordered", "scale": 0.3, "timeout": 1200.0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hub", default="http://localhost:5000")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--cases", default=None, help="default: ordered,parts,compact,corpus")
    parser.add_argument("--scale", type=float, default=None, help="material size factor (default 1.0)")
    parser.add_argument("--timeout", type=float, default=None, help="seconds (default 3600)")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="quick end-to-end check of the split chain: only 'ordered' at ~1.5x the available budget (~2 steps "
        "instead of ~10), 20 min timeout; explicit --cases/--scale/--timeout still win",
    )
    parser.add_argument("--tick", type=float, default=20)
    parser.add_argument("--out", type=Path, default=ROOT / "data/decision-benchmarks/long-context-e2e.json")
    parser.add_argument("--context-profile", default=None,
                        help="run under this context profile (standard_32k, full_64k, extended_128k); restored after")
    parser.add_argument("--metrics-urls", default="http://localhost:5000,http://ai-agent-alpha:5000,"
                                                  "http://ai-agent-beta:5000",
                        help="Hub/worker base URLs whose context_truncation_total is summed")
    args = parser.parse_args(argv)
    defaults = SMOKE_DEFAULTS if args.smoke else FULL_DEFAULTS
    args.cases = args.cases if args.cases is not None else defaults["cases"]
    args.scale = args.scale if args.scale is not None else defaults["scale"]
    args.timeout = args.timeout if args.timeout is not None else defaults["timeout"]
    token = args.token_file.read_text(encoding="utf-8").strip()
    hub = Hub(args.hub, token)
    report = run(hub, [c.strip() for c in args.cases.split(",") if c.strip()], args.scale, args.timeout, args.tick,
                 context_profile=args.context_profile, token=token,
                 metrics_urls=[url.strip() for url in args.metrics_urls.split(",") if url.strip()])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    for row in report["cases"]:
        print(f"{row['case']:8s} expected {row['expected_strategy']:10s} decided {str(row['decided']):10s} "
              f"ran {str(row['strategy']):10s} "
              f"{row['size_tokens']:>7} tok ratio {row['ratio']} steps {row['steps']:>2} {row['status']:>10} "
              f"{row['seconds']:>7}s out {row['output_chars']} calls {row.get('model_calls')} "
              f"max_prompt {row.get('max_prompt_tokens')} coverage {row.get('evidence_coverage')}")
    print(f"window: {json.dumps(report.get('context_window'))} truncations: {report.get('truncations')}")
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
