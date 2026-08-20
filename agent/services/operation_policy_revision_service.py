from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agent.services.operation_policy_service import OperationPolicyService, get_operation_policy_service


class OperationPolicyRevisionError(ValueError):
    def __init__(self, reason_code: str, *, conflict: bool = False) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.conflict = conflict


@dataclass(frozen=True)
class OperationPolicyRevisionUpdate:
    stored_policy: dict[str, Any]
    previous_revision: int
    revision: int
    previous_hash: str
    policy_hash: str
    diff: dict[str, Any]
    changed: bool
    change_kind: str


class OperationPolicyRevisionService:
    """Builds validated revision envelopes; repositories own atomic persistence."""

    _MAX_HISTORY = 25

    def __init__(self, policy_service: OperationPolicyService) -> None:
        self._policy_service = policy_service

    @staticmethod
    def _actor(actor: str) -> str:
        normalized = str(actor or "unknown").strip()
        return normalized[:160] or "unknown"

    @staticmethod
    def _history(stored: dict[str, Any] | None) -> list[dict[str, Any]]:
        raw = stored.get("_history") if isinstance(stored, dict) else []
        return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    @staticmethod
    def _policy_snapshot(policy: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in policy.items()
            if key not in {"_history", "history", "history_count", "expected_revision"}
        }

    @staticmethod
    def _set_diff(before: dict[str, Any], after: dict[str, Any], field: str) -> dict[str, list[str]]:
        old = set(before.get(field) or [])
        new = set(after.get(field) or [])
        return {f"{field}_added": sorted(new - old), f"{field}_removed": sorted(old - new)}

    def _diff(self, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        diff: dict[str, Any] = {}
        for field in ("allow_operations", "deny_operations", "allow_groups", "deny_groups", "enforced_transports"):
            diff.update(self._set_diff(before, after, field))
        diff["enabled_changed"] = bool(before.get("enabled")) != bool(after.get("enabled"))
        return diff

    @staticmethod
    def _expected_revision(requested: dict[str, Any], current_revision: int, *, current_exists: bool) -> int:
        value = requested.get("expected_revision", requested.get("revision"))
        if value is None and current_exists:
            raise OperationPolicyRevisionError("operation_policy_expected_revision_required", conflict=True)
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise OperationPolicyRevisionError("operation_policy_expected_revision_invalid", conflict=True)
        if value != current_revision:
            raise OperationPolicyRevisionError("operation_policy_revision_conflict", conflict=True)
        return value

    def prepare_update(
        self,
        *,
        current_stored: dict[str, Any] | None,
        requested: dict[str, Any],
        actor: str,
        change_kind: str = "update",
    ) -> OperationPolicyRevisionUpdate:
        current_exists = isinstance(current_stored, dict)
        current = self._policy_service.resolve_policy(
            {"operation_policy": current_stored} if current_exists else {}
        )
        current_revision = int(current.get("revision") or 0)
        self._expected_revision(requested, current_revision, current_exists=current_exists)
        normalized = self._policy_service.normalize_policy(requested)
        normalized["revision"] = current_revision
        before = self._policy_snapshot(current)
        candidate = self._policy_snapshot(normalized)
        previous_hash = str(current.get("policy_hash") or "")
        if candidate.get("policy_hash") == previous_hash:
            stored = dict(current_stored or current)
            return OperationPolicyRevisionUpdate(
                stored, current_revision, current_revision, previous_hash, previous_hash, {}, False, change_kind
            )

        revision = current_revision + 1
        candidate["revision"] = revision
        history = self._history(current_stored)
        history.append(
            {
                "revision": current_revision,
                "policy_hash": previous_hash,
                "policy": before,
                "replaced_by_revision": revision,
                "actor": self._actor(actor),
                "changed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "change_kind": change_kind,
                "diff": self._diff(before, candidate),
            }
        )
        candidate["_history"] = history[-self._MAX_HISTORY :]
        diff = self._diff(before, candidate)
        return OperationPolicyRevisionUpdate(
            candidate,
            current_revision,
            revision,
            previous_hash,
            str(candidate.get("policy_hash") or ""),
            diff,
            True,
            change_kind,
        )

    def prepare_rollback(
        self,
        *,
        current_stored: dict[str, Any] | None,
        target_revision: int,
        expected_revision: int,
        actor: str,
    ) -> OperationPolicyRevisionUpdate:
        if not isinstance(current_stored, dict):
            raise OperationPolicyRevisionError("operation_policy_history_missing")
        if isinstance(target_revision, bool) or not isinstance(target_revision, int) or target_revision < 0:
            raise OperationPolicyRevisionError("operation_policy_target_revision_invalid")
        history = self._history(current_stored)
        target = next((item for item in history if item.get("revision") == target_revision), None)
        if not isinstance(target, dict) or not isinstance(target.get("policy"), dict):
            raise OperationPolicyRevisionError("operation_policy_target_revision_not_found")
        requested = dict(target["policy"])
        requested["expected_revision"] = expected_revision
        requested["revision"] = expected_revision
        update = self.prepare_update(
            current_stored=current_stored,
            requested=requested,
            actor=actor,
            change_kind="rollback",
        )
        if not update.changed:
            raise OperationPolicyRevisionError("operation_policy_target_already_active")
        return update


operation_policy_revision_service = OperationPolicyRevisionService(get_operation_policy_service())


def get_operation_policy_revision_service() -> OperationPolicyRevisionService:
    return operation_policy_revision_service
