"""Compatibility import path.

The implementation lives in ``agent.models.meet_task_scope``: a dependency-free contract that repositories may
import (agent.models).
"""

from agent.models.meet_task_scope import (  # noqa: F401
    organization_tuple,
)
