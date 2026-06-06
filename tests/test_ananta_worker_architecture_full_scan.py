from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "agent" / "routes").mkdir(parents=True)
    (repo / "agent" / "services").mkdir(parents=True)
    (repo / "agent" / "models").mkdir(parents=True)
    (repo / "agent" / "config.py").write_text("RAG_ENABLED = True\n", encoding="utf-8")
    (repo / "agent" / "routes" / "snakes.py").write_text("def snake_ask():\n    pass\n", encoding="utf-8")
    (repo / "agent" / "services" / "rag_service.py").write_text("class RagService:\n    pass\n", encoding="utf-8")
    (repo / "agent" / "models" / "context.py").write_text("class ContextBundle:\n    pass\n", encoding="utf-8")
    return repo


def _write_research_context(workdir: Path, refs: list[dict]) -> None:
    rag = workdir / "rag_helper"
    rag.mkdir(parents=True, exist_ok=True)
    payload = {
        "analysis_mode": "architecture_full_scan",
        "retrieval_profile": {
            "profile_id": "codecompass/architecture_full_scan",
            "analysis_mode": "architecture_full_scan",
            "output_intent": "mermaid_component_diagram",
            "coverage_policy": "relation_expanded",
            "summary_policy": "rolling_structured",
            "budgets": {
                "max_batches": 4,
                "files_per_batch": 3,
                "max_ref_chars": 4000,
                "max_total_ref_count": 20,
                "max_summary_chars": 8000,
            },
        },
        "architecture_scope": {"refs": refs},
        "repo_scope_refs": [{"path": "agent/config.py", "role": "config", "score": 0.2}],
        "relation_edges": [
            {"from": "agent/routes/snakes.py", "to": "agent/services/rag_service.py", "relation_type": "calls"}
        ],
    }
    (rag / "research-context.json").write_text(json.dumps(payload), encoding="utf-8")


def test_architecture_planner_prefers_architecture_scope_and_batches(tmp_path):
    from agent.services.architecture_analysis_planner_service import ArchitectureAnalysisPlanner

    refs = [
        {"path": "agent/services/rag_service.py", "role": "service", "score": 0.7},
        {"path": "agent/routes/snakes.py", "role": "entrypoint", "score": 0.6},
        {"path": "agent/services/rag_service.py", "role": "service", "score": 0.7},
        {"path": "agent/models/context.py", "role": "model", "score": 0.4},
    ]
    ctx = {
        "retrieval_profile": {
            "profile_id": "p",
            "analysis_mode": "architecture_full_scan",
            "coverage_policy": "relation_expanded",
            "budgets": {"files_per_batch": 2, "max_batches": 2},
        },
        "architecture_scope": {"refs": refs},
        "repo_scope_refs": [{"path": "fallback.py"}],
    }

    plan = ArchitectureAnalysisPlanner().build_plan(query="architekturdiagramm", research_context=ctx)

    assert plan["schema"] == "architecture_analysis_plan.v1"
    assert plan["coverage_policy"] == "relation_expanded"
    assert plan["coverage"]["planned_refs"] == 3
    assert len(plan["batches"]) == 2
    assert plan["planned_refs"][0]["role"] == "entrypoint"
    assert any(item["reason"] == "duplicate" for item in plan["excluded_refs"])


def test_full_scan_settings_have_safe_defaults():
    from agent.config import settings

    assert settings.ananta_worker_full_scan_enabled is True
    assert 1 <= settings.ananta_worker_full_scan_max_batches <= 64
    assert 1 <= settings.ananta_worker_full_scan_files_per_batch <= 20
    assert 500 <= settings.ananta_worker_full_scan_max_ref_chars <= 40_000
    assert 1000 <= settings.ananta_worker_full_scan_summary_chars <= 80_000


