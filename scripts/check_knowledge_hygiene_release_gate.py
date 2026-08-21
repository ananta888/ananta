#!/usr/bin/env python3
"""Fail-closed release gate for the Knowledge Hygiene track."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark.knowledge_hygiene import DEFAULT_FIXTURE, run_benchmark


SCHEMA_DIR = ROOT / "schemas/knowledge_hygiene"
TODO = ROOT / "todos/archiv/todo.knowledge-hygiene-curated-markdown-wiki-conflict-resolution.json"
OUTPUT = ROOT / "artifacts/test-gates/knowledge-hygiene-release.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    schema_files = sorted(SCHEMA_DIR.glob("*.v1.json"))
    schema_hashes: dict[str, str] = {}
    for path in schema_files:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        if schema.get("additionalProperties") is not False:
            raise SystemExit(f"non-strict top-level schema: {path}")
        schema_hashes[path.name] = _sha(path)
    required = {
        "knowledge_claim.v1.json",
        "knowledge_conflict.v1.json",
        "knowledge_conflict_decision.v1.json",
        "curated_wiki_page.v1.json",
        "knowledge_hygiene_run.v1.json",
        "knowledge_health_snapshot.v1.json",
    }
    if not required.issubset(schema_hashes):
        raise SystemExit("required Knowledge Hygiene schemas missing")

    todo = json.loads(TODO.read_text(encoding="utf-8"))
    tasks = list(todo.get("tasks") or [])
    milestones = list(todo.get("milestones") or [])
    if todo.get("status") not in {"done", "completed"}:
        raise SystemExit("Knowledge Hygiene TODO is not complete")
    if not tasks or any(item.get("status") not in {"done", "completed"} for item in tasks):
        raise SystemExit("Knowledge Hygiene TODO contains open tasks")
    if any(item.get("status") not in {"done", "completed"} for item in milestones):
        raise SystemExit("Knowledge Hygiene TODO contains open milestones")

    benchmark = run_benchmark(DEFAULT_FIXTURE)
    if benchmark["status"] != "passed":
        raise SystemExit("Knowledge Hygiene benchmark failed")

    config_source = (ROOT / "agent/config_defaults.py").read_text(encoding="utf-8")
    writeback_source = (ROOT / "agent/services/knowledge_hygiene/writeback.py").read_text(encoding="utf-8")
    worker_source = (ROOT / "worker/knowledge_hygiene/handlers.py").read_text(encoding="utf-8")
    route_source = (ROOT / "agent/routes/knowledge_hygiene.py").read_text(encoding="utf-8")
    ui_source = (ROOT / "frontend-angular/src/app/features/knowledge-hygiene/knowledge-hygiene-page.component.html").read_text(encoding="utf-8")
    static_checks = {
        "default_disabled": '"enabled": False' in config_source and '"mode": "disabled"' in config_source,
        "writeback_separate_default": '"source_writeback_enabled": False' in config_source,
        "writeback_root_and_symlink_guards": "outside_allowed_roots" in writeback_source and "symlink_writeback_forbidden" in writeback_source,
        "writeback_hash_race_guard": "source_revision_race" in writeback_source and "portalocker.Lock" in writeback_source,
        "worker_has_no_queue_or_followup": all(token not in worker_source for token in ("task_queue", "spawn_worker", "followup_task", "enqueue_task")),
        "api_has_project_and_idempotency_binding": "_project_access" in route_source and "Idempotency-Key" in route_source,
        "ui_exposes_coverage_and_both_sides": "Interpretationsgrenze" in ui_source and "LINKS" in ui_source and "RECHTS" in ui_source,
    }
    if not all(static_checks.values()):
        failed = sorted(key for key, value in static_checks.items() if not value)
        raise SystemExit("static release checks failed: " + ", ".join(failed))

    source_files = [
        ROOT / "ananta_contracts/knowledge_hygiene.py",
        ROOT / "ananta_contracts/knowledge_claim_precedence.py",
        ROOT / "agent/services/knowledge_hygiene/service.py",
        ROOT / "agent/services/knowledge_hygiene/analysis.py",
        ROOT / "worker/knowledge_hygiene/handlers.py",
    ]
    report = {
        "schema": "knowledge_hygiene_release_gate.v1",
        "status": "passed",
        "track": "knowledge-hygiene-curated-markdown-wiki-conflict-resolution",
        "implementation_commit": "containing_git_commit",
        "configuration": {
            "default_mode": "disabled",
            "auto_run": False,
            "source_writeback": False,
            "semantic_profile": "deterministic_offline_profile",
        },
        "fixture": {
            "path": str(DEFAULT_FIXTURE.relative_to(ROOT)),
            "sha256": _sha(DEFAULT_FIXTURE),
            "profile": benchmark["profile"],
        },
        "benchmark": {
            "status": benchmark["status"],
            "precision": benchmark["metrics"]["precision"],
            "recall": benchmark["metrics"]["recall"],
            "f1": benchmark["metrics"]["f1"],
            "false_positive_rate": benchmark["metrics"]["false_positive_rate"],
            "coverage": benchmark["metrics"]["coverage"],
            "latency_slo_seconds": benchmark["thresholds"]["latency_seconds_max"],
            "latency_slo_passed": True,
            "provider": None,
            "tokens": None,
        },
        "limits": {
            "claims_per_run": 10000,
            "candidate_pairs": 50000,
            "pages_per_run": 500,
            "patch_bytes": 1000000,
            "api_page": 500,
        },
        "checks": static_checks,
        "schema_hashes": schema_hashes,
        "source_hashes": {str(path.relative_to(ROOT)): _sha(path) for path in source_files},
        "todo": {"tasks": len(tasks), "milestones": len(milestones), "all_done": True},
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
