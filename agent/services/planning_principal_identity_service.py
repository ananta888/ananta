from __future__ import annotations

from typing import Any

_PRINCIPAL_PREFIX = "principal:"
_WORKER_PREFIX = "worker:"
_MAX_CANONICAL_ID_LENGTH = 191
_NON_PRINCIPAL_APPROVERS = frozenset(
    {
        "auto_policy",
        "goal_pre_approval_policy",
        "policy:not-required",
        "system",
    }
)


def canonical_planning_actor_id(subject_id: str | None) -> str:
    """Return a namespace-safe identity for a human/control-plane actor."""
    return _canonical_id(subject_id, namespace="principal")


def canonical_planning_worker_id(worker_id: str | None) -> str:
    """Return a namespace-safe identity for an execution worker."""
    return _canonical_id(worker_id, namespace="worker")


def planning_revision_creator_id(revision: Any) -> str | None:
    """Resolve an immutable revision creator, including conservative legacy fallbacks.

    New rows carry ``created_by_principal_id``.  Legacy rows can only be
    recovered from provenance fields whose semantics are unambiguous.  A
    display label such as ``hub:proposal:<proposal-id>`` is deliberately not
    interpreted as an actor identity.
    """
    explicit = str(getattr(revision, "created_by_principal_id", None) or "").strip()
    if explicit:
        return _safe_stored_id(explicit)

    provenance = dict(getattr(revision, "execution_provenance", None) or {})
    hub_actor = str(provenance.get("created_by_hub_actor") or "").strip()
    if hub_actor:
        return _safe_canonical_id(hub_actor, namespace="principal")
    worker_id = str(provenance.get("worker_id") or "").strip()
    if worker_id:
        return _safe_canonical_id(worker_id, namespace="worker")

    display_creator = str(getattr(revision, "created_by", None) or "").strip()
    if display_creator.startswith(_WORKER_PREFIX):
        return _safe_canonical_id(display_creator, namespace="worker")
    if display_creator.startswith("hub:replan:"):
        return _safe_canonical_id(
            display_creator.removeprefix("hub:replan:"),
            namespace="principal",
        )
    return None


def planning_approval_decider_id(decided_by: str | None) -> str | None:
    """Canonicalize an approval actor; policy/system labels are not principals."""
    normalized = str(decided_by or "").strip()
    if not normalized or normalized in _NON_PRINCIPAL_APPROVERS:
        return None
    return _safe_stored_id(normalized)


def planning_separation_of_duties_reason(
    *,
    revision: Any,
    decided_by: str | None,
) -> str | None:
    """Return the fail-closed SoD reason, or ``None`` for distinct identities."""
    creator_id = planning_revision_creator_id(revision)
    if creator_id is None:
        return "planning_creator_principal_unverified"
    decider_id = planning_approval_decider_id(decided_by)
    if decider_id is None:
        return "planning_approver_principal_unverified"
    if creator_id == decider_id:
        return "planning_separation_of_duties_required"
    return None


def _normalize_stored_id(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized.startswith((_PRINCIPAL_PREFIX, _WORKER_PREFIX)):
        return _validate_canonical_id(normalized)
    return canonical_planning_actor_id(normalized)


def _safe_stored_id(value: str) -> str | None:
    try:
        return _normalize_stored_id(value)
    except ValueError:
        return None


def _safe_canonical_id(value: str, *, namespace: str) -> str | None:
    try:
        return _canonical_id(value, namespace=namespace)
    except ValueError:
        return None


def _canonical_id(value: str | None, *, namespace: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"planning_{namespace}_id_required")
    prefix = f"{namespace}:"
    if normalized.startswith(prefix):
        return _validate_canonical_id(normalized)
    return _validate_canonical_id(f"{prefix}{normalized}")


def _validate_canonical_id(value: str) -> str:
    if len(value) > _MAX_CANONICAL_ID_LENGTH:
        raise ValueError("planning_principal_id_too_long")
    namespace, separator, identifier = value.partition(":")
    if separator != ":" or namespace not in {"principal", "worker"} or not identifier.strip():
        raise ValueError("planning_principal_id_invalid")
    return value


__all__ = [
    "canonical_planning_actor_id",
    "canonical_planning_worker_id",
    "planning_approval_decider_id",
    "planning_revision_creator_id",
    "planning_separation_of_duties_reason",
]
