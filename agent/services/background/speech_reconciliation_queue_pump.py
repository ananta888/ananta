"""Hub-owned queue pump for bounded offline speech reconciliation.

The pump is intentionally separate from worker execution.  It reads durable
Hub state, evaluates the deployment kill switch and current resource pressure,
selects a healthy configured worker and only then asks the Hub scheduler to
claim and delegate an attempt.
"""

from __future__ import annotations

import http.client
import json
import logging
import math
import os
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Protocol, Sequence

import psutil
from sqlalchemy import func
from sqlmodel import Session, select

from agent.config import settings
from agent.database import engine
from agent.db_models import VoiceLiveRunDB
from agent.db_models.speech_reconciliation import (
    SpeechReconciliationCheckpointDB,
    SpeechReconciliationJobDB,
)
from agent.repositories.speech_reconciliation import SpeechReconciliationRepository
from agent.services.private_container_network_policy import (
    AddressResolver,
    PrivateContainerResolutionError,
    pin_private_container_address,
)
from agent.services.semantic_media_feature_flags import resolve_semantic_media_feature_flags
from agent.services.speech_reconciliation_job_service import job_contract
from agent.services.speech_reconciliation_repository_adapters import (
    RepositorySpeechReconciliationLeasePort,
)
from agent.services.speech_reconciliation_resource_policy import (
    SpeechReconciliationResourcePolicy,
    SpeechReconciliationResourceRequest,
)
from agent.services.speech_reconciliation_scheduler import (
    QueuedSpeechReconciliation,
    SpeechReconciliationDispatchPort,
    SpeechReconciliationLease,
    SpeechReconciliationLeasePort,
    SpeechReconciliationScheduler,
    SpeechReconciliationSchedulingError,
    SpeechReconciliationWorkerCandidate,
)
from agent.services.speech_reconciliation_task_port import HubSpeechReconciliationTaskPort
from agent.services.speech_reconciliation_worker_port import (
    HttpSpeechReconciliationWorkerPort,
    SpeechReconciliationWorkerPort,
    normalize_speech_reconciliation_endpoint,
)
from ananta_contracts.speech_reconciliation import (
    CONTRACT_VERSION,
    NORMAL_MAX_FACTOR,
    SpeechResourceVector,
    canonical_sha256,
)

_EXTENSION_KEY = "speech_reconciliation_queue_pump"
_FINAL_LIVE_STATES = frozenset({"active", "finalizing"})
_DEFAULT_CAPACITY = SpeechResourceVector(
    wall_time_ms=8 * 60 * 60 * 1000,
    cpu_time_ms=160 * 60 * 60 * 1000,
    gpu_time_ms=80 * 60 * 60 * 1000,
    memory_byte_ms=2**63 - 1,
    disk_bytes=512 * 1024**3,
    checkpoint_bytes=64 * 1024**3,
    energy_millijoules=2**63 - 1,
)


class SpeechReconciliationQueuePumpConfigurationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class SpeechReconciliationQueuedJobPort(Protocol):
    def list_queued(
        self,
        *,
        now_ms: int,
        limit: int,
    ) -> Sequence[QueuedSpeechReconciliation]: ...

    def tenant_active_assignments(self) -> Mapping[str, int]: ...


class SpeechReconciliationWorkerDirectoryPort(Protocol):
    def healthy_candidates(self) -> Sequence[SpeechReconciliationWorkerCandidate]: ...


@dataclass(frozen=True, slots=True)
class SpeechReconciliationRuntimePressure:
    live_call_active: bool
    foreground_load_micros: int
    charging: bool
    quiet_hours: bool
    minute_of_day: int


class SpeechReconciliationRuntimePressurePort(Protocol):
    def snapshot(self, *, now_ms: int) -> SpeechReconciliationRuntimePressure: ...


class SpeechReconciliationDispatcherFactoryPort(Protocol):
    """Compose a Hub dispatcher around one authenticated worker transport."""

    def __call__(
        self,
        worker: SpeechReconciliationWorkerPort,
    ) -> SpeechReconciliationDispatchPort: ...


