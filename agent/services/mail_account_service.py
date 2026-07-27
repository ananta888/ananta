from __future__ import annotations

import json
import os
import tempfile
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from agent.services.mail_account_mapper import MailAccountMapper
from agent.services.mail_contract_service import MAIL_ACCOUNT_STORE_SCHEMA, MailAccountV2
from agent.services.mail_migration_journal import MailFileLock

R = TypeVar("R")


def _locked(method: Callable[..., R]) -> Callable[..., R]:
    @wraps(method)
    def wrapped(self: MailAccountService, *args: Any, **kwargs: Any) -> R:
        with MailFileLock(path=self._lock_path):
            return method(self, *args, **kwargs)

    return wrapped


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
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


class MailAccountService:
    def __init__(self, *, store_path: str | Path) -> None:
        self._path = Path(store_path).resolve()
        self._lock_path = self._path.with_suffix(f"{self._path.suffix}.lock")

    @property
    def store_path(self) -> Path:
        return self._path

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"schema": MAIL_ACCOUNT_STORE_SCHEMA, "accounts": []}
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != MAIL_ACCOUNT_STORE_SCHEMA:
            raise ValueError("mail_account_store_schema_unsupported")
        if not isinstance(payload.get("accounts"), list):
            raise ValueError("mail_account_store_invalid")
        return payload

    def _save(self, accounts: list[MailAccountV2]) -> None:
        _atomic_json_write(
            self._path,
            {"schema": MAIL_ACCOUNT_STORE_SCHEMA, "accounts": [account.to_dict() for account in accounts]},
        )

    @_locked
    def list_accounts(self) -> list[MailAccountV2]:
        return [MailAccountV2.from_mapping(item) for item in self._load()["accounts"] if isinstance(item, dict)]

    @_locked
    def get_account(self, account_id: str) -> MailAccountV2 | None:
        target = str(account_id).strip()
        return next((account for account in self.list_accounts() if account.account_id == target), None)

    @_locked
    def create_account(self, account: MailAccountV2 | Mapping[str, Any]) -> MailAccountV2:
        candidate = account if isinstance(account, MailAccountV2) else MailAccountMapper.normalize(account)
        accounts = self.list_accounts()
        if any(item.account_id == candidate.account_id for item in accounts):
            raise ValueError("mail_account_already_exists")
        accounts.append(candidate)
        self._save(accounts)
        return candidate

    @_locked
    def upsert_account(self, account: MailAccountV2 | Mapping[str, Any]) -> MailAccountV2:
        candidate = account if isinstance(account, MailAccountV2) else MailAccountMapper.normalize(account)
        accounts = self.list_accounts()
        replaced = False
        for index, existing in enumerate(accounts):
            if existing.account_id == candidate.account_id:
                accounts[index] = candidate
                replaced = True
                break
        if not replaced:
            accounts.append(candidate)
        self._save(accounts)
        return candidate

    @_locked
    def disable_account(self, account_id: str) -> MailAccountV2:
        account = self.get_account(account_id)
        if account is None:
            raise ValueError("mail_account_not_found")
        disabled = MailAccountV2(
            account_id=account.account_id,
            display_name=account.display_name,
            requested_protocol=account.requested_protocol,
            resolved_protocol=account.resolved_protocol,
            username_ref=account.username_ref,
            credential_ref=account.credential_ref,
            sync_policy=account.sync_policy,
            enabled=False,
            provider_config=account.provider_config,
        )
        return self.upsert_account(disabled)

    @_locked
    def delete_account(self, account_id: str) -> MailAccountV2:
        account = self.get_account(account_id)
        if account is None:
            raise ValueError("mail_account_not_found")
        if account.enabled:
            raise ValueError("mail_account_disable_required")
        self._save(
            [
                candidate
                for candidate in self.list_accounts()
                if candidate.account_id != account.account_id
            ]
        )
        return account
