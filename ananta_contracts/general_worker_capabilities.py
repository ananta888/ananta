"""Shared semantic capabilities of Ananta's general-purpose Worker runtime.

These capabilities describe work the generic Hub-delegated LLM execution path
can perform.  They do not grant tools, source identities, network access, or
orchestration authority; those remain assignment- and task-scoped contracts.
"""

from __future__ import annotations

GENERAL_PURPOSE_WORKER_CAPABILITIES: tuple[str, ...] = (
    "planning",
    "analysis",
    "research",
    "source_analysis",
    "coding",
    "implementation",
    "review",
    "testing",
    "verification",
)


__all__ = ["GENERAL_PURPOSE_WORKER_CAPABILITIES"]
