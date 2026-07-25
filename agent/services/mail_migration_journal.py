from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

import fcntl


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class MailMigrationProgress:
    migration_id: str
    status: str
    account_cursor: int = 0
    message_cursor: int = 0
    migrated: int = 0
    skipped: int = 0
    conflicted: int = 0
    failed: int = 0
    matched: int = 0
    ambiguous: int = 0
    unmatched: int = 0
    alias_count: int = 0
    source_hashes: Mapping[str, str] | None = None
    backup_hashes: Mapping[str, str] | None = None
    target_hashes: Mapping[str, str] | None = None


_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_HELD_LOCKS = threading.local()


class MailFileLock:
    """Crash-safe inter-process lock with process-local reentrancy."""

    def __init__(self, *, path: str | Path, timeout_seconds: float = 5.0) -> None:
        self._path = Path(path).resolve()
        self._timeout = max(0.0, float(timeout_seconds))
        self._fd: int | None = None
        self._thread_lock: threading.RLock | None = None
        self._reentrant = False

    def __enter__(self) -> MailFileLock:
        key = str(self._path)
        held = getattr(_HELD_LOCKS, "paths", {})
        if key in held:
            held[key] += 1
            _HELD_LOCKS.paths = held
            self._reentrant = True
            return self
        with _THREAD_LOCKS_GUARD:
            thread_lock = _THREAD_LOCKS.setdefault(key, threading.RLock())
        if not thread_lock.acquire(timeout=self._timeout):
            raise TimeoutError("mail_store_lock_timeout")
        self._thread_lock = thread_lock
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    self._fd = None
                    thread_lock.release()
                    self._thread_lock = None
                    raise TimeoutError("mail_store_lock_timeout")
                time.sleep(0.01)
        held[key] = 1
        _HELD_LOCKS.paths = held
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        key = str(self._path)
        held = getattr(_HELD_LOCKS, "paths", {})
        depth = int(held.get(key, 1))
        if self._reentrant:
            if depth <= 1:
                held.pop(key, None)
            else:
                held[key] = depth - 1
            _HELD_LOCKS.paths = held
            return
        held.pop(key, None)
        _HELD_LOCKS.paths = held
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
        if self._thread_lock is not None:
            self._thread_lock.release()
            self._thread_lock = None


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


