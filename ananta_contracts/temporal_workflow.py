"""Versioned transport contracts for Ananta's optional Temporal runtime.

The contracts in this module are deliberately free of Temporal SDK, database,
network and hub implementation imports.  They can therefore cross the
hub/Temporal-worker container boundary without making Temporal a control plane.
Only identifiers, signed authorization material and artifact references are
transported; prompts, credentials and artifact payloads do not belong here.

This module is the stable public entry point; the contracts are implemented
in the ``temporal_workflow_*`` sibling modules and re-exported here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ananta_contracts.temporal_workflow_activities import (  # noqa: F401 - public re-export
    StepActivityInput,
    StepActivityResult,
)
from ananta_contracts.temporal_workflow_commands import (  # noqa: F401 - public re-export
    WorkflowCommand,
    WorkflowCommandAuthorityResult,
    WorkflowCommandResult,
    _valid_ed25519_signature,
    _valid_hmac_signature,
    _workflow_command_payload_digest,
    _workflow_command_semantic_payload,
)
from ananta_contracts.temporal_workflow_inputs import (  # noqa: F401 - public re-export
    AnantaWorkflowInput,
    TemporalWorkflowStep,
)
from ananta_contracts.temporal_workflow_primitives import (  # noqa: F401 - public re-export
    _COMMAND_MAX_SAFE_INTEGER,
    _COMMAND_SEMANTIC_PAYLOAD_SCHEMA,
    _COMMAND_SIGNATURE_ALGORITHMS,
    _DIGEST_RE,
    _IDENTIFIER_RE,
    _SAFE_TOKEN_KEYS,
    _SENSITIVE_KEYS,
    ACTIVITY_INPUT_SCHEMA,
    ACTIVITY_RESULT_SCHEMA,
    COMMAND_AUTHORITY_ACTIVITY,
    COMMAND_AUTHORITY_RESULT_SCHEMA,
    COMMAND_RESULT_SCHEMA,
    COMMAND_SCHEMA,
    LEGACY_COMMAND_SCHEMA,
    PROBE_SCHEMA,
    STATUS_SCHEMA,
    WORKFLOW_INPUT_SCHEMA,
    WORKFLOW_STEP_SCHEMA,
    TemporalContractError,
    _bounded_contract_items,
    _bounded_float,
    _bounded_integer,
    _bounded_strings,
    _contains_sensitive_keys,
    _identifier,
    _is_sensitive_key,
    _mapping,
    _workflow_command_numeric_fields,
    redact_mapping,
)
from ananta_contracts.temporal_workflow_references import (  # noqa: F401 - public re-export
    ActivityClass,
    ArtifactReference,
    AuthorizationEnvelopeRef,
    WorkflowCommandType,
    WorkflowPhase,
)


@dataclass(frozen=True)
class WorkflowStatus:
    workflow_id: str
    run_id: str
    status: str
    revision: int
    current_step_id: str
    completed_step_ids: tuple[str, ...]
    retry_budget_remaining: int
    checkpoint_ref: str
    open_gates: tuple[str, ...]
    reason_code: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)
    plan_hash: str = ""
    plan_revision: int = 1
    plan_ref: str = ""
    active_step_ids: tuple[str, ...] = ()
    failed_step_ids: tuple[str, ...] = ()
    schema: str = STATUS_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "status": self.status,
            "revision": self.revision,
            "current_step_id": self.current_step_id,
            "completed_step_ids": list(self.completed_step_ids),
            "retry_budget_remaining": self.retry_budget_remaining,
            "checkpoint_ref": self.checkpoint_ref,
            "open_gates": list(self.open_gates),
            "reason_code": self.reason_code,
            "parameters": redact_mapping(self.parameters),
            "plan_hash": self.plan_hash,
            "plan_revision": self.plan_revision,
            "plan_ref": self.plan_ref,
            "active_step_ids": list(self.active_step_ids),
            "failed_step_ids": list(self.failed_step_ids),
        }


@dataclass(frozen=True)
class ProbeRequest:
    request_id: str
    value: str = "probe"
    schema: str = PROBE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != PROBE_SCHEMA:
            raise TemporalContractError("unsupported_probe_schema", "probe schema is unsupported")
        _identifier(self.request_id, field_name="request_id")
        if len(self.value) > 128 or "\x00" in self.value:
            raise TemporalContractError("invalid_probe_value", "probe value is invalid")


__all__ = [
    "ACTIVITY_INPUT_SCHEMA",
    "ACTIVITY_RESULT_SCHEMA",
    "ActivityClass",
    "AnantaWorkflowInput",
    "ArtifactReference",
    "AuthorizationEnvelopeRef",
    "COMMAND_AUTHORITY_ACTIVITY",
    "COMMAND_AUTHORITY_RESULT_SCHEMA",
    "COMMAND_RESULT_SCHEMA",
    "COMMAND_SCHEMA",
    "LEGACY_COMMAND_SCHEMA",
    "ProbeRequest",
    "STATUS_SCHEMA",
    "StepActivityInput",
    "StepActivityResult",
    "TemporalContractError",
    "TemporalWorkflowStep",
    "WorkflowCommand",
    "WorkflowCommandAuthorityResult",
    "WorkflowCommandResult",
    "WorkflowCommandType",
    "WorkflowPhase",
    "WorkflowStatus",
    "redact_mapping",
]
