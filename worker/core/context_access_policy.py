"""Compatibility facade for the shared context-access policy contract.

New production code imports :mod:`ananta_contracts.context_access_policy`.
This module preserves the existing worker API without making Hub code depend on
worker infrastructure.
"""

from ananta_contracts.context_access_policy import (
    ContextAccessPolicy,
    ContextAccessPolicyEvaluator,
    ContextAccessRule,
    ContextBlockAccessDecision,
    Decision,
    DestinationContext,
    ModelScope,
    ReasonCode,
    RequestedOperation,
    Sensitivity,
    SourceType,
    build_destination_context,
)

__all__ = [
    "ContextAccessPolicy",
    "ContextAccessPolicyEvaluator",
    "ContextAccessRule",
    "ContextBlockAccessDecision",
    "Decision",
    "DestinationContext",
    "ModelScope",
    "ReasonCode",
    "RequestedOperation",
    "Sensitivity",
    "SourceType",
    "build_destination_context",
]
