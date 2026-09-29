"""Compatibility import path.

The implementation lives in ``agent.models.task_organization_scope``: a dependency-free contract that
repositories may import (agent.models).
"""

from agent.models.task_organization_scope import (  # noqa: F401
    BASE_SCOPE_FIELDS,
    ORGANIZATION_SCOPE_ALL,
    ORGANIZATION_SCOPE_FIELDS,
    goal_reference,
    inherited_organization_scope,
    organization_scope_of,
    parent_reference,
    resolve_ingest_scope,
    states_any_scope,
)