class MailMultiFileTransaction:
    """Write-ahead multi-file transaction with rollback and restart recovery."""

    def __init__(
        self,
        *,
        transaction_root: str | Path,
        transaction_id: str,
        replace_target: Callable[[Path, Path], None] | None = None,
    ) -> None:
        self._directory = Path(transaction_root).resolve() / str(transaction_id)
        self._manifest_path = self._directory / "manifest.json"
        self._replace_target = replace_target or self._replace

    @staticmethod
    def _replace(staged: Path, target: Path) -> None:
        os.replace(staged, target)

    def _write_manifest(self, payload: Mapping[str, Any]) -> None:
        _atomic_bytes(
            self._manifest_path,
            (json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )

    def _restore(self, manifest: Mapping[str, Any]) -> None:
        for item in reversed(list(manifest.get("targets") or [])):
            target = Path(str(item["path"]))
            preimage = Path(str(item["preimage"]))
            if bool(item.get("existed")):
                _atomic_bytes(target, preimage.read_bytes())
            else:
                target.unlink(missing_ok=True)

    def recover_if_needed(self) -> bool:
        if not self._manifest_path.exists():
            return False
        manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        if str(manifest.get("state")) not in {"prepared", "committing"}:
            return False
        self._restore(manifest)
        manifest["state"] = "recovered"
        self._write_manifest(manifest)
        return True

    def commit(self, files: Mapping[Path, Mapping[str, Any] | None]) -> Mapping[str, str]:
        import hashlib

        self._directory.mkdir(parents=True, exist_ok=True)
        targets: list[dict[str, Any]] = []
        hashes: dict[str, str] = {}
        for index, (target, payload) in enumerate(sorted(files.items(), key=lambda item: str(item[0]))):
            resolved = Path(target).resolve()
            preimage = self._directory / f"preimage-{index}.bin"
            existed = resolved.exists()
            _atomic_bytes(preimage, resolved.read_bytes() if existed else b"")
            staged = self._directory / f"staged-{index}.json"
            if payload is not None:
                content = (json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                _atomic_bytes(staged, content)
                hashes[str(resolved)] = hashlib.sha256(content).hexdigest()
            targets.append(
                {
                    "path": str(resolved),
                    "preimage": str(preimage),
                    "staged": str(staged),
                    "existed": existed,
                    "delete": payload is None,
                }
            )
        manifest: dict[str, Any] = {
            "schema": "mail_multi_file_transaction.v1",
            "state": "prepared",
            "targets": targets,
        }
        self._write_manifest(manifest)
        manifest["state"] = "committing"
        self._write_manifest(manifest)
        try:
            for item in targets:
                target = Path(item["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                if bool(item["delete"]):
                    target.unlink(missing_ok=True)
                else:
                    self._replace_target(Path(item["staged"]), target)
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            manifest["state"] = "committed"
            self._write_manifest(manifest)
            return hashes
        except BaseException:
            self._restore(manifest)
            manifest["state"] = "rolled_back"
            self._write_manifest(manifest)
            raise


class MailMigrationJournal:
    def __init__(self, *, path: str | Path) -> None:
        self._path = Path(path).resolve()
        self._lock_path = self._path.with_suffix(f"{self._path.suffix}.lock")

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"schema": "mail_migration_journal.v1", "entries": {}}
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != "mail_migration_journal.v1":
            raise ValueError("mail_migration_journal_invalid")
        payload.setdefault("entries", {})
        return payload

    def _save(self, payload: Mapping[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{self._path.name}.", suffix=".tmp", dir=self._path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self._path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise

    def get(self, migration_id: str) -> MailMigrationProgress | None:
        with MailFileLock(path=self._lock_path):
            item = dict(self._load()["entries"].get(str(migration_id)) or {})
        if not item:
            return None
        return MailMigrationProgress(
            migration_id=str(migration_id),
            status=str(item.get("status") or "pending"),
            account_cursor=int(item.get("account_cursor") or 0),
            message_cursor=int(item.get("message_cursor") or 0),
            migrated=int(item.get("migrated") or 0),
            skipped=int(item.get("skipped") or 0),
            conflicted=int(item.get("conflicted") or 0),
            failed=int(item.get("failed") or 0),
            matched=int(item.get("matched") or 0),
            ambiguous=int(item.get("ambiguous") or 0),
            unmatched=int(item.get("unmatched") or 0),
            alias_count=int(item.get("alias_count") or 0),
            source_hashes=dict(item.get("source_hashes") or {}),
            backup_hashes=dict(item.get("backup_hashes") or {}),
            target_hashes=dict(item.get("target_hashes") or {}),
        )

    def save(self, progress: MailMigrationProgress) -> MailMigrationProgress:
        with MailFileLock(path=self._lock_path):
            payload = self._load()
            entries = dict(payload["entries"])
            entries[progress.migration_id] = {
                "status": progress.status,
                "account_cursor": progress.account_cursor,
                "message_cursor": progress.message_cursor,
                "migrated": progress.migrated,
                "skipped": progress.skipped,
                "conflicted": progress.conflicted,
                "failed": progress.failed,
                "matched": progress.matched,
                "ambiguous": progress.ambiguous,
                "unmatched": progress.unmatched,
                "alias_count": progress.alias_count,
                "source_hashes": dict(progress.source_hashes or {}),
                "backup_hashes": dict(progress.backup_hashes or {}),
                "target_hashes": dict(progress.target_hashes or {}),
                "updated_at": _now_iso(),
            }
            payload["entries"] = entries
            self._save(payload)
        return progress