def test_full_scan_worker_writes_plan_summary_progress_and_diagram(tmp_path):
    from agent.common.sgpt import _run_ananta_worker_iterative

    repo = _make_repo(tmp_path)
    workdir = tmp_path / "ws"
    refs = [
        {"path": "agent/routes/snakes.py", "role": "entrypoint", "score": 0.9},
        {"path": "agent/services/rag_service.py", "role": "service", "score": 0.8},
        {"path": "agent/models/context.py", "role": "model", "score": 0.7},
        {"path": "agent/config.py", "role": "config", "score": 0.6},
    ]
    _write_research_context(workdir, refs)
    prompts: list[str] = []

    def fake_run(prompt, **kwargs):
        prompts.append(prompt)
        if "architecture_batch_analysis.v1" in prompt:
            return 0, json.dumps({
                "schema": "architecture_batch_analysis.v1",
                "analyzed_refs": [{"path": "agent/routes/snakes.py"}],
                "components": [{"name": "Snake Route", "source": "agent/routes/snakes.py"}],
                "edges": [{"from": "Snake Route", "to": "RagService", "relation": "calls"}],
                "source_evidence": [{"source": "agent/routes/snakes.py", "source_kind": "file_excerpt", "note": "route"}],
                "confidence": 0.8,
            }), ""
        return 0, "```mermaid\nflowchart TD\nSnakeRoute-->RagService\n```\n\nQuellen: agent/routes/snakes.py, agent/services/rag_service.py", ""

    with (
        patch("agent.common.sgpt._resolve_repo_root", return_value=repo),
        patch("agent.common.sgpt.run_sgpt_command", side_effect=fake_run),
    ):
        rc, out, err = _run_ananta_worker_iterative(
            "erstelle ein Mermaid Architekturdiagramm",
            str(workdir),
            options=[],
            timeout=30,
            model=None,
        )

    rag = workdir / "rag_helper"
    assert rc == 0
    assert "mermaid" in out
    assert (rag / "architecture-plan.json").exists()
    assert (rag / "architecture-progress.json").exists()
    assert (rag / "architecture-summary.json").exists()
    assert (rag / "architecture-diagrams.md").exists()
    summary = json.loads((rag / "architecture-summary.json").read_text(encoding="utf-8"))
    progress = json.loads((rag / "architecture-progress.json").read_text(encoding="utf-8"))
    assert summary["schema"] == "architecture_analysis_summary.v1"
    assert summary["coverage"]["processed_refs"] == 4
    assert progress["processed_refs"] == 4
    assert any("Strukturierte Summary" in prompt for prompt in prompts if "architecture_batch_analysis.v1" not in prompt)


def test_full_scan_resume_skips_processed_batches(tmp_path):
    from agent.common.sgpt import _run_ananta_worker_iterative

    repo = _make_repo(tmp_path)
    workdir = tmp_path / "ws"
    refs = [
        {"path": "agent/routes/snakes.py", "role": "entrypoint", "score": 0.9},
        {"path": "agent/services/rag_service.py", "role": "service", "score": 0.8},
        {"path": "agent/models/context.py", "role": "model", "score": 0.7},
        {"path": "agent/config.py", "role": "config", "score": 0.6},
    ]
    _write_research_context(workdir, refs)
    calls = {"batch": 0}

    def fake_run(prompt, **kwargs):
        if "architecture_batch_analysis.v1" in prompt:
            calls["batch"] += 1
            return 0, json.dumps({"schema": "architecture_batch_analysis.v1", "source_evidence": []}), ""
        return 0, "final", ""

    with (
        patch("agent.common.sgpt._resolve_repo_root", return_value=repo),
        patch("agent.common.sgpt.run_sgpt_command", side_effect=fake_run),
    ):
        _run_ananta_worker_iterative("architekturdiagramm", str(workdir), options=[], timeout=30, model=None)
        first_batch_calls = calls["batch"]
        _run_ananta_worker_iterative("architekturdiagramm", str(workdir), options=[], timeout=30, model=None)

    assert first_batch_calls == 2
    assert calls["batch"] == 2
