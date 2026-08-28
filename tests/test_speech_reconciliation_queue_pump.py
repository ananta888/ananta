from __future__ import annotations

import json
import threading
import time

import pytest
from sqlmodel import Session

import agent.services.background.speech_reconciliation_queue_pump as pump_module
from agent.database import engine
from agent.db_models.speech_reconciliation import (
    SpeechReconciliationAttemptDB,
    SpeechReconciliationCheckpointDB,
    SpeechReconciliationJobDB,
)
from agent.services.background.speech_reconciliation_queue_pump import (
    ConfiguredSpeechReconciliationWorkerDirectory,
    HttpSpeechReconciliationWorkerReadinessProbe,
    ResourceGatedSpeechReconciliationLeasePort,
    SpeechReconciliationQueuePump,
    SpeechReconciliationQueuePumpConfigurationError,
    SpeechReconciliationResourceConfiguration,
    SpeechReconciliationRuntimePressure,
    SpeechReconciliationWorkerConfiguration,
    SqlSpeechReconciliationQueuedJobSource,
    build_speech_reconciliation_queue_pump,
)
from agent.services.speech_reconciliation_resource_policy import (
    SpeechReconciliationResourcePolicy,
)
from agent.services.speech_reconciliation_scheduler import (
    QueuedSpeechReconciliation,
    SpeechReconciliationSchedulingError,
    SpeechReconciliationWorkerCandidate,
)
from agent.services.speech_reconciliation_worker_port import (
    HttpSpeechReconciliationWorkerPort,
)
from ananta_contracts.speech_reconciliation import CONTRACT_VERSION, SpeechResourceVector
from tests.speech_reconciliation_support import digest, job_contract


def _queued(*, factor: int = 10) -> QueuedSpeechReconciliation:
    return QueuedSpeechReconciliation(
        job=job_contract(max_compute_factor=factor),
        tenant_id="tenant-a",
        owner_subject="owner-a",
        priority=10,
        queued_sequence=1,
        allowed_locations=frozenset({"local"}),
        requested_resources=SpeechResourceVector(wall_time_ms=100, cpu_time_ms=100),
    )


def _candidate() -> SpeechReconciliationWorkerCandidate:
    return SpeechReconciliationWorkerCandidate(
        worker_id="speech-worker-local",
        location="local",
        capabilities=frozenset({"speech_reconciliation"}),
        capacity=SpeechResourceVector(wall_time_ms=1_000, cpu_time_ms=1_000),
        max_offline_assignments=1,
        active_offline_assignments=0,
    )


class _Queue:
    def __init__(self, events: list[str], items=(_queued(),)) -> None:
        self.events = events
        self.items = tuple(items)

    def list_queued(self, *, now_ms: int, limit: int):
        assert now_ms == 1_000_000 and limit == 100
        self.events.append("queue")
        return self.items

    def tenant_active_assignments(self):
        self.events.append("tenant-counts")
        return {"tenant-a": 0}


class _Workers:
    def __init__(self, events: list[str], candidates=(_candidate(),)) -> None:
        self.events = events
        self.candidates = tuple(candidates)

    def healthy_candidates(self):
        self.events.append("workers")
        return self.candidates


class _Pressure:
    def __init__(self, events: list[str], *, live: bool = False, load: int = 0) -> None:
        self.events = events
        self.live = live
        self.load = load

    def snapshot(self, *, now_ms: int):
        assert now_ms == 1_000_000
        self.events.append("pressure")
        return SpeechReconciliationRuntimePressure(
            live_call_active=self.live,
            foreground_load_micros=self.load,
            charging=True,
            quiet_hours=False,
            minute_of_day=60,
        )


class _Policy(SpeechReconciliationResourcePolicy):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def evaluate(self, request):
        self.events.append("policy")
        return super().evaluate(request)


class _Scheduler:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls = []

    def schedule(self, queued, candidates, **kwargs):
        self.events.append("schedule")
        self.calls.append((tuple(queued), tuple(candidates), kwargs))
        return (object(),) if candidates else ()


def _pump(
    events: list[str],
    *,
    enabled=True,
    live=False,
    load=0,
    resources: SpeechReconciliationResourceConfiguration | None = None,
    items=(_queued(),),
):
    scheduler = _Scheduler(events)
    service = SpeechReconciliationQueuePump(
        queued_jobs=_Queue(events, items),
        workers=_Workers(events),
        pressure=_Pressure(events, live=live, load=load),
        scheduler=scheduler,  # type: ignore[arg-type]
        resources=resources or SpeechReconciliationResourceConfiguration(mode="immediate"),
        feature_enabled=lambda: events.append("feature") is None and enabled,
        policy=_Policy(events),
        clock_ms=lambda: 1_000_000,
    )
    return service, scheduler