@dataclass(frozen=True, slots=True)
class SpeechReconciliationResourceConfiguration:
    mode: str = "idle_only"
    user_max_factor: int = NORMAL_MAX_FACTOR
    schedule_start_minute: int | None = None
    schedule_end_minute: int | None = None

    @classmethod
    def from_environment(
        cls,
        source: Mapping[str, object],
    ) -> "SpeechReconciliationResourceConfiguration":
        mode = str(source.get("ANANTA_SPEECH_RECONCILIATION_RESOURCE_MODE") or "idle_only")
        mode = mode.strip().casefold().replace("-", "_")
        factor = _environment_int(
            source,
            "ANANTA_SPEECH_RECONCILIATION_USER_MAX_FACTOR",
            NORMAL_MAX_FACTOR,
            minimum=1,
            maximum=NORMAL_MAX_FACTOR,
        )
        start = _optional_environment_int(
            source,
            "ANANTA_SPEECH_RECONCILIATION_SCHEDULE_START_MINUTE",
            minimum=0,
            maximum=1439,
        )
        end = _optional_environment_int(
            source,
            "ANANTA_SPEECH_RECONCILIATION_SCHEDULE_END_MINUTE",
            minimum=0,
            maximum=1439,
        )
        # Reuse the authoritative policy validator so config cannot diverge
        # from runtime admission semantics.
        SpeechReconciliationResourcePolicy().evaluate(
            SpeechReconciliationResourceRequest(
                mode=mode,
                requested_factor=1,
                user_max_factor=factor,
                live_call_active=False,
                foreground_load_micros=0,
                charging=True,
                minute_of_day=0,
                schedule_start_minute=start,
                schedule_end_minute=end,
            )
        )
        return cls(mode, factor, start, end)


@dataclass(frozen=True, slots=True)
class SpeechReconciliationWorkerConfiguration:
    endpoint: str
    allowed_endpoints: tuple[str, ...]
    bearer_token: str = field(repr=False)
    worker_id: str = ""
    location: str = "local"
    capacity: SpeechResourceVector = _DEFAULT_CAPACITY
    max_offline_assignments: int = 1

    @classmethod
    def from_environment(
        cls,
        source: Mapping[str, object],
    ) -> "SpeechReconciliationWorkerConfiguration":
        try:
            endpoint = normalize_speech_reconciliation_endpoint(
                str(source.get("ANANTA_SPEECH_RECONCILIATION_WORKER_URL") or "")
            )
            raw_allowlist = str(source.get("ANANTA_SPEECH_RECONCILIATION_ALLOWED_ENDPOINTS") or "")
            allowed = tuple(
                dict.fromkeys(
                    normalize_speech_reconciliation_endpoint(item) for item in raw_allowlist.split(",") if item.strip()
                )
            )
        except ValueError as exc:
            raise SpeechReconciliationQueuePumpConfigurationError(
                "speech_reconciliation_worker_endpoint_invalid"
            ) from exc
        if not allowed or endpoint not in allowed:
            raise SpeechReconciliationQueuePumpConfigurationError(
                "speech_reconciliation_worker_endpoint_not_allowlisted"
            )
        token = str(source.get("ANANTA_SPEECH_RECONCILIATION_TOKEN") or "").strip()
        if len(token) < 24 or any(character.isspace() for character in token):
            raise SpeechReconciliationQueuePumpConfigurationError("speech_reconciliation_worker_token_invalid")
        location = str(source.get("ANANTA_SPEECH_RECONCILIATION_WORKER_LOCATION") or "local").strip()
        if not _identifier(location):
            raise SpeechReconciliationQueuePumpConfigurationError("speech_reconciliation_worker_location_invalid")
        capacity = _resource_capacity(source)
        maximum = _environment_int(
            source,
            "ANANTA_SPEECH_RECONCILIATION_MAX_WORKERS",
            1,
            minimum=1,
            maximum=4,
        )
        configured_id = str(source.get("ANANTA_SPEECH_RECONCILIATION_WORKER_ID") or "").strip()
        worker_id = configured_id or f"speech-reconciliation-worker-{canonical_sha256(endpoint)[:16]}"
        if not _identifier(worker_id):
            raise SpeechReconciliationQueuePumpConfigurationError("speech_reconciliation_worker_id_invalid")
        return cls(endpoint, allowed, token, worker_id, location, capacity, maximum)


