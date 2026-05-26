from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.services.imap_contract_service import validate_mail_message_ref


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ImapMetadataStore:
    def __init__(self, *, store_path: str | Path) -> None:
        self._path = Path(store_path).resolve()

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"schema": "imap_metadata_store.v1", "messages": []}
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"schema": "imap_metadata_store.v1", "messages": []}
        payload.setdefault("schema", "imap_metadata_store.v1")
        payload.setdefault("messages", [])
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _key(message_ref: dict[str, Any]) -> str:
        return f"{message_ref.get('account_id')}::{message_ref.get('mailbox')}::{message_ref.get('uid')}"

    def upsert_message(self, *, message_ref: dict[str, Any], header_meta: dict[str, Any]) -> dict[str, Any]:
        issues = validate_mail_message_ref(message_ref)
        if issues:
            raise ValueError(f"mail_message_ref_invalid:{issues[0]['reason_code']}")
        payload = self._load()
        rows = [dict(item) for item in list(payload.get("messages") or []) if isinstance(item, dict)]
        key = self._key(message_ref)
        row = {
            "message_ref": dict(message_ref),
            "header_meta": dict(header_meta or {}),
            "stale": False,
            "body": "",
            "body_scope": "metadata_only",
            "updated_at": _now_iso(),
        }
        replaced = False
        for idx, existing in enumerate(rows):
            existing_ref = dict(existing.get("message_ref") or {})
            if self._key(existing_ref) == key:
                # Preserve existing explicit body unless overwritten later.
                row["body"] = str(existing.get("body") or "")
                row["body_scope"] = str(existing.get("body_scope") or "metadata_only")
                rows[idx] = row
                replaced = True
                break
        if not replaced:
            rows.append(row)
        payload["messages"] = rows
        self._save(payload)
        return row

    def store_body(
        self,
        *,
        account_id: str,
        mailbox: str,
        uid: int,
        body: str,
        release_scope: str,
    ) -> dict[str, Any]:
        scope = str(release_scope or "").strip()
        if scope not in {"body_excerpt", "full_body"}:
            return {"ok": False, "reason_code": "policy_scope_denied"}
        payload = self._load()
        rows = [dict(item) for item in list(payload.get("messages") or []) if isinstance(item, dict)]
        key = f"{account_id}::{mailbox}::{int(uid)}"
        for idx, row in enumerate(rows):
            ref = dict(row.get("message_ref") or {})
            if self._key(ref) == key:
                next_row = dict(row)
                next_row["body"] = str(body or "")
                next_row["body_scope"] = scope
                next_row["updated_at"] = _now_iso()
                rows[idx] = next_row
                payload["messages"] = rows
                self._save(payload)
                return {"ok": True, "reason_code": "stored", "row": next_row}
        return {"ok": False, "reason_code": "message_not_found"}

    def mark_stale(self, *, account_id: str, mailbox: str, uid: int, stale: bool = True) -> dict[str, Any]:
        payload = self._load()
        rows = [dict(item) for item in list(payload.get("messages") or []) if isinstance(item, dict)]
        key = f"{account_id}::{mailbox}::{int(uid)}"
        for idx, row in enumerate(rows):
            ref = dict(row.get("message_ref") or {})
            if self._key(ref) == key:
                next_row = dict(row)
                next_row["stale"] = bool(stale)
                next_row["updated_at"] = _now_iso()
                rows[idx] = next_row
                payload["messages"] = rows
                self._save(payload)
                return next_row
        raise ValueError("imap_message_not_found")

    def get_by_uid(self, *, account_id: str, mailbox: str, uid: int) -> dict[str, Any] | None:
        key = f"{account_id}::{mailbox}::{int(uid)}"
        payload = self._load()
        for row in list(payload.get("messages") or []):
            if not isinstance(row, dict):
                continue
            ref = dict(row.get("message_ref") or {})
            if self._key(ref) == key:
                return dict(row)
        return None

    def get_by_message_id(self, *, message_id: str) -> dict[str, Any] | None:
        payload = self._load()
        for row in list(payload.get("messages") or []):
            if not isinstance(row, dict):
                continue
            ref = dict(row.get("message_ref") or {})
            if str(ref.get("message_id") or "") == str(message_id).strip():
                return dict(row)
        return None

    def list_messages(self) -> list[dict[str, Any]]:
        payload = self._load()
        return [dict(item) for item in list(payload.get("messages") or []) if isinstance(item, dict)]