def test_feature_and_kill_switch_fence_every_read_and_claim() -> None:
    events: list[str] = []
    service, scheduler = _pump(events, enabled=False)
    summary = service.run_once()
    assert summary.reason_code == "speech_reconciliation_feature_disabled"
    assert events == ["feature"]
    assert scheduler.calls == []


@pytest.mark.parametrize(
    ("live", "load", "reason"),
    [
        (True, 0, "speech_reconciliation_live_pressure"),
        (False, 800_000, "speech_reconciliation_foreground_pressure"),
    ],
)
def test_live_and_foreground_pressure_are_policy_checked_before_any_claim(
    live: bool,
    load: int,
    reason: str,
) -> None:
    events: list[str] = []
    service, scheduler = _pump(events, live=live, load=load)
    summary = service.run_once()
    assert summary.reason_code == reason and summary.policy_rejected == 1
    assert events == ["feature", "pressure", "queue", "policy"]
    assert scheduler.calls == []


def test_productive_tick_loads_queue_health_and_calls_real_scheduler_boundary() -> None:
    events: list[str] = []
    service, scheduler = _pump(events)
    summary = service.run_once()
    assert summary.scheduled == 1 and summary.healthy_candidates == 1
    assert events == [
        "feature",
        "pressure",
        "queue",
        "policy",
        "workers",
        "tenant-counts",
        "schedule",
    ]
    scheduled_queue, candidates, kwargs = scheduler.calls[0]
    assert scheduled_queue[0].job.job_id == "speech-reconciliation-job-test"
    assert candidates[0].capabilities == frozenset({"speech_reconciliation"})
    assert kwargs == {"live_pressure": False, "tenant_active_assignments": {"tenant-a": 0}}


def test_user_factor_and_disabled_mode_fail_closed_before_worker_discovery() -> None:
    events: list[str] = []
    service, _ = _pump(
        events,
        resources=SpeechReconciliationResourceConfiguration(mode="immediate", user_max_factor=5),
    )
    summary = service.run_once()
    assert summary.reason_code == "speech_reconciliation_factor_reduction_required"
    assert events == ["feature", "pressure", "queue"]

    events.clear()
    service, _ = _pump(
        events,
        resources=SpeechReconciliationResourceConfiguration(mode="disabled"),
    )
    summary = service.run_once()
    assert summary.reason_code == "speech_reconciliation_disabled"
    assert events == ["feature", "pressure", "queue", "policy"]


def test_production_lease_guard_rechecks_feature_and_pressure_at_claim_boundary() -> None:
    class _LeaseDelegate:
        acquired = 0

        def acquire(self, queued, candidate, *, ttl_ms):
            del queued, candidate, ttl_ms
            self.acquired += 1
            return object()

        def revoke(self, lease_id, *, reason_code):
            del lease_id, reason_code

    events: list[str] = []
    delegate = _LeaseDelegate()
    guard = ResourceGatedSpeechReconciliationLeasePort(
        delegate,  # type: ignore[arg-type]
        pressure=_Pressure(events, live=True),
        resources=SpeechReconciliationResourceConfiguration(mode="immediate"),
        feature_enabled=lambda: True,
        clock_ms=lambda: 1_000_000,
    )
    with pytest.raises(
        SpeechReconciliationSchedulingError,
        match="speech_reconciliation_live_pressure",
    ):
        guard.acquire(_queued(), _candidate(), ttl_ms=30_000)
    assert delegate.acquired == 0 and events == ["pressure"]

    disabled = ResourceGatedSpeechReconciliationLeasePort(
        delegate,  # type: ignore[arg-type]
        pressure=_Pressure(events),
        resources=SpeechReconciliationResourceConfiguration(mode="immediate"),
        feature_enabled=lambda: False,
        clock_ms=lambda: 1_000_000,
    )
    with pytest.raises(
        SpeechReconciliationSchedulingError,
        match="speech_reconciliation_feature_disabled",
    ):
        disabled.acquire(_queued(), _candidate(), ttl_ms=30_000)
    assert delegate.acquired == 0


def _worker_environment(token: str = "x" * 32) -> dict[str, str]:
    endpoint = "http://speech-reconciliation-worker:8098/internal/v1/speech-reconciliation"
    return {
        "ANANTA_SPEECH_RECONCILIATION_WORKER_URL": endpoint,
        "ANANTA_SPEECH_RECONCILIATION_ALLOWED_ENDPOINTS": endpoint,
        "ANANTA_SPEECH_RECONCILIATION_TOKEN": token,
        "ANANTA_SPEECH_RECONCILIATION_MAX_WORKERS": "2",
    }


