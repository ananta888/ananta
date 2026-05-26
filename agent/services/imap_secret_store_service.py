from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.services.imap_security_policy_service import redact_mail_content


class InMemoryImapSecretStore:
    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}

    def set_secret(self, *, credential_ref: str, secret: str) -> dict[str, Any]:
        self._secrets[str(credential_ref).strip()] = str(secret)
        return {"ok": True, "reason_code": "stored", "warning_reason_code": ""}

    def get_secret(self, *, credential_ref: str) -> dict[str, Any]:
        key = str(credential_ref).strip()
        if key not in self._secrets:
            return {"ok": False, "reason_code": "secret_not_found", "secret": ""}
        return {"ok": True, "reason_code": "ok", "secret": str(self._secrets[key])}

    def delete_secret(self, *, credential_ref: str) -> dict[str, Any]:
        key = str(credential_ref).strip()
        if key not in self._secrets:
            return {"ok": False, "reason_code": "secret_not_found"}
        self._secrets.pop(key, None)
        return {"ok": True, "reason_code": "deleted"}


class FileFallbackImapSecretStore:
    def __init__(self, *, store_path: str | Path) -> None:
        self._path = Path(store_path).resolve()

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"schema": "imap_secret_store.v1", "storage_mode": "insecure_plaintext", "secrets": {}}
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"schema": "imap_secret_store.v1", "storage_mode": "insecure_plaintext", "secrets": {}}
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def set_secret(self, *, credential_ref: str, secret: str) -> dict[str, Any]:
        payload = self._load()
        secrets = dict(payload.get("secrets") or {})
        key = str(credential_ref).strip()
        secrets[key] = str(secret)
        payload["schema"] = "imap_secret_store.v1"
        payload["storage_mode"] = "insecure_plaintext"
        payload["secrets"] = secrets
        self._save(payload)
        return {"ok": True, "reason_code": "stored", "warning_reason_code": "insecure_fallback_storage"}

    def get_secret(self, *, credential_ref: str) -> dict[str, Any]:
        payload = self._load()
        secrets = dict(payload.get("secrets") or {})
        key = str(credential_ref).strip()
        if key not in secrets:
            return {"ok": False, "reason_code": "secret_not_found", "secret": ""}
        return {
            "ok": True,
            "reason_code": "ok",
            "secret": str(secrets[key]),
            "warning_reason_code": "insecure_fallback_storage",
        }

    def delete_secret(self, *, credential_ref: str) -> dict[str, Any]:
        payload = self._load()
        secrets = dict(payload.get("secrets") or {})
        key = str(credential_ref).strip()
        if key not in secrets:
            return {"ok": False, "reason_code": "secret_not_found"}
        secrets.pop(key, None)
        payload["secrets"] = secrets
        self._save(payload)
        return {"ok": True, "reason_code": "deleted", "warning_reason_code": "insecure_fallback_storage"}


def safe_secret_log_text(raw: str) -> str:
    return str(redact_mail_content(str(raw or "")).get("text") or "")
