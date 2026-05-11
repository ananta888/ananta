"""Minimal safe baseline skills. AWF-T031.

Read-only and proposal-only by default.
No baseline skill requires shell_execute or patch_apply.
"""
from __future__ import annotations

from worker.skills.skill_manifest import SkillManifest

REPO_CONTEXT_REVIEW = SkillManifest(
    id="repo_context_review",
    version="1.0",
    name="Repository Context Review",
    description="Reviews repository context and produces a structured summary of relevant code areas.",
    required_capabilities=["code_read", "summarize"],
    allowed_tools=["read_file", "list_dir"],
    denied_tools=["run_shell", "file_write", "memory_write", "patch_apply"],
    risk_class="low",
    context_requirements=["code_context"],
    output_schema={"kind": "review_artifact"},
    owner="ananta_builtin",
    source="builtin",
)

TEST_FAILURE_TRIAGE = SkillManifest(
    id="test_failure_triage",
    version="1.0",
    name="Test Failure Triage",
    description="Analyzes test failures and produces a structured triage report with root cause analysis.",
    required_capabilities=["code_read", "summarize"],
    allowed_tools=["read_file", "list_dir"],
    denied_tools=["run_shell", "file_write", "memory_write", "patch_apply"],
    risk_class="low",
    context_requirements=["test_output", "code_context"],
    output_schema={"kind": "triage_artifact"},
    owner="ananta_builtin",
    source="builtin",
)

PATCH_PLAN = SkillManifest(
    id="patch_plan",
    version="1.0",
    name="Patch Plan",
    description="Produces a structured patch plan (proposal only) given a task and code context.",
    required_capabilities=["code_read", "patch_propose"],
    allowed_tools=["read_file", "list_dir"],
    denied_tools=["run_shell", "file_write", "memory_write", "patch_apply"],
    risk_class="medium",
    context_requirements=["code_context", "task_description"],
    output_schema={"kind": "patch_plan_artifact"},
    owner="ananta_builtin",
    source="builtin",
)

SECURITY_REVIEW = SkillManifest(
    id="security_review",
    version="1.0",
    name="Security Review",
    description="Reviews code for security issues and produces a structured security review report.",
    required_capabilities=["code_read", "review"],
    allowed_tools=["read_file", "list_dir"],
    denied_tools=["run_shell", "file_write", "memory_write", "patch_apply"],
    risk_class="low",
    context_requirements=["code_context"],
    output_schema={"kind": "security_review_artifact"},
    owner="ananta_builtin",
    source="builtin",
)

RESULT_SUMMARY = SkillManifest(
    id="result_summary",
    version="1.0",
    name="Result Summary",
    description="Produces a structured summary artifact from worker result output.",
    required_capabilities=["summarize"],
    allowed_tools=[],
    denied_tools=["run_shell", "file_write", "memory_write", "patch_apply"],
    risk_class="low",
    context_requirements=[],
    output_schema={"kind": "summary_artifact"},
    owner="ananta_builtin",
    source="builtin",
)

BUILTIN_SKILLS: list[SkillManifest] = [
    REPO_CONTEXT_REVIEW,
    TEST_FAILURE_TRIAGE,
    PATCH_PLAN,
    SECURITY_REVIEW,
    RESULT_SUMMARY,
]


def load_builtin_skills(registry) -> list[str]:
    """Register all builtin skills into a SkillRegistry (disabled by default). AWF-T031."""
    errors: list[str] = []
    for skill in BUILTIN_SKILLS:
        errs = registry.register(skill)
        errors.extend(errs)
    return errors