def test_environment_worker_configuration_is_exactly_allowlisted_and_secret_safe() -> None:
    token = "secret-worker-token-value-123456"
    configuration = SpeechReconciliationWorkerConfiguration.from_environment(
        _worker_environment(token)
    )
    assert configuration.max_offline_assignments == 2
    assert configuration.location == "local"
    assert token not in repr(configuration)
    with pytest.raises(
        SpeechReconciliationQueuePumpConfigurationError,
        match="endpoint_not_allowlisted",
    ):
        SpeechReconciliationWorkerConfiguration.from_environment(
            {
                **_worker_environment(),
                "ANANTA_SPEECH_RECONCILIATION_ALLOWED_ENDPOINTS": (
                    "http://other-worker:8098/internal/v1/speech-reconciliation"
                ),
            }
        )
    with pytest.raises(
        SpeechReconciliationQueuePumpConfigurationError,
        match="worker_token_invalid",
    ):
        SpeechReconciliationWorkerConfiguration.from_environment(
            _worker_environment("too-short")
        )


def test_default_composition_builds_real_scheduler_transport_through_narrow_dispatcher_factory(
    monkeypatch,
) -> None:
    for key, value in _worker_environment().items():
        monkeypatch.setenv(key, value)
    captured = []

    class _Dispatcher:
        @staticmethod
        def dispatch(scheduled):
            return scheduled

    class _App:
        extensions = {
            "speech_reconciliation_dispatcher_factory": lambda worker: (
                captured.append(worker) or _Dispatcher()
            )
        }

    service = build_speech_reconciliation_queue_pump(_App())
    assert isinstance(service, SpeechReconciliationQueuePump)
    assert len(captured) == 1
    assert isinstance(captured[0], HttpSpeechReconciliationWorkerPort)
    assert "x" * 32 not in repr(captured[0])


class _Readiness:
    def __init__(self, ready: bool) -> None:
        self.is_ready = ready

    def ready(self, configuration):
        assert configuration.bearer_token == "x" * 32
        return self.is_ready


def test_directory_only_projects_fresh_healthy_candidate() -> None:
    configuration = SpeechReconciliationWorkerConfiguration.from_environment(
        _worker_environment()
    )
    unavailable = ConfiguredSpeechReconciliationWorkerDirectory(
        configuration, _Readiness(False)
    )
    assert unavailable.healthy_candidates() == ()
    available = ConfiguredSpeechReconciliationWorkerDirectory(
        configuration, _Readiness(True)
    )
    candidates = available.healthy_candidates()
    assert len(candidates) == 1
    assert candidates[0].max_offline_assignments == 1


def test_authenticated_readiness_probe_pins_dns_and_validates_closed_payload(monkeypatch) -> None:
    calls: list[tuple] = []

    class _Response:
        status = 200

        @staticmethod
        def read(limit):
            assert limit == 8193
            return json.dumps(
                {
                    "contract_version": CONTRACT_VERSION,
                    "ready": True,
                    "reason_code": None,
                }
            ).encode()

    class _Connection:
        def __init__(self, address, port, timeout):
            calls.append(("connect", address, port, timeout))

        def request(self, method, path, headers):
            calls.append(("request", method, path, headers))

        @staticmethod
        def getresponse():
            return _Response()

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(pump_module.http.client, "HTTPConnection", _Connection)
    configuration = SpeechReconciliationWorkerConfiguration.from_environment(
        _worker_environment()
    )
    probe = HttpSpeechReconciliationWorkerReadinessProbe(
        resolver=lambda host, port: ["10.44.0.9"]
    )
    assert probe.ready(configuration) is True
    assert calls[0] == ("connect", "10.44.0.9", 8098, 3.0)
    request = calls[1]
    assert request[1:3] == ("GET", "/ready")
    assert request[3]["Authorization"] == "Bearer " + "x" * 32


def test_resource_environment_reuses_policy_shape_validation() -> None:
    scheduled = SpeechReconciliationResourceConfiguration.from_environment(
        {
            "ANANTA_SPEECH_RECONCILIATION_RESOURCE_MODE": "scheduled",
            "ANANTA_SPEECH_RECONCILIATION_SCHEDULE_START_MINUTE": "120",
            "ANANTA_SPEECH_RECONCILIATION_SCHEDULE_END_MINUTE": "240",
            "ANANTA_SPEECH_RECONCILIATION_USER_MAX_FACTOR": "5",
        }
    )
    assert scheduled == SpeechReconciliationResourceConfiguration("scheduled", 5, 120, 240)
    with pytest.raises(Exception, match="schedule_invalid"):
        SpeechReconciliationResourceConfiguration.from_environment(
            {"ANANTA_SPEECH_RECONCILIATION_RESOURCE_MODE": "scheduled"}
        )


