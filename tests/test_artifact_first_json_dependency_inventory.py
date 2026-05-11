"""AFH-T001: Static inventory test — JSON-from-LLM anti-pattern detector.

Fails if any new authoritative 'Return JSON only' task-completion path is introduced
that drives task graph state directly from model chat output.

Safe patterns (Hub-generated contracts, schema validation) are whitelisted.
Unsafe patterns (json.loads on model chat output driving completed state) are flagged.
"""
from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_SERVICES = REPO_ROOT / "agent" / "services"

# Files where json.loads on model chat is acceptable (Hub-built contracts, schemas, etc.)
SAFE_FILES = {
    "planning_utils.py",          # parse_followup_analysis is now advisory
    "worker_todo_planner_service.py",  # planner LLM disabled by default; proposal artifact wraps output
    "planning_service.py",
    "planning_strategies.py",
    "planning_proposal_service.py",
    "config_service.py",
    "config_read_model_service.py",
    "domain_policy_loader.py",
    "context_schema_registry.py",
    "seed_blueprint_catalog.py",
    "wiki_dump_parser.py",
    "wiki_mediawiki_xml_parser.py",
    "benchmark_job_service.py",
    "hub_benchmark_service.py",
    "result_memory_service.py",
}

# Patterns considered unsafe when they drive authoritative task state
UNSAFE_CALL_PATTERNS = [
    # parse_followup_analysis result used to directly set completed/queued state
    re.compile(r'parse_followup_analysis\b.*task_complete.*(?:status|completed|queue)', re.DOTALL),
]

# Anti-pattern: "Return JSON only" prompt that drives completion
AUTHORITATIVE_JSON_PROMPT_PATTERN = re.compile(
    r'(?:Return JSON only|JSON\s+only\s*\.|respond.*with.*JSON\s+only).*'
    r'(?:task_complete|status.*complete|mark.*complete)',
    re.IGNORECASE | re.DOTALL,
)


def _collect_service_files() -> list[Path]:
    return [
        p for p in AGENT_SERVICES.rglob("*.py")
        if p.is_file() and p.name not in SAFE_FILES and "__pycache__" not in p.parts
    ]


def _get_json_loads_uses(source: str) -> list[tuple[int, str]]:
    """Return (lineno, context) for each json.loads call in source."""
    hits: list[tuple[int, str]] = []
    lines = source.splitlines()
    for i, line in enumerate(lines, start=1):
        if "json.loads(" in line:
            context = "\n".join(lines[max(0, i - 5):i + 5])
            hits.append((i, context))
    return hits


def _is_authoritative_completion_context(context: str) -> bool:
    """Return True if the json.loads context drives task graph state directly."""
    lower = context.lower()
    completion_keywords = ("task_complete", "set.*status.*complete", "update.*task.*status", "mark_completed")
    for kw in completion_keywords:
        if re.search(kw, lower):
            return True
    return False


class TestJsonDependencyInventory:
    def test_no_new_authoritative_json_completion_paths(self) -> None:
        """Fail if a new service directly json.loads model chat to drive task completion state."""
        violations: list[str] = []
        for path in _collect_service_files():
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            hits = _get_json_loads_uses(source)
            for lineno, context in hits:
                if _is_authoritative_completion_context(context):
                    violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")

        if violations:
            detail = "\n".join(violations)
            pytest.fail(
                f"Authoritative JSON-from-LLM completion paths detected. "
                f"Model chat JSON must not drive task completed state directly.\n"
                f"Violations:\n{detail}\n"
                "Fix: wrap output in PlannerProposalArtifact or use artifact-first completion policy.",
            )

    def test_planner_llm_disabled_by_default(self) -> None:
        """Verify planner_llm_enabled defaults to False."""
        planner_path = AGENT_SERVICES / "worker_todo_planner_service.py"
        source = planner_path.read_text(encoding="utf-8")
        assert '"planner_llm_enabled": False' in source or "'planner_llm_enabled': False" in source, (
            "planner_llm_enabled must default to False. "
            "LLM planner refinement must be opt-in, not default."
        )

    def test_parse_followup_analysis_is_advisory(self) -> None:
        """Verify parse_followup_analysis returns advisory=True and task_complete is not bool on parse errors."""
        from agent.services.planning_utils import parse_followup_analysis

        # Malformed JSON — must not return authoritative task_complete=True
        result = parse_followup_analysis("this is just markdown text, no JSON here")
        assert result["advisory"] is True, "parse_followup_analysis must be advisory"
        assert result["parse_error"] is True
        assert result["task_complete"] is None, (
            "task_complete must be None on parse error, not True. "
            "Malformed follow-up JSON must not drive retry loops."
        )

        # Valid JSON — advisory flag must still be set
        import json
        valid = parse_followup_analysis(json.dumps({"task_complete": True, "followup_tasks": []}))
        assert valid["advisory"] is True, "parse_followup_analysis is always advisory"

    def test_planner_does_not_overwrite_tasks_from_llm_output(self) -> None:
        """Planner must not directly set todo_contract tasks from LLM output."""
        planner_path = AGENT_SERVICES / "worker_todo_planner_service.py"
        source = planner_path.read_text(encoding="utf-8")
        # Ensure the old direct-overwrite line is gone
        assert 'todo_contract["todo"]["tasks"] = llm_tasks' not in source, (
            "Direct overwrite of todo_contract tasks from LLM output is forbidden. "
            "LLM output must go through PlannerProposalArtifact."
        )

    def test_extract_json_payload_safe_for_advisory_use(self) -> None:
        """extract_json_payload alone is safe — only becomes unsafe when result drives state."""
        from agent.services.planning_utils import extract_json_payload

        assert extract_json_payload("```json\n{}\n```") == "{}"
        assert extract_json_payload("no json here") is None
        assert extract_json_payload("") is None
