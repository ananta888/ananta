"""Agent Skill Profiles (VPDF-005 + VPDF-006).

A SkillProfile describes what an agent node in a visual process graph
can do: which task_kinds it handles, which tools it may use, and what
model preference it has.

VPDF-006: The profile registry is open — external specialty-agent
profiles can be registered at runtime via ``register_profile()``.
"""
from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel


class AgentSkillProfile(BaseModel):
    """Declares an agent's capabilities for use in a visual process step."""
    id: str
    name: str
    description: str = ""
    role: str = "default"
    task_kinds: list[str] = []            # task_kind values this profile handles
    capabilities: list[str] = []          # semantic capability tags
    allowed_tools: list[str] = []         # tool names allowed when this profile is active
    forbidden_tools: list[str] = []
    model_preference: Optional[str] = None
    max_context_tokens: Optional[int] = None
    tags: list[str] = []
    metadata: dict[str, Any] = {}

    def supports_kind(self, task_kind: str) -> bool:
        return not self.task_kinds or task_kind in self.task_kinds

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump()


# ── Built-in profiles ─────────────────────────────────────────────────────────

_BUILTIN_PROFILES: list[AgentSkillProfile] = [
    AgentSkillProfile(
        id="coder",
        name="Code Agent",
        description="Implements code changes, refactors, and bug fixes.",
        role="developer",
        task_kinds=["patch_apply", "patch_propose", "run_tests", "script"],
        capabilities=["write_file", "read_file", "grep_search", "git_diff"],
        allowed_tools=["read_file", "write_file", "grep_search", "git_diff", "git_status", "run_tests"],
        tags=["code", "engineering"],
    ),
    AgentSkillProfile(
        id="analyst",
        name="Analysis Agent",
        description="Reads and analyses code, documents, and data without mutating anything.",
        role="analyst",
        task_kinds=["review", "research_limited", "summarize", "file_check"],
        capabilities=["read_only"],
        allowed_tools=["read_file", "list_files", "grep_search", "git_status", "git_diff", "json_validate"],
        tags=["analysis", "read-only"],
    ),
    AgentSkillProfile(
        id="tester",
        name="Test Agent",
        description="Writes and executes tests.",
        role="qa",
        task_kinds=["run_tests", "script", "file_check", "regex_check"],
        capabilities=["run_tests", "write_file", "read_file"],
        allowed_tools=["read_file", "write_file", "run_tests", "grep_search"],
        tags=["testing", "qa"],
    ),
    AgentSkillProfile(
        id="planner",
        name="Planning Agent",
        description="Creates plans, specs, and task breakdowns. LLM-heavy, read-only file access.",
        role="architect",
        task_kinds=["plan_only", "summarize"],
        capabilities=["llm_generate"],
        allowed_tools=["read_file", "list_files", "grep_search"],
        tags=["planning", "architect"],
    ),
    AgentSkillProfile(
        id="reviewer",
        name="Review Agent",
        description="Reviews code, docs, and outputs for quality and correctness.",
        role="reviewer",
        task_kinds=["review", "summarize", "research_limited"],
        capabilities=["read_only", "llm_generate"],
        allowed_tools=["read_file", "grep_search", "git_diff"],
        tags=["review", "quality"],
    ),
    AgentSkillProfile(
        id="devops",
        name="DevOps Agent",
        description="Handles CI, deployment, and infrastructure tasks.",
        role="devops",
        task_kinds=["shell_execute", "git_op", "script", "run_tests"],
        capabilities=["shell_exec", "run_tests"],
        allowed_tools=["run_tests", "git_status", "git_diff", "read_file"],
        tags=["devops", "infra"],
    ),
    AgentSkillProfile(
        id="ml_engineer",
        name="ML Engineer Agent",
        description="Handles vector encoding, RAG pipelines, and quantization steps.",
        role="ml_engineer",
        task_kinds=[
            "vector_encode", "turboquant_encode", "embed_chunk",
            "rag_retrieve", "rerank", "query_rewrite", "cluster",
        ],
        capabilities=["ml_inference", "vector_operation", "read_only"],
        allowed_tools=["read_file", "list_files", "grep_search"],
        tags=["ml", "embeddings", "retrieval"],
    ),
    AgentSkillProfile(
        id="evolver_agent",
        name="Evolver Agent",
        description="Evolves prompts and project structure using run telemetry and EvolutionService.",
        role="evolver",
        task_kinds=["evolve_prompt", "evolve_project"],
        capabilities=["self_modifying", "llm_generate"],
        allowed_tools=["read_file", "list_files", "grep_search", "git_diff"],
        tags=["evolution", "self-improving"],
    ),
]


# ── Registry (VPDF-006) ───────────────────────────────────────────────────────

class SkillProfileRegistry:
    """Open registry for agent skill profiles.

    Initialized with built-in profiles; specialty agents can register
    additional profiles at runtime.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, AgentSkillProfile] = {}
        for p in _BUILTIN_PROFILES:
            self._profiles[p.id] = p

    def register(self, profile: AgentSkillProfile) -> None:
        self._profiles[profile.id] = profile

    def get(self, profile_id: str) -> Optional[AgentSkillProfile]:
        return self._profiles.get(profile_id)

    def all(self) -> list[AgentSkillProfile]:
        return list(self._profiles.values())

    def for_task_kind(self, task_kind: str) -> list[AgentSkillProfile]:
        return [p for p in self._profiles.values() if p.supports_kind(task_kind)]

    def as_library(self) -> list[dict[str, Any]]:
        """Agent Library format for VPAD-005 panel."""
        return [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "role": p.role,
                "task_kinds": p.task_kinds,
                "tags": p.tags,
            }
            for p in sorted(self._profiles.values(), key=lambda x: x.name)
        ]


# Module-level singleton
_registry: SkillProfileRegistry | None = None


def get_skill_profile_registry() -> SkillProfileRegistry:
    global _registry
    if _registry is None:
        _registry = SkillProfileRegistry()
    return _registry
