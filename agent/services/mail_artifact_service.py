"""Provider-neutral registry for explicitly released mail artifacts."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from agent.artifacts.artifact_grants import is_grant_active
from agent.artifacts.goal_artifact_service import GoalArtifactService
from agent.services.mail_contract_service import MailMessageRefV2
from agent.services.mail_provider_ports import VerifiedMailContentAccess
from agent.services.mail_migration_journal import MailFileLock

R = TypeVar("R")


def _locked(method: Callable[..., R]) -> Callable[..., R]:
    @wraps(method)
    def wrapped(self: MailArtifactService, *args: Any, **kwargs: Any) -> R:
        with MailFileLock(path=self._lock_path):
            return method(self, *args, **kwargs)

    return wrapped

_SCOPES = frozenset(
    {
        "metadata_only",
        "excerpt",
        "body_excerpt",
        "full_body",
        "attachment_ref",
    }
)


def mail_artifact_ref(mail_ref_id: str, scope: str) -> str:
    message_id = str(mail_ref_id or "").strip()
    release_scope = str(scope or "").strip()
    if not message_id or release_scope not in _SCOPES:
        raise ValueError("mail_artifact_ref_invalid")
    return f"mail://{message_id}?scope={release_scope}"


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


class MailArtifactService:
    def __init__(
        self,
        *,
        store_path: str | Path,
        goal_artifacts: GoalArtifactService | None = None,
    ) -> None:
        self._path = Path(store_path).resolve()
        self._goal_artifacts = goal_artifacts or GoalArtifactService()
        self._lock_path = self._path.with_suffix(f"{self._path.suffix}.lock")

    @property
    def store_path(self) -> Path:
        return self._path

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"schema": "ananta.mail-artifacts.v2", "artifacts": []}
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != "ananta.mail-artifacts.v2"
            or not isinstance(payload.get("artifacts"), list)
        ):
            raise ValueError("mail_artifact_store_invalid")
        return payload

    @_locked
    def register(
        self,
        *,
        message_ref: Mapping[str, Any] | MailMessageRefV2,
        scope: str,
        redaction_status: str,
        policy_decision_ref: str,
        excerpt: str = "",
        content: str = "",
        access: VerifiedMailContentAccess | None = None,
    ) -> dict[str, Any]:
        release_scope = str(scope or "metadata_only").strip()
        if release_scope not in _SCOPES:
            raise ValueError("mail_artifact_scope_invalid")
        ref = (
            message_ref.to_dict()
            if isinstance(message_ref, MailMessageRefV2)
            else dict(message_ref or {})
        )
        mail_ref_id = str(ref.get("mail_ref_id") or "").strip()
        account_id = str(ref.get("account_id") or "").strip()
        if not mail_ref_id or not account_id:
            raise ValueError("mail_artifact_message_ref_invalid")
        policy_ref = str(policy_decision_ref or "").strip()
        if release_scope != "metadata_only" and not policy_ref:
            raise ValueError("mail_artifact_policy_decision_required")
        released_content = str(content or excerpt or "")
        if release_scope != "metadata_only":
            expected_scope = (
                "body_excerpt" if release_scope == "excerpt" else release_scope
            )
            if not isinstance(access, VerifiedMailContentAccess):
                raise PermissionError("verified_mail_content_access_required")
            if (
                access.account_id != account_id
                or access.mail_ref_id != mail_ref_id
                or access.release_scope != expected_scope
                or not access.artifact_ref.startswith(f"mail://{mail_ref_id}")
            ):
                raise PermissionError("mail_content_artifact_mismatch")
            artifact_ref = access.artifact_ref
        else:
            artifact_ref = mail_artifact_ref(mail_ref_id, release_scope)
        artifact = {
            "artifact_ref": artifact_ref,
            "artifact_kind": release_scope,
            "message_ref": {
                "mail_ref_id": mail_ref_id,
                "account_id": account_id,
                "protocol": str(ref.get("protocol") or ""),
            },
            "policy_decision_ref": policy_ref,
            "redaction_status": str(redaction_status or "not_required"),
            "excerpt": (
                released_content
                if release_scope
                in {"excerpt", "body_excerpt", "full_body", "attachment_ref"}
                else ""
            ),
            "created_at": datetime.now(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        payload = self._load()
        rows = [
            dict(item)
            for item in payload["artifacts"]
            if isinstance(item, Mapping)
            and str(item.get("artifact_ref") or "") != artifact_ref
        ]
        rows.append(artifact)
        payload["artifacts"] = rows
        _atomic_write(self._path, payload)
        return artifact

    @_locked
    def get(self, artifact_ref: str) -> dict[str, Any] | None:
        target = str(artifact_ref or "").strip()
        for item in self._load()["artifacts"]:
            if (
                isinstance(item, Mapping)
                and str(item.get("artifact_ref") or "") == target
            ):
                return dict(item)
        return None

    @_locked
    def build_context_envelope(
        self,
        *,
        goal_id: str,
        worker_target: str,
    ) -> dict[str, Any]:
        target = str(worker_target or "").strip() or "local_worker"
        if target == "cloud_worker":
            return {
                "goal_id": goal_id,
                "worker_target": target,
                "allowed": False,
                "reason_code": "mail_context_default_denied_cloud",
                "mail_artifacts": [],
                "mail_source_refs": [],
                "redaction_statuses": [],
            }
        graph = self._goal_artifacts.get_goal_graph(goal_id)
        rows: list[dict[str, Any]] = []
        for grant in graph.get("source_grants") or ():
            if not isinstance(grant, Mapping):
                continue
            active, _reason = is_grant_active(dict(grant))
            if not active:
                continue
            artifact = self.get(str(grant.get("artifact_ref") or ""))
            if artifact is not None:
                rows.append(artifact)
        return {
            "goal_id": goal_id,
            "worker_target": target,
            "allowed": True,
            "reason_code": "mail_context_allowed_local",
            "mail_artifacts": rows,
            "mail_source_refs": [
                str(item["artifact_ref"]) for item in rows
            ],
            "redaction_statuses": [
                str(item.get("redaction_status") or "not_required")
                for item in rows
            ],
        }


__all__ = ["MailArtifactService", "mail_artifact_ref"]
