"""Test helper for the per-application planning-strategy collaborator seam.

Tests replace LLM-planning collaborators for one Flask application through
``install_planning_strategy_collaborators`` instead of monkeypatching
``agent.services.planning_strategies`` module names.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Callable

from agent.services.planning_strategies import (
    PlanningStrategyCollaborators,
    install_planning_strategy_collaborators,
)


def install_repo_context_loader(app: Any, loader: Callable[[str], str | None]) -> None:
    """Use ``loader`` as the planning repo-context loader of ``app``."""

    install_planning_strategy_collaborators(
        app,
        dataclasses.replace(PlanningStrategyCollaborators.default(), repo_context_loader=loader),
    )