class SpeechReconciliationWorkerReadinessPort(Protocol):
    def ready(self, configuration: SpeechReconciliationWorkerConfiguration) -> bool: ...


class HttpSpeechReconciliationWorkerReadinessProbe:
    """Authenticated, DNS-pinned and content-free readiness probe."""

    def __init__(
        self,
        *,
        resolver: AddressResolver | None = None,
        timeout_seconds: float = 3.0,
        max_response_bytes: int = 8 * 1024,
    ) -> None:
        if not 0 < timeout_seconds <= 10 or not 1024 <= max_response_bytes <= 64 * 1024:
            raise ValueError("speech_reconciliation_readiness_limits_invalid")
        self._resolver = resolver
        self._timeout = timeout_seconds
        self._max_response_bytes = max_response_bytes

    def ready(self, configuration: SpeechReconciliationWorkerConfiguration) -> bool:
        parsed = urllib.parse.urlsplit(configuration.endpoint)
        assert parsed.hostname is not None and parsed.port is not None
        try:
            address = pin_private_container_address(
                parsed.hostname,
                parsed.port,
                resolver=self._resolver,
            )
        except PrivateContainerResolutionError:
            return False
        connection = http.client.HTTPConnection(address, parsed.port, timeout=self._timeout)
        try:
            connection.request(
                "GET",
                "/ready",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {configuration.bearer_token}",
                    "Host": f"{parsed.hostname}:{parsed.port}",
                },
            )
            response = connection.getresponse()
            raw = response.read(self._max_response_bytes + 1)
            if response.status != 200 or len(raw) > self._max_response_bytes:
                return False
            payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_non_finite)
            return bool(
                isinstance(payload, Mapping)
                and set(payload) == {"contract_version", "ready", "reason_code"}
                and payload.get("contract_version") == CONTRACT_VERSION
                and payload.get("ready") is True
                and payload.get("reason_code") is None
            )
        except (OSError, http.client.HTTPException, UnicodeDecodeError, ValueError):
            return False
        finally:
            connection.close()


class ConfiguredSpeechReconciliationWorkerDirectory:
    def __init__(
        self,
        configuration: SpeechReconciliationWorkerConfiguration,
        readiness: SpeechReconciliationWorkerReadinessPort,
    ) -> None:
        self._configuration = configuration
        self._readiness = readiness

    def healthy_candidates(self) -> tuple[SpeechReconciliationWorkerCandidate, ...]:
        configuration = self._configuration
        if not self._readiness.ready(configuration):
            return ()
        return (
            SpeechReconciliationWorkerCandidate(
                worker_id=configuration.worker_id,
                location=configuration.location,
                capabilities=frozenset({"speech_reconciliation"}),
                capacity=configuration.capacity,
                # /ready proves at least one free slot, not the exact active
                # count. Delegate one attempt per observation to avoid a
                # check/use burst that can oversubscribe the runtime.
                max_offline_assignments=1,
                active_offline_assignments=0,
                available=True,
                draining=False,
            ),
        )


