"""Domain command modules for the Ananta CLI.

Each module exposes:
  - dispatch(argv: Sequence[str]) -> int   — called by main.py flat dispatch
  - register(subparsers) -> None           — attaches group parser for enumeration/testing
  - SUBCOMMANDS: list[str]                 — leaf command names for help-coverage tests
"""
from __future__ import annotations

from agent.cli.commands import (
    config,
    dev,
    goal,
    hub,
    llm,
    project,
    prompt,
    rag,
    repair,
    runtime,
    share,
    task,
    worker,
)

DOMAIN_MODULES = {
    "config": config,
    "runtime": runtime,
    "llm": llm,
    "hub": hub,
    "worker": worker,
    "goal": goal,
    "task": task,
    "project": project,
    "rag": rag,
    "repair": repair,
    "prompt": prompt,
    "dev": dev,
    "share": share,
}

__all__ = ["DOMAIN_MODULES"]
