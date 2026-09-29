"""Task-scoped propose/execute routing, forwarding, and response assembly.

Compatibility remains in thin :class:`TaskScopedExecutionService` wrappers.

This module is the public entry point of the step orchestration. The
implementation is split by responsibility (SRP) into sibling modules:

- ``_task_scoped_step_policies``: shared guards, cache/vector policy aliases
- ``_task_scoped_dispatch_admission``: Hub dispatch admission of one step
- ``_task_scoped_propose_step``: the propose step
- ``_task_scoped_execute_step``: the execute step and recovery receipts

Every name stays importable from here. Dispatch admission and the admitted
execute runner are injected into the step functions as
:class:`StepOrchestrationPorts` (keyword-only ``step_ports``), defaulting to
the documented ``_task_scoped_forwarding_dependencies`` seam.
"""

from __future__ import annotations

from agent.services._task_scoped_dispatch_admission import (  # noqa: F401
    _admit_task_scoped_dispatch,
)
from agent.services._task_scoped_execute_step import (  # noqa: F401
    _publish_recovery_artifact_receipts,
    _run_execute_step_admitted,
    run_execute_step,
)
from agent.services._task_scoped_forwarding_dependencies import (  # noqa: F401 - public seam
    StepOrchestrationPorts,
)
from agent.services._task_scoped_propose_step import (  # noqa: F401
    _run_propose_step_admitted,
    run_propose_step,
)
from agent.services._task_scoped_step_policies import (  # noqa: F401
    _INTERACTIVE_TERMINAL_FINALIZE_COMMAND,
    _RECOVERY_OUTCOME_CACHE,
    _RECOVERY_OUTCOME_CACHE_LOCK,
    HANDLER_ONLY_TASK_KINDS,
    _apply_request_run_evidence_context,
    _cache_recovery_outcome,
    _cached_recovery_outcome,
    _dispatch_admission_error,
    _handler_only_unavailable,
    _knowledge_index_handler_unavailable,
    _organization_research_hub_execution_guard,
    _recovery_cache_key,
    _vector_index_domain_binding_error,
    _vector_index_handler_unavailable,
)
