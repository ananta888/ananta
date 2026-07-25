"""Hub-owned, bounded polling scheduler for provider-neutral mail sync."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import portalocker

from agent.adapters.mail_metrics_adapter import MailMetricsAdapter
from agent.services.mail_contract_service import MailAccountV2
from agent.services.mail_task_service import MailWorkspaceScope


class MailPollingAccountPort(Protocol):
    def list_accounts(self) -> Sequence[MailAccountV2]: ...


class MailPollingTaskPort(Protocol):
    def poll_accounts(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    def cancel_account(
        self,
        *,
        account_ref: str,
        actor: str,
        operation: str | None = None,
    ) -> int: ...


class MailPollingRuntimePort(Protocol):
    def snapshot(self) -> Any: ...


class MailPollingHealthPort(Protocol):
    def observe(
        self,
        component: str,
        *,
        status: str,
        reason_code: str,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class MailPollingSchedulerConfig:
    enabled: bool = False
    interval_seconds: int = 300
    lease_ttl_seconds: int = 600
    max_accounts_per_tick: int = 20
    workspace_id: str = "repo"
    sync_policy_ref: str = "policy:mail:scheduled-sync:v1"

    def __post_init__(self) -> None:
        if self.interval_seconds < 30 or self.interval_seconds > 86400:
            raise ValueError("mail_polling_interval_invalid")
        if (
            self.lease_ttl_seconds < self.interval_seconds
            or self.lease_ttl_seconds > 172800
        ):
            raise ValueError("mail_polling_lease_ttl_invalid")
        if self.max_accounts_per_tick < 1 or self.max_accounts_per_tick > 100:
            raise ValueError("mail_polling_batch_invalid")
        if not self.workspace_id or not self.sync_policy_ref:
            raise ValueError("mail_polling_context_invalid")

    @classmethod
    def from_environment(
        cls,
        source: Mapping[str, str] | None = None,
    ) -> "MailPollingSchedulerConfig":
        values = os.environ if source is None else source

        def integer(name: str, default: int) -> int:
            try:
                return int(str(values.get(name) or default))
            except ValueError as exc:
                raise ValueError(f"{name.lower()}_invalid") from exc

        enabled = str(
            values.get("ANANTA_MAIL_POLLING_ENABLED") or ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        interval = integer("ANANTA_MAIL_POLLING_INTERVAL_SECONDS", 300)
        return cls(
            enabled=enabled,
            interval_seconds=interval,
            lease_ttl_seconds=integer(
                "ANANTA_MAIL_POLLING_LEASE_TTL_SECONDS",
                max(600, interval * 2),
            ),
            max_accounts_per_tick=integer(
                "ANANTA_MAIL_POLLING_MAX_ACCOUNTS",
                20,
            ),
            workspace_id=str(
                values.get("ANANTA_MAIL_POLLING_WORKSPACE_ID") or "repo"
            ).strip(),
            sync_policy_ref=str(
                values.get("ANANTA_MAIL_POLLING_POLICY_REF")
                or "policy:mail:scheduled-sync:v1"
            ).strip(),
        )


class PersistentMailPollingLease:
    """Small crash-safe leader lease for exactly one effective Hub poller."""

    _SCHEMA = "ananta.mail-polling-lease.v1"

    def __init__(
        self,
        *,
        path: str | Path,
        lock_timeout_seconds: float = 5.0,
    ) -> None:
        self._path = Path(path).resolve()
        self._lock_path = self._path.with_suffix(f"{self._path.suffix}.lock")
        self._lock_timeout = max(0.1, float(lock_timeout_seconds))

    def claim(
        self,
        *,
        owner_id: str,
        now: float,
        ttl_seconds: int,
    ) -> bool:
        with self._locked():
            payload = self._load()
            lease = dict(payload.get("lease") or {})
            if (
                float(lease.get("expires_at") or 0.0) > now
                and str(lease.get("owner_id") or "") != owner_id
            ):
                return False
            payload["lease"] = {
                "owner_id": owner_id,
                "expires_at": now + int(ttl_seconds),
                "fencing_token": int(lease.get("fencing_token") or 0) + (
                    0
                    if str(lease.get("owner_id") or "") == owner_id
                    else 1
                ),
            }
            self._save(payload)
            return True

    def finish(
        self,
        *,
        owner_id: str,
        now: float,
        result: Mapping[str, Any],
    ) -> None:
        with self._locked():
            payload = self._load()
            lease = dict(payload.get("lease") or {})
            if str(lease.get("owner_id") or "") != owner_id:
                return
            payload["last_run"] = {
                "at": float(now),
                "result": {
                    str(key): value
                    for key, value in result.items()
                    if isinstance(value, (bool, int, float, str))
                },
            }
            self._save(payload)

    def release(self, *, owner_id: str, now: float) -> None:
        with self._locked():
            payload = self._load()
            lease = dict(payload.get("lease") or {})
            if str(lease.get("owner_id") or "") != owner_id:
                return
            lease["expires_at"] = float(now)
            lease["released_at"] = float(now)
            payload["lease"] = lease
            self._save(payload)

    def snapshot(self) -> dict[str, Any]:
        with self._locked():
            return json.loads(json.dumps(self._load(), ensure_ascii=True))

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"schema": self._SCHEMA, "lease": {}, "last_run": {}}
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != self._SCHEMA:
            raise ValueError("mail_polling_lease_store_invalid")
        return payload

    def _save(self, payload: Mapping[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.",
            suffix=".tmp",
            dir=self._path.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    dict(payload),
                    handle,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
        finally:
            temporary.unlink(missing_ok=True)

    def _locked(self) -> Any:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        return portalocker.Lock(
            str(self._lock_path),
            mode="a+",
            timeout=self._lock_timeout,
            flags=portalocker.LOCK_EX | portalocker.LOCK_NB,
        )


class MailPollingScheduler:
    def __init__(
        self,
        *,
        accounts: MailPollingAccountPort,
        tasks: MailPollingTaskPort,
        runtime: MailPollingRuntimePort,
        health: MailPollingHealthPort,
        lease: PersistentMailPollingLease,
        config: MailPollingSchedulerConfig,
        circuit_breaker: Any,
        metrics: MailMetricsAdapter | None = None,
        owner_id: str | None = None,
        clock: Any = time.time,
    ) -> None:
        self._accounts = accounts
        self._tasks = tasks
        self._runtime = runtime
        self._health = health
        self._lease = lease
        self._config = config
        self._circuit = circuit_breaker
        self._metrics = metrics or MailMetricsAdapter()
        self._owner_id = owner_id or f"hub-mail-poller:{uuid.uuid4().hex}"
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    def start(self) -> None:
        if not self._config.enabled:
            self._health.observe(
                "sync",
                status="disabled",
                reason_code="mail_polling_disabled",
            )
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="mail-hub-polling-scheduler",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, join_timeout: float = 5.0) -> None:
        with self._lock:
            thread = self._thread
            self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(join_timeout)))
        self._lease.release(
            owner_id=self._owner_id,
            now=float(self._clock()),
        )
        with self._lock:
            self._thread = None

    def run_once(self) -> dict[str, Any]:
        now = float(self._clock())
        if not self._config.enabled:
            return {
                "status": "disabled",
                "claimed": 0,
                "queued": 0,
                "cancelled": 0,
            }
        if not self._lease.claim(
            owner_id=self._owner_id,
            now=now,
            ttl_seconds=self._config.lease_ttl_seconds,
        ):
            return {
                "status": "standby",
                "claimed": 0,
                "queued": 0,
                "cancelled": 0,
            }
        accounts = sorted(
            self._accounts.list_accounts(),
            key=lambda item: item.account_id,
        )[: self._config.max_accounts_per_tick]
        snapshot = self._runtime.snapshot()
        cancelled = 0
        if not bool(getattr(snapshot, "network_enabled", False)):
            for account in accounts:
                cancelled += self._tasks.cancel_account(
                    account_ref=f"mail-account:{account.account_id}",
                    operation="sync",
                    actor="mail-polling-scheduler",
                )
            result = {
                "status": "offline",
                "claimed": 1,
                "queued": 0,
                "cancelled": cancelled,
            }
            self._health.observe(
                "sync",
                status="disabled",
                reason_code="mail_runtime_network_disabled",
            )
            self._metrics.record_call(
                provider="none",
                operation="sync",
                outcome="blocked",
                error_class="policy",
                duration_seconds=0.0,
            )
            self._lease.finish(owner_id=self._owner_id, now=now, result=result)
            return result

        eligible: list[str] = []
        for account in accounts:
            account_ref = f"mail-account:{account.account_id}"
            if not account.enabled:
                cancelled += self._tasks.cancel_account(
                    account_ref=account_ref,
                    actor="mail-polling-scheduler",
                )
                self._circuit.close_account(account.account_id)
                continue
            if (
                account.sync_policy == "manual"
                or account.effective_protocol not in {"jmap", "imap"}
            ):
                continue
            eligible.append(account_ref)
        started = time.monotonic()
        queued = self._tasks.poll_accounts(
            account_refs=eligible,
            workspace_scope=MailWorkspaceScope(
                workspace_id=self._config.workspace_id
            ),
            sync_policy_ref=self._config.sync_policy_ref,
            actor="mail-polling-scheduler",
            interval_seconds=self._config.interval_seconds,
            max_tasks=self._config.max_accounts_per_tick,
        )
        result = {
            "status": "scheduled",
            "claimed": 1,
            "eligible": len(eligible),
            "queued": len(queued),
            "cancelled": cancelled,
        }
        self._health.observe(
            "sync",
            status="ok",
            reason_code="mail_polling_scheduled",
        )
        self._metrics.record_call(
            provider="none",
            operation="sync",
            outcome="success",
            duration_seconds=time.monotonic() - started,
        )
        self._lease.finish(owner_id=self._owner_id, now=now, result=result)
        return result

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                self._health.observe(
                    "sync",
                    status="degraded",
                    reason_code="mail_polling_tick_failed",
                )
            self._stop.wait(self._config.interval_seconds)


__all__ = [
    "MailPollingScheduler",
    "MailPollingSchedulerConfig",
    "PersistentMailPollingLease",
]
