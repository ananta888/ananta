"""Compatibility facade for the task dependency policy service."""

from agent.services.task_dependency_policy import (
    effective_dependencies,
    followup_exists,
    normalize_depends_on,
    normalize_text,
    validate_dependencies_and_cycles,
    validate_dependency_graph,
)

__all__ = [
    "effective_dependencies",
    "followup_exists",
    "normalize_depends_on",
    "normalize_text",
    "validate_dependencies_and_cycles",
    "validate_dependency_graph",
]
