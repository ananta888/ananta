from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _store_path(repo_root: str | Path | None = None) -> Path:
    root = Path(repo_root).resolve() if repo_root else Path.cwd()
    return root / "data" / "imap" / "mail-artifacts.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": "mail_artifacts.v1", "artifacts": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {"schema": "mail_artifacts.v1", "artifacts": []}
    payload.setdefault("schema", "mail_artifacts.v1")
    payload.setdefault("artifacts", [])
    return payload


def _save(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _artifact_ref(message_ref: dict[str, Any], scope: str) -> str:
    account_id = str(message_ref.get("account_id") or "")
    mailbox = str(message_ref.get("mailbox") or "")
    uid = int(message_ref.get("uid") or 0)
    return f"mail://{account_id}/{mailbox}/{uid}?scope={scope}"


def register_mail_artifact(
    *,
    message_ref: dict[str, Any],
    scope: str,
    redaction_status: str,
    policy_decision_ref: str,
    excerpt: str = "",
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    scope_value = str(scope or "metadata_only").strip()
    if scope_value not in {"metadata_only", "excerpt", "full_body", "attachment_ref"}:
        raise ValueError("mail_artifact_scope_invalid")
    ref = dict(message_ref or {})
    account_id = str(ref.get("account_id") or "").strip()
    mailbox = str(ref.get("mailbox") or "").strip()
    uid = int(ref.get("uid") or 0)
    if not account_id or not mailbox or uid <= 0:
        raise ValueError("mail_artifact_message_ref_invalid")
    path = _store_path(repo_root)
    payload = _load(path)
    rows = [dict(item) for item in list(payload.get("artifacts") or []) if isinstance(item, dict)]
    artifact_ref = _artifact_ref(ref, scope_value)
    artifact = {
        "artifact_ref": artifact_ref,
        "artifact_kind": scope_value,
        "message_ref": {
            "account_id": account_id,
            "mailbox": mailbox,
            "uid": uid,
            "message_id": str(ref.get("message_id") or ""),
        },
        "policy_decision_ref": str(policy_decision_ref or ""),
        "redaction_status": str(redaction_status or "not_required"),
        "excerpt": str(excerpt or "") if scope_value in {"excerpt", "full_body", "attachment_ref"} else "",
        "created_at": _now_iso(),
    }
    rows = [row for row in rows if str(row.get("artifact_ref") or "") != artifact_ref]
    rows.append(artifact)
    payload["artifacts"] = rows
    _save(path, payload)
    return artifact


def list_mail_artifacts(*, repo_root: str | Path | None = None) -> list[dict[str, Any]]:
    payload = _load(_store_path(repo_root))
    return [dict(item) for item in list(payload.get("artifacts") or []) if isinstance(item, dict)]


def get_mail_artifact(*, artifact_ref: str, repo_root: str | Path | None = None) -> dict[str, Any] | None:
    target = str(artifact_ref or "").strip()
    for row in list_mail_artifacts(repo_root=repo_root):
        if str(row.get("artifact_ref") or "") == target:
            return dict(row)
    return None