def test_sql_queue_source_loads_durable_jobs_latest_checkpoint_and_tenant_activity() -> None:
    now = time.time_ns() // 1_000_000
    vector = SpeechResourceVector(
        wall_time_ms=1_000,
        cpu_time_ms=1_000,
        disk_bytes=1_000,
    )

    def row(job_id: str, state: str) -> SpeechReconciliationJobDB:
        return SpeechReconciliationJobDB(
            id=job_id,
            tenant_id="tenant-queue-source",
            owner_subject="owner-queue-source",
            pair_scope_digest=digest("queue-scope"),
            idempotency_key_digest=digest(f"queue-idempotency-{job_id}"),
            request_digest=digest(f"queue-request-{job_id}"),
            state=state,
            stage="admission",
            consent_id="speech-consent-queue-source",
            consent_version=1,
            revocation_epoch=0,
            input_manifest_digest=digest(f"queue-manifest-{job_id}"),
            input_lineage_digest=digest(f"queue-lineage-{job_id}"),
            input_artifact_ref="artifact://speech-evidence/queue/input.enc",
            policy_digest=digest("queue-policy"),
            budget_plan={"allocated": vector.to_dict()},
            source_duration_ms=60_000,
            max_compute_factor=10,
            key_epoch=1,
            deadline_at_ms=now + 60_000,
            created_at_ms=now,
            updated_at_ms=now,
        )

    queued_id = "speech-reconciliation-queue-source"
    running_id = "speech-reconciliation-queue-source-running"
    attempt_id = "speech-reconciliation-attempt-queue-source"
    with Session(engine) as session:
        session.add(row(queued_id, "queued"))
        session.add(row(running_id, "running"))
        session.flush()
        session.add(
            SpeechReconciliationAttemptDB(
                id=attempt_id,
                job_id=queued_id,
                tenant_id="tenant-queue-source",
                owner_subject="owner-queue-source",
                attempt_number=1,
                state="fenced",
                worker_id_digest=digest("queue-worker"),
                worker_capability_digest=digest("queue-capability"),
                location_digest=digest("local"),
                resource_profile_digest=digest("queue-resource-profile"),
                fencing_token_digest=digest("queue-fencing-token"),
                fencing_epoch=1,
                lease_expires_at_ms=now - 1,
                deadline_at_ms=now + 60_000,
                last_heartbeat_at_ms=now - 1,
                created_at_ms=now - 2,
                updated_at_ms=now - 1,
                finished_at_ms=now - 1,
            )
        )
        session.flush()
        for sequence in (1, 2):
            session.add(
                SpeechReconciliationCheckpointDB(
                    job_id=queued_id,
                    attempt_id=attempt_id,
                    tenant_id="tenant-queue-source",
                    owner_subject="owner-queue-source",
                    fencing_epoch=1,
                    consent_version=1,
                    revocation_epoch=0,
                    input_manifest_digest=digest(f"queue-manifest-{queued_id}"),
                    policy_digest=digest("queue-policy"),
                    ledger_sequence=sequence,
                    key_epoch=1,
                    checkpoint_sequence=sequence,
                    checkpoint_digest=digest(f"queue-checkpoint-{sequence}"),
                    checkpoint_ref=(
                        f"artifact://speech-reconciliation-checkpoints/queue/{sequence}.enc"
                    ),
                    stage="slow_asr",
                    state_digest=digest(f"queue-state-{sequence}"),
                    created_at_ms=now + sequence,
                )
            )
        session.commit()
    source = SqlSpeechReconciliationQueuedJobSource(
        allowed_locations=frozenset({"local"})
    )
    queued = source.list_queued(now_ms=now, limit=100)
    selected = next(item for item in queued if item.job.job_id == queued_id)
    assert selected.checkpoint_ref == (
        "artifact://speech-reconciliation-checkpoints/queue/2.enc"
    )
    assert selected.requested_resources == vector
    assert source.tenant_active_assignments()["tenant-queue-source"] == 1


def test_pressure_snapshot_shape_is_immutable() -> None:
    pressure = SpeechReconciliationRuntimePressure(False, 10, True, False, 20)
    with pytest.raises(AttributeError):
        pressure.foreground_load_micros = 20  # type: ignore[misc]


def test_stop_is_idempotent_for_absent_and_completed_thread() -> None:
    class _App:
        extensions = {}

    app = _App()
    pump_module.stop_speech_reconciliation_queue_pump(app)
    event = threading.Event()
    thread = threading.Thread(target=lambda: None)
    thread.start()
    thread.join()
    app.extensions[pump_module._EXTENSION_KEY] = {
        "stop_event": event,
        "thread": thread,
    }
    pump_module.stop_speech_reconciliation_queue_pump(app)
    assert event.is_set()
