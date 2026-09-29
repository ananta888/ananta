"""Compatibility import path.

The implementation lives in ``agent.models.meet_deadline_bindings``: a dependency-free contract that
repositories may import (agent.models).
"""

from agent.models.meet_deadline_bindings import (  # noqa: F401
    DEADLINE_BINDINGS,
    DeadlineBinding,
    deadline_identifier,
)