class SqlSpeechReconciliationQueuedJobSource:
    """Read the global durable offline queue without exposing payload content."""

    def __init__(
        self,
        *,
        allowed_locations: frozenset[str],
        priority: int = 10,
    ) -> None:
        if not allowed_locations or any(not _identifier(item) for item in allowed_locations):
            raise ValueError("speech_reconciliation_allowed_locations_invalid")
        if isinstance(priority, bool) or not 0 <= priority <= 100:
            raise ValueError("speech_reconciliation_priority_invalid")
        self._allowed_locations = allowed_locations
        self._priority = priority

    def list_queued(
        self,
        *,
        now_ms: int,
        limit: int,
    ) -> tuple[QueuedSpeechReconciliation, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("speech_reconciliation_queue_batch_invalid")
        with Session(engine) as session:
            rows = session.exec(
                select(SpeechReconciliationJobDB)
                .where(
                    SpeechReconciliationJobDB.state == "queued",
                    SpeechReconciliationJobDB.deadline_at_ms > now_ms,
                )
                .order_by(
                    SpeechReconciliationJobDB.created_at_ms.asc(),
                    SpeechReconciliationJobDB.id.asc(),
                )
                .limit(limit)
            ).all()
            job_ids = [row.id for row in rows]
            checkpoints: dict[str, str] = {}
            if job_ids:
                checkpoint_rows = session.exec(
                    select(SpeechReconciliationCheckpointDB)
                    .where(SpeechReconciliationCheckpointDB.job_id.in_(job_ids))
                    .order_by(
                        SpeechReconciliationCheckpointDB.job_id.asc(),
                        SpeechReconciliationCheckpointDB.created_at_ms.desc(),
                        SpeechReconciliationCheckpointDB.checkpoint_sequence.desc(),
                    )
                ).all()
                for checkpoint in checkpoint_rows:
                    checkpoints.setdefault(checkpoint.job_id, checkpoint.checkpoint_ref)
        queued: list[QueuedSpeechReconciliation] = []
        for row in rows:
            allocated = SpeechResourceVector.from_mapping(
                dict(row.budget_plan or {}).get("allocated"),
                "budget_plan.allocated",
            )
            queued.append(
                QueuedSpeechReconciliation(
                    job=job_contract(row),
                    tenant_id=row.tenant_id,
                    owner_subject=row.owner_subject,
                    priority=self._priority,
                    queued_sequence=row.created_at_ms,
                    allowed_locations=self._allowed_locations,
                    requested_resources=allocated,
                    checkpoint_ref=checkpoints.get(row.id),
                    requested_compute_factor=row.current_compute_factor,
                )
            )
        return tuple(queued)

    def tenant_active_assignments(self) -> dict[str, int]:
        with Session(engine) as session:
            rows = session.exec(
                select(
                    SpeechReconciliationJobDB.tenant_id,
                    func.count(SpeechReconciliationJobDB.id),
                )
                .where(SpeechReconciliationJobDB.state == "running")
                .group_by(SpeechReconciliationJobDB.tenant_id)
            ).all()
        return {str(tenant_id): int(count) for tenant_id, count in rows}


class SqlSpeechReconciliationRuntimePressureProbe:
    """Fail closed on active Voice runs, system pressure or probe failure."""

    def __init__(
        self,
        *,
        charging: bool,
        quiet_hours: bool,
        cpu_percent: Callable[[], float] | None = None,
    ) -> None:
        self._charging = bool(charging)
        self._quiet_hours = bool(quiet_hours)
        self._cpu_percent = cpu_percent or (lambda: psutil.cpu_percent(interval=None))

    def snapshot(self, *, now_ms: int) -> SpeechReconciliationRuntimePressure:
        try:
            with Session(engine) as session:
                active = session.exec(
                    select(VoiceLiveRunDB.id)
                    .where(
                        VoiceLiveRunDB.status.in_(_FINAL_LIVE_STATES),
                        VoiceLiveRunDB.expires_at > now_ms / 1000,
                    )
                    .limit(1)
                ).first()
            raw_cpu = float(self._cpu_percent())
            if not math.isfinite(raw_cpu) or raw_cpu < 0 or raw_cpu > 100:
                raise ValueError("invalid cpu observation")
            load = min(1_000_000, int(raw_cpu * 10_000))
            live = active is not None
        except Exception:
            # Unknown pressure cannot authorize competing offline work.
            live = True
            load = 1_000_000
        instant = datetime.fromtimestamp(now_ms / 1000).astimezone()
        return SpeechReconciliationRuntimePressure(
            live_call_active=live,
            foreground_load_micros=load,
            charging=self._charging,
            quiet_hours=self._quiet_hours,
            minute_of_day=instant.hour * 60 + instant.minute,
        )


@dataclass(frozen=True, slots=True)
class SpeechReconciliationQueuePumpSummary:
    reason_code: str
    queued_scanned: int = 0
    policy_rejected: int = 0
    healthy_candidates: int = 0
    scheduled: int = 0


class SpeechReconciliationQueuePump:
    """One bounded Hub scheduling tick with all gates ahead of claim."""

    def __init__(
        self,
        *,
        queued_jobs: SpeechReconciliationQueuedJobPort,
        workers: SpeechReconciliationWorkerDirectoryPort,
        pressure: SpeechReconciliationRuntimePressurePort,
        scheduler: SpeechReconciliationScheduler,
        resources: SpeechReconciliationResourceConfiguration,
        feature_enabled: Callable[[], bool],
        policy: SpeechReconciliationResourcePolicy | None = None,
        clock_ms: Callable[[], int] | None = None,
        batch_size: int = 100,
    ) -> None:
        if not 1 <= batch_size <= 200:
            raise ValueError("speech_reconciliation_queue_batch_invalid")
        self._queued_jobs = queued_jobs
        self._workers = workers
        self._pressure = pressure
        self._scheduler = scheduler
        self._resources = resources
        self._feature_enabled = feature_enabled
        self._policy = policy or SpeechReconciliationResourcePolicy()
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._batch_size = batch_size

    def run_once(self) -> SpeechReconciliationQueuePumpSummary:
        # The dependency-resolved feature flag includes the deployment-wide
        # background-operations kill switch.  Nothing is read or claimed first.
        if not self._feature_enabled():
            return SpeechReconciliationQueuePumpSummary("speech_reconciliation_feature_disabled")
        now = self._clock_ms()
        pressure = self._pressure.snapshot(now_ms=now)
        queued = tuple(self._queued_jobs.list_queued(now_ms=now, limit=self._batch_size))
        if not queued:
            return SpeechReconciliationQueuePumpSummary("speech_reconciliation_queue_empty")
        admitted: list[QueuedSpeechReconciliation] = []
        rejected_reason = "speech_reconciliation_resource_paused"
        for item in queued:
            # Research factors and runtime caps require an explicit factor
            # transition before a claim; the pump never silently broadens work.
            requested_factor = _queued_factor(item)
            if requested_factor > self._resources.user_max_factor:
                rejected_reason = "speech_reconciliation_factor_reduction_required"
                continue
            decision = self._policy.evaluate(
                SpeechReconciliationResourceRequest(
                    mode=self._resources.mode,
                    requested_factor=requested_factor,
                    user_max_factor=self._resources.user_max_factor,
                    live_call_active=pressure.live_call_active,
                    foreground_load_micros=pressure.foreground_load_micros,
                    charging=pressure.charging,
                    minute_of_day=pressure.minute_of_day,
                    schedule_start_minute=self._resources.schedule_start_minute,
                    schedule_end_minute=self._resources.schedule_end_minute,
                    quiet_hours=pressure.quiet_hours,
                )
            )
            if decision.allowed:
                admitted.append(item)
            else:
                rejected_reason = decision.reason_code
        rejected = len(queued) - len(admitted)
        if not admitted:
            return SpeechReconciliationQueuePumpSummary(
                rejected_reason,
                queued_scanned=len(queued),
                policy_rejected=rejected,
            )
        candidates = tuple(self._workers.healthy_candidates())
        scheduled = self._scheduler.schedule(
            admitted,
            candidates,
            live_pressure=pressure.live_call_active,
            tenant_active_assignments=self._queued_jobs.tenant_active_assignments(),
        )
        return SpeechReconciliationQueuePumpSummary(
            "speech_reconciliation_attempts_scheduled" if scheduled else "speech_reconciliation_no_eligible_worker",
            queued_scanned=len(queued),
            policy_rejected=rejected,
            healthy_candidates=len(candidates),
            scheduled=len(scheduled),
        )


class ResourceGatedSpeechReconciliationLeasePort:
    """Recheck dynamic Hub authority immediately before every durable claim."""

    def __init__(
        self,
        delegate: SpeechReconciliationLeasePort,
        *,
        pressure: SpeechReconciliationRuntimePressurePort,
        resources: SpeechReconciliationResourceConfiguration,
        feature_enabled: Callable[[], bool],
        policy: SpeechReconciliationResourcePolicy | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._delegate = delegate
        self._pressure = pressure
        self._resources = resources
        self._feature_enabled = feature_enabled
        self._policy = policy or SpeechReconciliationResourcePolicy()
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)

    def acquire(
        self,
        queued: QueuedSpeechReconciliation,
        candidate: SpeechReconciliationWorkerCandidate,
        *,
        ttl_ms: int,
    ) -> SpeechReconciliationLease:
        if not self._feature_enabled():
            raise SpeechReconciliationSchedulingError("speech_reconciliation_feature_disabled")
        requested_factor = _queued_factor(queued)
        if requested_factor > self._resources.user_max_factor:
            raise SpeechReconciliationSchedulingError("speech_reconciliation_factor_reduction_required")
        pressure = self._pressure.snapshot(now_ms=self._clock_ms())
        decision = self._policy.evaluate(
            SpeechReconciliationResourceRequest(
                mode=self._resources.mode,
                requested_factor=requested_factor,
                user_max_factor=self._resources.user_max_factor,
                live_call_active=pressure.live_call_active,
                foreground_load_micros=pressure.foreground_load_micros,
                charging=pressure.charging,
                minute_of_day=pressure.minute_of_day,
                schedule_start_minute=self._resources.schedule_start_minute,
                schedule_end_minute=self._resources.schedule_end_minute,
                quiet_hours=pressure.quiet_hours,
            )
        )
        if not decision.allowed:
            raise SpeechReconciliationSchedulingError(decision.reason_code)
        return self._delegate.acquire(queued, candidate, ttl_ms=ttl_ms)

    def revoke(self, lease_id: str, *, reason_code: str) -> None:
        self._delegate.revoke(lease_id, reason_code=reason_code)


def build_speech_reconciliation_queue_pump(app: Any) -> SpeechReconciliationQueuePump:
    """Compose the productive Hub pump, with optional test/application overrides."""

    configuration = SpeechReconciliationWorkerConfiguration.from_environment(os.environ)
    extensions = getattr(app, "extensions", None)
    dispatcher = extensions.get("speech_reconciliation_dispatcher") if isinstance(extensions, Mapping) else None
    worker = HttpSpeechReconciliationWorkerPort(
        endpoint=configuration.endpoint,
        allowed_endpoints=configuration.allowed_endpoints,
        bearer_token=configuration.bearer_token,
    )
    if dispatcher is None:
        factory = (
            extensions.get("speech_reconciliation_dispatcher_factory") if isinstance(extensions, Mapping) else None
        )
        if callable(factory):
            dispatcher = factory(worker)
        else:
            from agent.services.speech_reconciliation_production_composition import (
                build_default_speech_reconciliation_dispatcher,
            )

            dispatcher = build_default_speech_reconciliation_dispatcher(worker)
    if not hasattr(dispatcher, "dispatch"):
        raise SpeechReconciliationQueuePumpConfigurationError("speech_reconciliation_dispatcher_invalid")
    audit = extensions.get("semantic_media_audit_recorder") if isinstance(extensions, Mapping) else None
    repository = SpeechReconciliationRepository(audit=audit)
    resources = SpeechReconciliationResourceConfiguration.from_environment(os.environ)
    pressure = SqlSpeechReconciliationRuntimePressureProbe(
        charging=_strict_environment_boolean(
            os.environ.get("ANANTA_SPEECH_RECONCILIATION_CHARGING"),
            default=False,
        ),
        quiet_hours=_strict_environment_boolean(
            os.environ.get("ANANTA_SPEECH_RECONCILIATION_QUIET_HOURS"),
            default=False,
        ),
    )

    def feature_enabled() -> bool:
        import agent.common.context

        return not agent.common.context.shutdown_requested and resolve_semantic_media_feature_flags(os.environ).get(
            "speech_reconciliation", False
        )

    scheduler = SpeechReconciliationScheduler(
        leases=ResourceGatedSpeechReconciliationLeasePort(
            RepositorySpeechReconciliationLeasePort(
                repository,
                audit=audit,
            ),
            pressure=pressure,
            resources=resources,
            feature_enabled=feature_enabled,
        ),
        tasks=HubSpeechReconciliationTaskPort(),
        dispatcher=dispatcher,
        max_offline_assignments=_environment_int(
            os.environ,
            "ANANTA_SPEECH_RECONCILIATION_QUEUE_CAPACITY",
            4,
            minimum=1,
            maximum=128,
        ),
        lease_ttl_ms=_environment_int(
            os.environ,
            "ANANTA_SPEECH_RECONCILIATION_LEASE_TTL_MS",
            30_000,
            minimum=5_000,
            maximum=300_000,
        ),
    )
    return SpeechReconciliationQueuePump(
        queued_jobs=SqlSpeechReconciliationQueuedJobSource(allowed_locations=frozenset({configuration.location})),
        workers=ConfiguredSpeechReconciliationWorkerDirectory(
            configuration,
            HttpSpeechReconciliationWorkerReadinessProbe(),
        ),
        pressure=pressure,
        scheduler=scheduler,
        resources=resources,
        feature_enabled=feature_enabled,
        batch_size=_environment_int(
            os.environ,
            "ANANTA_SPEECH_RECONCILIATION_QUEUE_BATCH_SIZE",
            100,
            minimum=1,
            maximum=200,
        ),
    )


def start_speech_reconciliation_queue_pump_thread(app: Any) -> None:
    """Start one feature-gated Hub queue-pump thread per Flask app."""

    if settings.role != "hub" or not resolve_semantic_media_feature_flags(os.environ).get(
        "speech_reconciliation", False
    ):
        return
    extensions = getattr(app, "extensions", None)
    if isinstance(extensions, dict):
        existing = extensions.get(_EXTENSION_KEY)
        if isinstance(existing, Mapping):
            thread = existing.get("thread")
            if isinstance(thread, threading.Thread) and thread.is_alive():
                return
    try:
        factory = extensions.get("speech_reconciliation_queue_pump_factory") if isinstance(extensions, dict) else None
        service = factory() if callable(factory) else build_speech_reconciliation_queue_pump(app)
    except Exception as exc:
        logging.warning(
            "Speech reconciliation queue pump unavailable: %s",
            str(getattr(exc, "reason_code", type(exc).__name__)),
        )
        return
    interval = _environment_float(
        os.environ,
        "ANANTA_SPEECH_RECONCILIATION_QUEUE_INTERVAL_SECONDS",
        2.0,
        minimum=0.25,
        maximum=300.0,
    )
    stop_event = threading.Event()

    def run() -> None:
        import agent.common.context

        while not stop_event.is_set() and not agent.common.context.shutdown_requested:
            try:
                with app.app_context():
                    summary = service.run_once()
                if summary.scheduled:
                    logging.info(
                        "Speech reconciliation queue: scanned=%s candidates=%s scheduled=%s rejected=%s",
                        summary.queued_scanned,
                        summary.healthy_candidates,
                        summary.scheduled,
                        summary.policy_rejected,
                    )
            except Exception as exc:
                logging.warning(
                    "Speech reconciliation queue tick unavailable: %s",
                    str(getattr(exc, "reason_code", type(exc).__name__)),
                )
            stop_event.wait(interval)

    thread = threading.Thread(
        target=run,
        name="speech-reconciliation-queue-pump",
        daemon=True,
    )
    if isinstance(extensions, dict):
        extensions[_EXTENSION_KEY] = {
            "service": service,
            "thread": thread,
            "stop_event": stop_event,
        }
    import agent.common.context

    agent.common.context.active_threads.append(thread)
    thread.start()


def stop_speech_reconciliation_queue_pump(app: Any, *, join_timeout: float = 1.0) -> None:
    extensions = getattr(app, "extensions", None)
    state = extensions.get(_EXTENSION_KEY) if isinstance(extensions, Mapping) else None
    if not isinstance(state, Mapping):
        return
    stop_event = state.get("stop_event")
    thread = state.get("thread")
    if isinstance(stop_event, threading.Event):
        stop_event.set()
    if isinstance(thread, threading.Thread) and thread is not threading.current_thread() and thread.is_alive():
        thread.join(timeout=max(0.0, min(float(join_timeout), 10.0)))


def _resource_capacity(source: Mapping[str, object]) -> SpeechResourceVector:
    raw = str(source.get("ANANTA_SPEECH_RECONCILIATION_WORKER_CAPACITY_JSON") or "").strip()
    if not raw:
        return _DEFAULT_CAPACITY
    try:
        parsed = json.loads(raw, parse_constant=_reject_non_finite)
        return SpeechResourceVector.from_mapping(parsed, "worker_capacity")
    except (ValueError, TypeError) as exc:
        raise SpeechReconciliationQueuePumpConfigurationError("speech_reconciliation_worker_capacity_invalid") from exc


def _environment_int(
    source: Mapping[str, object],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = source.get(key)
    if raw in (None, ""):
        return default
    try:
        value = int(str(raw), 10)
    except (TypeError, ValueError) as exc:
        raise SpeechReconciliationQueuePumpConfigurationError(
            "speech_reconciliation_environment_integer_invalid"
        ) from exc
    if not minimum <= value <= maximum:
        raise SpeechReconciliationQueuePumpConfigurationError("speech_reconciliation_environment_integer_invalid")
    return value


def _optional_environment_int(
    source: Mapping[str, object],
    key: str,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if source.get(key) in (None, ""):
        return None
    return _environment_int(source, key, minimum, minimum=minimum, maximum=maximum)


def _environment_float(
    source: Mapping[str, object],
    key: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = source.get(key)
    if raw in (None, ""):
        return default
    try:
        value = float(str(raw))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum)) if math.isfinite(value) else default


def _strict_environment_boolean(value: object, *, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true"}:
            return True
        if normalized in {"0", "false"}:
            return False
    raise SpeechReconciliationQueuePumpConfigurationError("speech_reconciliation_environment_boolean_invalid")


def _identifier(value: str) -> bool:
    return bool(
        value
        and len(value) <= 192
        and value[0].isalnum()
        and all(character.isalnum() or character in "_.:-" for character in value)
    )


def _queued_factor(item: QueuedSpeechReconciliation) -> int:
    value = item.requested_compute_factor
    if value is None:
        value = min(10, item.job.max_compute_factor)
    if type(value) is not int or not 1 <= value <= item.job.max_compute_factor:
        raise SpeechReconciliationQueuePumpConfigurationError(
            "speech_reconciliation_quality_factor_invalid"
        )
    return value


def _reject_non_finite(value: str) -> None:
    raise ValueError("non-finite JSON value")


__all__ = [
    "ConfiguredSpeechReconciliationWorkerDirectory",
    "HttpSpeechReconciliationWorkerReadinessProbe",
    "ResourceGatedSpeechReconciliationLeasePort",
    "SpeechReconciliationDispatcherFactoryPort",
    "SpeechReconciliationQueuePump",
    "SpeechReconciliationQueuePumpConfigurationError",
    "SpeechReconciliationQueuePumpSummary",
    "SpeechReconciliationQueuedJobPort",
    "SpeechReconciliationResourceConfiguration",
    "SpeechReconciliationRuntimePressure",
    "SpeechReconciliationRuntimePressurePort",
    "SpeechReconciliationWorkerConfiguration",
    "SpeechReconciliationWorkerDirectoryPort",
    "SpeechReconciliationWorkerReadinessPort",
    "SqlSpeechReconciliationQueuedJobSource",
    "SqlSpeechReconciliationRuntimePressureProbe",
    "build_speech_reconciliation_queue_pump",
    "start_speech_reconciliation_queue_pump_thread",
    "stop_speech_reconciliation_queue_pump",
]
