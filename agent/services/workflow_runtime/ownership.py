"""Hub-owned run/step leases, fencing, acknowledgements, and retry budgets.

Every worker/runtime claim receives a unique attempt ID and monotonically
increasing fencing token. Tool/native runtimes pass that fencing token to the
side-effect ledger and checkpoint store. Stale heartbeats, results, failures, and
side-effect completions fail closed.

The retry budget is shared across categories (hub task, runtime, tool, provider,
or Temporal activity). ``retry_id`` makes delivery idempotent while ``used`` is a
single combined counter, preventing nested runtimes from multiplying retries.

This module is the stable public entry point; the implementation lives in the
``ownership_*`` sibling modules and is re-exported here unchanged.
"""

from __future__ import annotations

from agent.services.workflow_runtime.ownership_in_memory_store import (  # noqa: F401 - public re-export
    InMemoryExecutionOwnershipStore,
)
from agent.services.workflow_runtime.ownership_records import (  # noqa: F401 - public re-export
    ExecutionOwnership,
    ExecutionOwnershipStore,
    OwnershipClaim,
    RetryBudgetOwner,
    RetryBudgetSnapshot,
    _assert_expected_revision,
    _assert_owner,
    _assert_owner_from_values,
    _exact_optional_ownership,
    _heartbeat,
    _timestamp,
    _validate_lease,
    ownership_event,
)
from agent.services.workflow_runtime.ownership_sqlite_schema import (  # noqa: F401 - public re-export
    _DIRECT_OWNERSHIP_PRIMARY_KEYS,
    _DIRECT_OWNERSHIP_TABLE_COLUMNS,
    _DIRECT_OWNERSHIP_TABLE_TYPES,
    _DIRECT_RESERVATION_CHECK_TERMS,
    _DIRECT_RESERVATION_INDEXES,
    _DIRECT_RESERVATION_UNIQUES,
    _assert_direct_ownership_table,
    _assert_direct_transition_reservation_schema,
    _direct_check_term,
    _direct_exact_current,
    _direct_exact_history,
    _direct_named_check_expression,
    _direct_transition_reservation_receipt,
    _direct_transition_reservation_receipt_values,
    _preflight_direct_ownership_schema,
)
from agent.services.workflow_runtime.ownership_sqlite_store import (  # noqa: F401 - public re-export
    SQLiteExecutionOwnershipStore,
)
from agent.services.workflow_runtime.ownership_transition_errors import (  # noqa: F401 - public re-export
    WorkflowTransitionOwnershipReservationConflict,
    WorkflowTransitionOwnershipReservationError,
    WorkflowTransitionOwnershipReservationHeld,
    WorkflowTransitionOwnershipReservationStale,
    WorkflowTransitionOwnershipReservationUnavailable,
)
from agent.services.workflow_runtime.ownership_transition_identity import (  # noqa: F401 - public re-export
    _ownership_observation_digest,
    workflow_transition_ownership_attempt_id,
    workflow_transition_ownership_intent_digest,
    workflow_transition_ownership_operation_fence_id,
    workflow_transition_ownership_owner_id,
    workflow_transition_ownership_receipt_digest,
    workflow_transition_ownership_receipt_id,
    workflow_transition_ownership_record_digest,
)
from agent.services.workflow_runtime.ownership_transition_projection import (  # noqa: F401 - public re-export
    _transition_ownership_evidence,
    _transition_ownership_observation,
    _transition_ownership_relevant_receipts,
    _transition_ownership_reservation_values,
)
from agent.services.workflow_runtime.ownership_transition_reservation import (  # noqa: F401 - public re-export
    WorkflowTransitionOwnershipReservationCommitPort,
    WorkflowTransitionOwnershipReservationEvidence,
    WorkflowTransitionOwnershipReservationHistoricalReadPort,
    WorkflowTransitionOwnershipReservationIntent,
    WorkflowTransitionOwnershipReservationObservation,
    WorkflowTransitionOwnershipReservationReadPort,
    WorkflowTransitionOwnershipReservationReceipt,
    WorkflowTransitionOwnershipRetryConsumption,
    workflow_transition_ownership_intent_from_mapping,
)
from agent.services.workflow_runtime.ownership_values import (  # noqa: F401 - public re-export
    _OWNERSHIP_ID_RE,
    _OWNERSHIP_MAX_COUNTER,
    _OWNERSHIP_MAX_LEGACY_REVISION,
    _OWNERSHIP_MAX_RETRIES,
    _OWNERSHIP_RETRY_CATEGORY,
    _OWNERSHIP_SHA256_RE,
    EXECUTION_OWNERSHIP_SCHEMA,
    OWNERSHIP_STATUSES,
    RETRY_BUDGET_SCHEMA,
    WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_INTENT_SCHEMA,
    WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_OBSERVATION_SCHEMA,
    WORKFLOW_TRANSITION_OWNERSHIP_RESERVATION_RECEIPT_SCHEMA,
    _ownership_exact_positive_float,
    _ownership_exact_schema,
    _ownership_exact_timestamp,
    _ownership_finite_timestamp,
    _ownership_identity,
    _ownership_legacy_text,
    _ownership_namespaced_digest,
    _ownership_non_negative_integer,
    _ownership_opaque_id,
    _ownership_positive_integer,
    _ownership_positive_legacy_counter,
    _ownership_retry_maximum,
    _ownership_sha256,
    _ownership_status,
)


