"""Errors shared by the framework-neutral workflow runtime contracts."""

from __future__ import annotations

from dataclasses import dataclass


class WorkflowRuntimeError(RuntimeError):
    """Base class for fail-closed workflow runtime failures."""


@dataclass(frozen=True)
class ContractIssue:
    code: str
    path: str = ""
    message: str = ""


class ContractValidationError(ValueError):
    """Raised when a versioned contract is structurally invalid."""

    def __init__(self, *issues: ContractIssue | str):
        normalized = tuple(
            issue if isinstance(issue, ContractIssue) else ContractIssue(code=str(issue))
            for issue in issues
        )
        self.issues = normalized
        super().__init__(", ".join(issue.code for issue in normalized) or "contract_invalid")


class OptimisticConcurrencyError(WorkflowRuntimeError):
    """The supplied expected sequence or revision is stale."""


class FencingTokenError(WorkflowRuntimeError):
    """A stale or unrelated execution owner attempted to write."""


class InvalidTransitionError(WorkflowRuntimeError):
    """A state transition is not legal for the current record."""


class SignatureValidationError(WorkflowRuntimeError):
    """A signed contract failed signature, binding, or freshness checks."""


class UnsupportedSchemaVersion(WorkflowRuntimeError):
    """No deterministic migration path exists for a contract version."""

