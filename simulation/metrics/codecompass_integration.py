"""CodeCompass/RAG Integration for Run Analysis (SIM-036).

Indexes simulation run artifacts into CodeCompass so the operator can
ask natural-language questions about simulation runs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _safe_import_codecompass():
    """Returns (resolver_cls, None) or (None, error_str)."""
    try:
        from agent.services.codecompass_candidate_resolver import CodeCompassCandidateResolver
        return CodeCompassCandidateResolver, None
    except ImportError as e:
        return None, str(e)


class SimRunIndexer:
    """Converts simulation run artifacts to RAG-indexable text chunks."""

    def index_run(self, run_dir: str | Path) -> list[dict[str, Any]]:
        """Produce text chunks from a run directory for CodeCompass ingestion."""
        p = Path(run_dir)
        chunks: list[dict[str, Any]] = []

        # Report
        report_path = p / "report.json"
        if report_path.exists():
            report = json.loads(report_path.read_text())
            chunks.append({
                "id": f"sim_report:{p.name}",
                "text": self._report_to_text(report),
                "metadata": {"kind": "sim_report", "run_id": p.name},
            })

        # Events (sample from each tick)
        for events_file in sorted((p / "events").glob("*.jsonl"))[:5]:
            lines = events_file.read_text().splitlines()[:10]
            events = [json.loads(l) for l in lines if l.strip()]
            if events:
                chunks.append({
                    "id": f"sim_events:{p.name}:{events_file.stem}",
                    "text": self._events_to_text(events),
                    "metadata": {"kind": "sim_events", "run_id": p.name,
                                  "file": events_file.name},
                })

        return chunks

    def _report_to_text(self, report: dict[str, Any]) -> str:
        outcome = report.get("outcome", {})
        metrics = report.get("metrics", {})
        lines = [
            f"Simulation Run: {report.get('run_id')} | Scenario: {report.get('scenario_name')}",
            f"Outcome: {outcome.get('category')} ({outcome.get('severity')}): {outcome.get('description')}",
            f"Ticks: {report.get('final_tick')} | Final Living: {metrics.get('final_living')}",
            f"Survival Rate: {metrics.get('survival_rate_pct')}% | Crimes: {metrics.get('total_crimes')}",
            f"Deaths: {metrics.get('total_deaths')} | Tokens Used: {report.get('budget', {}).get('usage', {}).get('tokens')}",
        ]
        return "\n".join(lines)

    def _events_to_text(self, events: list[dict[str, Any]]) -> str:
        return "\n".join(
            f"[tick={e.get('tick')} kind={e.get('kind')}] {e.get('actor_id')}: {e.get('description')}"
            for e in events
        )


class SimRunQueryAdapter:
    """Wraps CodeCompassCandidateResolver to answer questions about sim runs."""

    def __init__(self, run_dir: str | Path | None = None) -> None:
        self._chunks: list[dict[str, Any]] = []
        if run_dir:
            self._chunks = SimRunIndexer().index_run(run_dir)

    def add_run(self, run_dir: str | Path) -> None:
        self._chunks.extend(SimRunIndexer().index_run(run_dir))

    def query(self, question: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Simple keyword search over indexed chunks (no LLM required)."""
        q_lower = question.lower()
        scored = [
            (sum(1 for w in q_lower.split() if w in c["text"].lower()), c)
            for c in self._chunks
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:top_k] if _ > 0]
