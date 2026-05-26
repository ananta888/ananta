from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agent.services.imap_contract_service import validate_imap_account_config


def _store_path(repo_root: str | Path | None = None) -> Path:
    root = Path(repo_root).resolve() if repo_root else Path.cwd()
    return root / "data" / "imap" / "accounts.json"


def _load_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": "imap_accounts.v1", "accounts": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {"schema": "imap_accounts.v1", "accounts": []}
    accounts = [dict(item) for item in list(payload.get("accounts") or []) if isinstance(item, dict)]
    return {"schema": "imap_accounts.v1", "accounts": accounts}


def _save_store(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _account_id(*, host: str, username_ref: str) -> str:
    digest = hashlib.sha1(f"{host}|{username_ref}".encode("utf-8")).hexdigest()[:10]
    return f"imap-{digest}"


def list_imap_accounts(*, repo_root: str | Path | None = None) -> list[dict[str, Any]]:
    path = _store_path(repo_root)
    payload = _load_store(path)
    return [dict(item) for item in list(payload.get("accounts") or [])]


def create_imap_account(
    *,
    repo_root: str | Path | None = None,
    display_name: str,
    host: str,
    port: int,
    username_ref: str,
    credential_ref: str,
    auth_mode: str = "password_app_token",
    tls_mode: str = "require_tls",
    sync_policy: str = "headers_only",
) -> dict[str, Any]:
    path = _store_path(repo_root)
    payload = _load_store(path)
    accounts = [dict(item) for item in list(payload.get("accounts") or [])]
    account = {
        "account_id": _account_id(host=str(host).strip(), username_ref=str(username_ref).strip()),
        "display_name": str(display_name).strip(),
        "host": str(host).strip(),
        "port": int(port),
        "username_ref": str(username_ref).strip(),
        "credential_ref": str(credential_ref).strip(),
        "auth_mode": str(auth_mode).strip(),
        "tls_mode": str(tls_mode).strip(),
        "sync_policy": str(sync_policy).strip(),
        "enabled": True,
    }
    issues = validate_imap_account_config(account)
    if issues:
        raise ValueError(f"imap_account_invalid:{issues[0]['reason_code']}")
    if any(str(item.get("account_id") or "") == account["account_id"] for item in accounts):
        raise ValueError("imap_account_already_exists")
    accounts.append(account)
    payload["accounts"] = accounts
    _save_store(path, payload)
    return dict(account)


def disable_imap_account(*, account_id: str, repo_root: str | Path | None = None) -> dict[str, Any]:
    path = _store_path(repo_root)
    payload = _load_store(path)
    accounts = [dict(item) for item in list(payload.get("accounts") or [])]
    for account in accounts:
        if str(account.get("account_id") or "") == str(account_id).strip():
            account["enabled"] = False
            payload["accounts"] = accounts
            _save_store(path, payload)
            return dict(account)
    raise ValueError("imap_account_not_found")


def delete_imap_account(*, account_id: str, repo_root: str | Path | None = None) -> dict[str, Any]:
    path = _store_path(repo_root)
    payload = _load_store(path)
    accounts = [dict(item) for item in list(payload.get("accounts") or [])]
    keep: list[dict[str, Any]] = []
    deleted: dict[str, Any] | None = None
    for account in accounts:
        if str(account.get("account_id") or "") == str(account_id).strip():
            deleted = dict(account)
            continue
        keep.append(account)
    if deleted is None:
        raise ValueError("imap_account_not_found")
    payload["accounts"] = keep
    _save_store(path, payload)
    return deleted
