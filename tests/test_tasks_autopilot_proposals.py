import contextlib
import time
from types import SimpleNamespace

import pytest
from sqlmodel import Session, delete

from agent.config import settings
from agent.database import engine
from agent.db_models import AgentInfoDB, TaskDB
from agent.repository import agent_repo, task_repo
from agent.routes.tasks.auto_planner import auto_planner
from agent.routes.tasks.autopilot import autonomous_loop
from agent.routes.tasks.autopilot_tick_engine import (
    _should_terminalize_no_executable_strategy,
)
from tests.knowledge_index_execution_test_support import (
    build_execution_task,
)


def _auth_headers(app):
    return {"Authorization": f"Bearer {app.config.get('AGENT_TOKEN')}"}


@pytest.fixture(autouse=True)
def _disable_followup_side_effects():
    previous_followups = auto_planner.auto_followup_enabled
    previous_autostart = auto_planner.auto_start_autopilot
    auto_planner.auto_followup_enabled = False
    auto_planner.auto_start_autopilot = False
    try:
        yield
    finally:
        auto_planner.auto_followup_enabled = previous_followups
        auto_planner.auto_start_autopilot = previous_autostart


@pytest.fixture(autouse=True)
def _isolate_autopilot_queue(app):
    """These tests assert one tick against one synthetic task and worker."""
    base_agent_config = dict(app.config.get("AGENT_CONFIG") or {})

    def _clear() -> None:
        with Session(engine) as session:
            session.exec(delete(TaskDB))
            session.exec(delete(AgentInfoDB))
            session.commit()
        app.config["AGENT_CONFIG"] = dict(base_agent_config)
        autonomous_loop._task_propose_streak = {}
        autonomous_loop._task_propose_last_attempt_at = {}
        autonomous_loop._task_propose_next_allowed_at = {}

    _clear()
    try:
        yield
    finally:
        _clear()



# Split from tests/test_tasks_autopilot.py to keep source files below 1000 lines.


def test_autopilot_propose_and_execute_propagate_exact_recovery_leases(
    app,
    monkeypatch,
):
    from agent.routes.tasks import autopilot as autopilot_module
    from agent.services import recovery_dispatch_gate_service
    from agent.services.recovery_dispatch_gate_service import (
        RecoveryDispatchGateDecision,
        RecoveryDispatchLease,
    )

    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "adaptive_model_routing_enabled": False,
        "autopilot_strategy_fallback_models": [],
        "autopilot_strategy_max_attempts": 1,
        "quality_gates": {
            "enabled": False,
            "autopilot_enforce": False,
        },
    }
    task_repo.save(
        TaskDB(
            id="recovery-lease-propagation",
            title="Propagate recovery lease",
            status="todo",
            task_kind="coding",
        )
    )
    worker_url = "http://worker-recovery-lease:5001"
    agent_repo.save(
        AgentInfoDB(
            url=worker_url,
            name="worker-recovery-lease",
            role="worker",
            token="worker-auth-token",
            status="online",
        )
    )
    allowed = RecoveryDispatchGateDecision(
        True,
        "recovery_release_gate_valid",
        source_task_id="source-recovery",
        plan_id="plan-recovery",
        release_epoch="release-recovery",
    )
    guarded = []

    class Gate:
        @contextlib.contextmanager
        def dispatch_guard(self, _task_id, **_kwargs):
            yield allowed

        def acquire_dispatch_lease(
            self,
            _task_id,
            *,
            phase,
            worker_url,
            **_kwargs,
        ):
            return RecoveryDispatchLease(
                allowed,
                phase=phase,
                token=f"lease-{phase}",
            )

        @contextlib.contextmanager
        def result_guard(
            self,
            task_id,
            *,
            token,
            phase,
            worker_url,
            **_kwargs,
        ):
            guarded.append(
                {
                    "task_id": task_id,
                    "token": token,
                    "phase": phase,
                    "worker_url": worker_url,
                }
            )
            yield allowed

        @staticmethod
        def invalidate_task(*_args, **_kwargs):
            raise AssertionError("valid recovery dispatch was invalidated")

    gate = Gate()
    monkeypatch.setattr(
        "agent.routes.tasks.autopilot_task_dispatcher."
        "get_recovery_dispatch_gate_service",
        lambda: gate,
    )
    monkeypatch.setattr(
        recovery_dispatch_gate_service,
        "get_recovery_dispatch_gate_service",
        lambda: gate,
    )
    calls = []

    def forward(url, endpoint, data, token=None):
        calls.append(
            {
                "url": url,
                "endpoint": endpoint,
                "payload": dict(data),
                "token": token,
            }
        )
        if endpoint.endswith("/step/propose"):
            return {
                "status": "success",
                "data": {
                    "reason": "run",
                    "command": "echo ok",
                },
            }
        return {
            "status": "success",
            "data": {
                "status": "completed",
                "exit_code": 0,
                "output": "ok",
            },
        }

    monkeypatch.setattr(autopilot_module, "_forward_to_worker", forward)

    with app.app_context():
        response = autonomous_loop.tick_once()
        updated = task_repo.get_by_id(
            "recovery-lease-propagation"
        )

    assert response["dispatched"] == 1
    assert updated is not None and updated.status == "completed"
    propose_call = next(
        call
        for call in calls
        if call["endpoint"].endswith("/step/propose")
    )
    execute_call = next(
        call
        for call in calls
        if call["endpoint"].endswith("/step/execute")
    )
    assert propose_call["url"] == worker_url
    assert propose_call["endpoint"] == (
        "/tasks/recovery-lease-propagation/step/propose"
    )
    assert propose_call["token"] == "worker-auth-token"
    assert propose_call["payload"]["task_id"] == (
        "recovery-lease-propagation"
    )
    assert propose_call["payload"][
        "dispatch_lease_token"
    ] == "lease-propose"
    assert propose_call["payload"][
        "dispatch_lease_phase"
    ] == "propose"
    assert execute_call["url"] == worker_url
    assert execute_call["endpoint"] == (
        "/tasks/recovery-lease-propagation/step/execute"
    )
    assert execute_call["token"] == "worker-auth-token"
    assert execute_call["payload"]["task_id"] == (
        "recovery-lease-propagation"
    )
    assert execute_call["payload"]["command"] == "echo ok"
    assert execute_call["payload"][
        "dispatch_lease_token"
    ] == "lease-execute"
    assert execute_call["payload"][
        "dispatch_lease_phase"
    ] == "execute"
    assert guarded == [
        {
            "task_id": "recovery-lease-propagation",
            "token": "lease-propose",
            "phase": "propose",
            "worker_url": worker_url,
        },
        {
            "task_id": "recovery-lease-propagation",
            "token": "lease-execute",
            "phase": "execute",
            "worker_url": worker_url,
        },
    ]


def test_autopilot_recovery_retry_reuses_exact_payload_and_worker_token(
    app,
    monkeypatch,
):
    from agent.routes.tasks import autopilot as autopilot_module

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "hub_url", "http://hub:5000")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "autonomous_resilience": {
            "retry_attempts": 2,
            "retry_backoff_seconds": 0.01,
            "retry_max_backoff_seconds": 0.01,
            "retry_jitter_factor": 0,
            "circuit_breaker_threshold": 5,
        },
    }
    calls = []
    payload = {
        "task_id": "recovery-retry",
        "dispatch_lease_token": "same-retry-lease",
        "dispatch_lease_phase": "execute",
    }

    def forward(url, endpoint, data, token=None):
        calls.append((url, endpoint, dict(data), token))
        if len(calls) == 1:
            raise RuntimeError("connection refused")
        return {
            "status": "success",
            "data": {"status": "completed"},
        }

    monkeypatch.setattr(autopilot_module, "_forward_to_worker", forward)
    monkeypatch.setattr(autopilot_module.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(
        autopilot_module,
        "get_repository_registry",
        lambda _app=None: SimpleNamespace(
            agent_repo=SimpleNamespace(
                get_by_url=lambda _url: SimpleNamespace(
                    token="worker-auth-token"
                )
            ),
            task_repo=SimpleNamespace(
                get_by_id=lambda _task_id: SimpleNamespace(
                    status="in_progress"
                )
            ),
        ),
    )
    monkeypatch.setattr(
        autonomous_loop,
        "_record_worker_failure",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        autonomous_loop,
        "_record_worker_success",
        lambda *_args, **_kwargs: None,
    )

    with app.app_context():
        result = autonomous_loop._forward_with_retry(
            "http://worker-retry:5001",
            "/tasks/recovery-retry/step/execute",
            payload,
            token="worker-auth-token",
        )

    assert result == {"status": "completed"}
    assert calls == [
        (
            "http://worker-retry:5001",
            "/tasks/recovery-retry/step/execute",
            payload,
            "worker-auth-token",
        ),
        (
            "http://worker-retry:5001",
            "/tasks/recovery-retry/step/execute",
            payload,
            "worker-auth-token",
        ),
    ]


@pytest.mark.parametrize(
    "failure_mode",
    ["unauthorized_response", "forbidden_response", "unauthorized_exception"],
)
def test_autopilot_recovery_auth_failure_is_not_retried(
    app,
    monkeypatch,
    failure_mode,
):
    from agent.routes.tasks import autopilot as autopilot_module

    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "hub_url", "http://hub:5000")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "autonomous_resilience": {
            "retry_attempts": 3,
            "retry_backoff_seconds": 0.01,
            "retry_max_backoff_seconds": 0.01,
            "retry_jitter_factor": 0,
            "circuit_breaker_threshold": 5,
        },
    }
    payload = {
        "task_id": "recovery-auth-failure",
        "dispatch_lease_token": "recovery-auth-lease",
        "dispatch_lease_phase": "execute",
    }
    endpoint = "/tasks/recovery-auth-failure/step/execute"
    calls = []

    def forward(url, path, data, token=None):
        calls.append((url, path, dict(data), token))
        if failure_mode == "unauthorized_response":
            return {
                "status": "error",
                "http_status": 401,
                "message": "unauthorized",
            }
        if failure_mode == "forbidden_response":
            return {
                "status": "error",
                "http_status": 403,
                "message": "forbidden",
            }
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(autopilot_module, "_forward_to_worker", forward)
    monkeypatch.setattr(
        autopilot_module,
        "get_repository_registry",
        lambda _app=None: SimpleNamespace(
            agent_repo=SimpleNamespace(
                get_by_url=lambda _url: SimpleNamespace(
                    token="current-worker-token"
                )
            )
        ),
    )
    worker_failures = []
    worker_successes = []
    monkeypatch.setattr(
        autonomous_loop,
        "_record_worker_failure",
        lambda *args, **kwargs: worker_failures.append((args, kwargs)),
    )
    monkeypatch.setattr(
        autonomous_loop,
        "_record_worker_success",
        lambda *args, **kwargs: worker_successes.append((args, kwargs)),
    )

    with app.app_context(), pytest.raises(RuntimeError, match="worker_forward_failed"):
        autonomous_loop._forward_with_retry(
            "http://worker:5001",
            endpoint,
            payload,
            token="stale-worker-token",
        )

    assert calls == [
        (
            "http://worker:5001",
            endpoint,
            payload,
            "current-worker-token",
        )
    ]
    assert len(worker_failures) == 1
    assert worker_failures[0][0] == (
        "http://worker:5001",
        f"forward_failed:{endpoint}",
    )
    assert worker_successes == []


def test_autopilot_hub_owned_step_uses_file_managed_hub_service_token(
    app,
    monkeypatch,
    tmp_path,
):
    from agent.routes.tasks import autopilot as autopilot_module

    hub_token = "hub-service-token-0123456789abcdef"
    token_file = tmp_path / "hub-service-token"
    token_file.write_text(hub_token, encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.setitem(app.config, "AGENT_TOKEN", None)
    monkeypatch.setitem(app.config, "AGENT_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "hub_url", "http://hub:5000")
    calls = []
    payload = {"task_id": "hub-owned-step"}
    endpoint = "/tasks/hub-owned-step/step/execute"

    def forward(url, path, data, token=None):
        calls.append((url, path, dict(data), token))
        return {"status": "success", "data": {"status": "completed"}}

    monkeypatch.setattr(autopilot_module, "_forward_to_worker", forward)
    monkeypatch.setattr(
        autonomous_loop,
        "_record_worker_success",
        lambda *_args, **_kwargs: None,
    )

    with app.app_context():
        result = autonomous_loop._forward_with_retry(
            "http://worker:5001",
            endpoint,
            payload,
            token="worker-service-token",
        )

    assert result == {"status": "completed"}
    assert calls == [("http://hub:5000", endpoint, payload, hub_token)]


@pytest.mark.parametrize(
    "failure_mode",
    ["unauthorized_response", "unauthorized_exception", "not_found"],
)
def test_autopilot_hub_self_failure_never_crosses_target_or_drops_auth(
    app,
    monkeypatch,
    tmp_path,
    failure_mode,
):
    from agent.routes.tasks import autopilot as autopilot_module

    hub_token = "hub-service-token-0123456789abcdef"
    token_file = tmp_path / "hub-service-token"
    token_file.write_text(hub_token, encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.setitem(app.config, "AGENT_TOKEN", None)
    monkeypatch.setitem(app.config, "AGENT_TOKEN_FILE", str(token_file))
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "autonomous_resilience": {
            "retry_attempts": 2,
            "retry_backoff_seconds": 0.01,
            "retry_max_backoff_seconds": 0.01,
            "retry_jitter_factor": 0,
            "circuit_breaker_threshold": 5,
        },
    }
    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "hub_url", "http://hub:5000")
    calls = []
    payload = {"task_id": "hub-owned-failure"}
    endpoint = "/tasks/hub-owned-failure/step/propose"

    def forward(url, path, data, token=None):
        calls.append((url, path, dict(data), token))
        if failure_mode == "unauthorized_response":
            return {"status": "error", "http_status": 401, "message": "unauthorized"}
        if failure_mode == "not_found":
            return {"status": "error", "http_status": 404, "message": "not found"}
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(autopilot_module, "_forward_to_worker", forward)
    worker_failures = []
    worker_successes = []
    monkeypatch.setattr(
        autonomous_loop,
        "_record_worker_failure",
        lambda *args, **kwargs: worker_failures.append((args, kwargs)),
    )
    monkeypatch.setattr(
        autonomous_loop,
        "_record_worker_success",
        lambda *args, **kwargs: worker_successes.append((args, kwargs)),
    )

    with app.app_context(), pytest.raises(RuntimeError, match="worker_forward_failed"):
        autonomous_loop._forward_with_retry(
            "http://worker:5001",
            endpoint,
            payload,
            token="worker-service-token",
        )

    assert calls == [("http://hub:5000", endpoint, payload, hub_token)]
    assert worker_failures == []
    assert worker_successes == []


@pytest.mark.parametrize(
    ("http_status", "reason_code", "reported_retryable"),
    [
        (401, "worker_authentication_rejected", True),
        (403, "worker_authentication_rejected", False),
        (409, "knowledge_index_execution_queue_context_stale", False),
    ],
)
def test_autopilot_hub_self_preserves_structured_non_retryable_failure(
    app,
    monkeypatch,
    tmp_path,
    http_status,
    reason_code,
    reported_retryable,
):
    from agent.routes.tasks import autopilot as autopilot_module

    hub_token = "hub-service-token-0123456789abcdef"
    token_file = tmp_path / "hub-service-token"
    token_file.write_text(hub_token, encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.setitem(app.config, "AGENT_TOKEN", None)
    monkeypatch.setitem(app.config, "AGENT_TOKEN_FILE", str(token_file))
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "autonomous_resilience": {
            "retry_attempts": 3,
            "retry_backoff_seconds": 0.01,
            "retry_max_backoff_seconds": 0.01,
            "retry_jitter_factor": 0,
            "circuit_breaker_threshold": 5,
        },
    }
    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "hub_url", "http://hub:5000")
    calls = []
    worker_failures = []
    worker_successes = []

    def forward(url, path, data, token=None):
        calls.append((url, path, dict(data), token))
        return {
            "status": "error",
            "http_status": http_status,
            "message": reason_code,
            "details": {
                "error_type": "WorkerForwardingError",
                "retryable": reported_retryable,
                "details": {"reason_code": reason_code},
            },
        }

    monkeypatch.setattr(autopilot_module, "_forward_to_worker", forward)
    monkeypatch.setattr(
        autonomous_loop,
        "_record_worker_failure",
        lambda *args, **kwargs: worker_failures.append((args, kwargs)),
    )
    monkeypatch.setattr(
        autonomous_loop,
        "_record_worker_success",
        lambda *args, **kwargs: worker_successes.append((args, kwargs)),
    )
    endpoint = "/tasks/codecompass-hub-self/step/execute"
    payload = {"task_id": "codecompass-hub-self"}

    with app.app_context(), pytest.raises(
        RuntimeError,
        match=reason_code,
    ):
        autonomous_loop._forward_with_retry(
            "http://worker:5001",
            endpoint,
            payload,
            token="worker-service-token",
        )

    assert calls == [("http://hub:5000", endpoint, payload, hub_token)]
    assert worker_failures == []
    assert worker_successes == []


@pytest.mark.parametrize("failure_mode", ["structured_502", "timeout"])
def test_autopilot_retries_only_retryable_hub_self_failures(
    app,
    monkeypatch,
    tmp_path,
    failure_mode,
):
    from agent.routes.tasks import autopilot as autopilot_module

    hub_token = "hub-service-token-0123456789abcdef"
    token_file = tmp_path / "hub-service-token"
    token_file.write_text(hub_token, encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.setitem(app.config, "AGENT_TOKEN", None)
    monkeypatch.setitem(app.config, "AGENT_TOKEN_FILE", str(token_file))
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "autonomous_resilience": {
            "retry_attempts": 3,
            "retry_backoff_seconds": 0.01,
            "retry_max_backoff_seconds": 0.01,
            "retry_jitter_factor": 0,
            "circuit_breaker_threshold": 5,
        },
    }
    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "hub_url", "http://hub:5000")
    monkeypatch.setattr(autopilot_module.time, "sleep", lambda _delay: None)
    calls = []

    def forward(url, path, data, token=None):
        calls.append((url, path, dict(data), token))
        if len(calls) < 3:
            if failure_mode == "timeout":
                raise TimeoutError("hub self request timed out")
            return {
                "status": "error",
                "http_status": 502,
                "message": "forwarding_failed",
                "details": {"retryable": True},
            }
        return {"status": "success", "data": {"status": "completed"}}

    monkeypatch.setattr(autopilot_module, "_forward_to_worker", forward)
    endpoint = "/tasks/codecompass-retry/step/execute"
    payload = {"task_id": "codecompass-retry"}

    with app.app_context():
        result = autonomous_loop._forward_with_retry(
            "http://worker:5001",
            endpoint,
            payload,
            token="worker-service-token",
        )

    assert result == {"status": "completed"}
    assert calls == [
        ("http://hub:5000", endpoint, payload, hub_token),
        ("http://hub:5000", endpoint, payload, hub_token),
        ("http://hub:5000", endpoint, payload, hub_token),
    ]


def test_autopilot_hub_self_uses_bound_knowledge_index_runtime_timeout(
    app,
    monkeypatch,
    tmp_path,
):
    from agent.routes.tasks import autopilot as autopilot_module

    hub_token = "hub-service-token-0123456789abcdef"
    token_file = tmp_path / "hub-service-token"
    token_file.write_text(hub_token, encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.setitem(app.config, "AGENT_TOKEN", None)
    monkeypatch.setitem(app.config, "AGENT_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "hub_url", "http://hub:5000")
    task = build_execution_task(max_runtime_seconds=749)
    task_id = task["id"]
    monkeypatch.setattr(
        autopilot_module,
        "get_repository_registry",
        lambda _app=None: SimpleNamespace(
            task_repo=SimpleNamespace(
                get_by_id=lambda requested_id: (
                    task if requested_id == task_id else None
                )
            )
        ),
    )
    calls = []

    def forward(url, path, data, token=None, **options):
        calls.append((url, path, dict(data), token, dict(options)))
        return {"status": "success", "data": {"status": "completed"}}

    monkeypatch.setattr(autopilot_module, "_forward_to_worker", forward)
    endpoint = f"/tasks/{task_id}/step/execute"
    payload = {"task_id": task_id}

    with app.app_context():
        result = autonomous_loop._forward_with_retry(
            "http://worker:5001",
            endpoint,
            payload,
            token="worker-service-token",
        )

    assert result == {"status": "completed"}
    assert len(calls) == 1
    url, path, forwarded_payload, token, options = calls[0]
    assert (url, path, forwarded_payload, token) == (
        "http://hub:5000",
        endpoint,
        payload,
        hub_token,
    )
    deadline = options["transport_deadline"]
    assert deadline.budget_seconds == 779
    assert 778 < deadline.remaining_seconds() <= 779


def test_governed_retries_share_one_absolute_deadline(
    app,
    monkeypatch,
    tmp_path,
):
    from agent.routes.tasks import autopilot as autopilot_module

    hub_token = "hub-service-token-0123456789abcdef"
    token_file = tmp_path / "hub-service-token"
    token_file.write_text(hub_token, encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.setitem(app.config, "AGENT_TOKEN", None)
    monkeypatch.setitem(app.config, "AGENT_TOKEN_FILE", str(token_file))
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "autonomous_resilience": {
            "retry_attempts": 3,
            "retry_backoff_seconds": 0,
            "retry_max_backoff_seconds": 0,
            "retry_jitter_factor": 0,
        },
    }
    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "hub_url", "http://hub:5000")
    task = build_execution_task(max_runtime_seconds=60)
    monkeypatch.setattr(
        autopilot_module,
        "get_repository_registry",
        lambda _app=None: SimpleNamespace(
            task_repo=SimpleNamespace(get_by_id=lambda _task_id: task)
        ),
    )
    deadlines = []

    def forward(_url, _path, _data, token=None, **options):
        assert token == hub_token
        deadlines.append(options["transport_deadline"])
        if len(deadlines) == 1:
            raise TimeoutError("transient transport timeout")
        return {"status": "success", "data": {"status": "completed"}}

    monkeypatch.setattr(autopilot_module, "_forward_to_worker", forward)

    with app.app_context():
        result = autonomous_loop._forward_with_retry(
            "http://worker:5001",
            f"/tasks/{task['id']}/step/execute",
            {"task_id": task["id"]},
            token="worker-service-token",
        )

    assert result == {"status": "completed"}
    assert len(deadlines) == 2
    assert deadlines[0] is deadlines[1]


def test_governed_permanent_response_error_is_not_retried(
    app,
    monkeypatch,
    tmp_path,
):
    from agent.routes.tasks import autopilot as autopilot_module
    from agent.services.worker_forward_transport import (
        WorkerForwardPermanentTransportError,
    )

    hub_token = "hub-service-token-0123456789abcdef"
    token_file = tmp_path / "hub-service-token"
    token_file.write_text(hub_token, encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.setitem(app.config, "AGENT_TOKEN", None)
    monkeypatch.setitem(app.config, "AGENT_TOKEN_FILE", str(token_file))
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "autonomous_resilience": {
            "retry_attempts": 5,
            "retry_backoff_seconds": 0,
            "retry_max_backoff_seconds": 0,
            "retry_jitter_factor": 0,
        },
    }
    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "hub_url", "http://hub:5000")
    task = build_execution_task(max_runtime_seconds=60)
    monkeypatch.setattr(
        autopilot_module,
        "get_repository_registry",
        lambda _app=None: SimpleNamespace(
            task_repo=SimpleNamespace(get_by_id=lambda _task_id: task)
        ),
    )
    calls = []

    def forward(*_args, **_kwargs):
        calls.append(True)
        raise WorkerForwardPermanentTransportError(
            "knowledge_index_worker_response_too_large"
        )

    monkeypatch.setattr(autopilot_module, "_forward_to_worker", forward)

    with app.app_context(), pytest.raises(
        RuntimeError,
        match="knowledge_index_worker_response_too_large",
    ):
        autonomous_loop._forward_with_retry(
            "http://worker:5001",
            f"/tasks/{task['id']}/step/execute",
            {"task_id": task["id"]},
            token="worker-service-token",
        )

    assert calls == [True]


@pytest.mark.parametrize(
    "worker_context",
    [
        {},
        {
            "knowledge_index_job": {
                "schema": "ananta.knowledge_index_job.unknown"
            }
        },
    ],
)
def test_autopilot_codecompass_execute_never_downgrades_unknown_binding(
    app,
    monkeypatch,
    worker_context,
):
    from agent.routes.tasks import autopilot as autopilot_module

    task_id = "codecompass-invalid-binding"
    task = {
        "id": task_id,
        "task_kind": "codecompass_index_build",
        "worker_execution_context": worker_context,
    }
    monkeypatch.setattr(settings, "role", "worker")
    monkeypatch.setattr(
        autopilot_module,
        "get_repository_registry",
        lambda _app=None: SimpleNamespace(
            task_repo=SimpleNamespace(get_by_id=lambda _task_id: task)
        ),
    )
    calls = []
    monkeypatch.setattr(
        autopilot_module,
        "_forward_to_worker",
        lambda *_args, **_kwargs: calls.append(True),
    )

    with app.app_context(), pytest.raises(
        RuntimeError,
        match="knowledge_index_execution_binding",
    ):
        autonomous_loop._forward_with_retry(
            "http://worker:5001",
            f"/tasks/{task_id}/step/execute",
            {"task_id": task_id},
            token="worker-service-token",
        )

    assert calls == []


@pytest.mark.parametrize(
    ("response", "expected_successes", "expected_failures"),
    [
        (
            {"status": "success", "data": {"status": "completed"}},
            1,
            0,
        ),
        ({"status": "success", "data": []}, 0, 1),
    ],
)
def test_autopilot_worker_circuit_success_requires_normalized_envelope(
    app,
    monkeypatch,
    response,
    expected_successes,
    expected_failures,
):
    from agent.routes.tasks import autopilot as autopilot_module

    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "autonomous_resilience": {
            "retry_attempts": 1,
            "retry_backoff_seconds": 0.01,
            "retry_max_backoff_seconds": 0.01,
            "retry_jitter_factor": 0,
            "circuit_breaker_threshold": 5,
        },
    }
    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "hub_url", "http://hub:5000")
    monkeypatch.setattr(
        autopilot_module,
        "get_repository_registry",
        lambda _app=None: SimpleNamespace(
            agent_repo=SimpleNamespace(
                get_by_url=lambda _url: SimpleNamespace(
                    token="current-worker-token"
                )
            )
        ),
    )
    monkeypatch.setattr(
        autopilot_module,
        "_forward_to_worker",
        lambda *_args, **_kwargs: response,
    )
    successes = []
    failures = []
    monkeypatch.setattr(
        autonomous_loop,
        "_record_worker_success",
        lambda *args, **kwargs: successes.append((args, kwargs)),
    )
    monkeypatch.setattr(
        autonomous_loop,
        "_record_worker_failure",
        lambda *args, **kwargs: failures.append((args, kwargs)),
    )
    payload = {
        "task_id": "circuit-envelope",
        "dispatch_lease_token": "recovery-lease",
        "dispatch_lease_phase": "execute",
    }

    expectation = (
        contextlib.nullcontext()
        if expected_successes
        else pytest.raises(RuntimeError, match="worker_empty_payload")
    )
    with app.app_context(), expectation:
        autonomous_loop._forward_with_retry(
            "http://worker:5001",
            "/tasks/circuit-envelope/step/execute",
            payload,
            token="stale-worker-token",
        )

    assert len(successes) == expected_successes
    assert len(failures) == expected_failures
    if successes:
        assert successes[0][0] == ("http://worker:5001",)
    if failures:
        assert failures[0][0] == (
            "http://worker:5001",
            "forward_failed:/tasks/circuit-envelope/step/execute",
        )


def test_autopilot_unsafe_hub_token_file_prevents_transport(
    app,
    monkeypatch,
    tmp_path,
):
    from agent.auth import AgentTokenConfigurationError
    from agent.routes.tasks import autopilot as autopilot_module

    token_file = tmp_path / "unsafe-hub-service-token"
    token_file.write_text(
        "hub-service-token-0123456789abcdef",
        encoding="utf-8",
    )
    token_file.chmod(0o666)
    monkeypatch.setitem(app.config, "AGENT_TOKEN", None)
    monkeypatch.setitem(app.config, "AGENT_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(settings, "role", "hub")
    monkeypatch.setattr(settings, "hub_url", "http://hub:5000")
    calls = []
    monkeypatch.setattr(
        autopilot_module,
        "_forward_to_worker",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with app.app_context(), pytest.raises(
        AgentTokenConfigurationError,
        match="permissions are unsafe",
    ):
        autonomous_loop._forward_with_retry(
            "http://worker:5001",
            "/tasks/hub-token-failure/step/execute",
            {"task_id": "hub-token-failure"},
            token="worker-service-token",
        )

    assert calls == []


def test_autopilot_retries_proposal_with_next_strategy_model(app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "adaptive_model_routing_enabled": False,
        "task_kind_model_overrides": {"coding": "model-a"},
        "autopilot_strategy_fallback_models": ["model-b"],
        "autopilot_strategy_max_attempts": 3,
        "quality_gates": {"enabled": False, "autopilot_enforce": False},
    }
    task_repo.save(TaskDB(id="strategy-retry-1", title="Retry Strategy", status="todo", task_kind="coding"))
    agent_repo.save(
        AgentInfoDB(url="http://worker-strategy:5001", name="worker-strategy", role="worker", token="tok", status="online")
    )
    propose_models: list[str | None] = []

    def _fake_forward(worker_url, endpoint, data, token=None):
        if endpoint.endswith("/step/propose"):
            propose_models.append(data.get("model"))
            if len(propose_models) == 1:
                return {"status": "success", "data": {"reason": "bad", "raw": "{}"}}
            return {"status": "success", "data": {"reason": "ok", "command": "echo ok", "raw": "{\"command\":\"echo ok\"}"}}
        return {"status": "success", "data": {"status": "completed", "exit_code": 0, "output": "ok"}}

    monkeypatch.setattr("agent.routes.tasks.autopilot._forward_to_worker", _fake_forward)
    with app.app_context():
        res = autonomous_loop.tick_once()
        updated = task_repo.get_by_id("strategy-retry-1")
    assert res["reason"] == "ok"
    assert res["dispatched"] == 1
    assert propose_models[0] == "model-a"
    assert len(propose_models) >= 2
    assert propose_models[1] and propose_models[1] != "model-a"
    assert updated is not None and updated.status == "completed"
    model_selection = dict((updated.last_proposal or {}).get("model_selection") or {})
    assert model_selection.get("selected_model") == propose_models[1]
    assert model_selection.get("attempt") == 2


def test_autopilot_recovers_embedded_json_from_raw_proposal(app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "adaptive_model_routing_enabled": False,
        "task_kind_model_overrides": {"coding": "model-a"},
        "autopilot_strategy_fallback_models": ["model-b"],
        "autopilot_strategy_max_attempts": 2,
        "quality_gates": {"enabled": False, "autopilot_enforce": False},
    }
    task_repo.save(TaskDB(id="strategy-embedded-1", title="Embedded JSON Strategy", status="todo", task_kind="coding"))
    agent_repo.save(
        AgentInfoDB(url="http://worker-embedded:5001", name="worker-embedded", role="worker", token="tok", status="online")
    )
    propose_models: list[str | None] = []
    raw_output = (
        "Traceback (most recent call last):\n"
        "ValueError: transient parse issue\n"
        '{"reason":"embedded json","command":"echo ok"}'
    )

    def _fake_forward(worker_url, endpoint, data, token=None):
        if endpoint.endswith("/step/propose"):
            propose_models.append(data.get("model"))
            return {"status": "success", "data": {"reason": raw_output, "raw": raw_output}}
        return {"status": "success", "data": {"status": "completed", "exit_code": 0, "output": "ok"}}

    monkeypatch.setattr("agent.routes.tasks.autopilot._forward_to_worker", _fake_forward)
    with app.app_context():
        res = autonomous_loop.tick_once()
        updated = task_repo.get_by_id("strategy-embedded-1")
    assert res["reason"] == "ok"
    assert res["dispatched"] == 1
    assert propose_models == ["model-a"]
    assert updated is not None and updated.status == "completed"
    assert (updated.last_proposal or {}).get("reason") == "embedded json"
    assert (updated.last_proposal or {}).get("command") == "echo ok"
    model_selection = dict((updated.last_proposal or {}).get("model_selection") or {})
    assert model_selection.get("selected_model") == "model-a"
    assert model_selection.get("attempt") == 1


def test_autopilot_recovers_fenced_cmd_payload_with_trailing_commas(app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "adaptive_model_routing_enabled": False,
        "task_kind_model_overrides": {"coding": "model-a"},
        "autopilot_strategy_fallback_models": [],
        "autopilot_strategy_max_attempts": 1,
        "quality_gates": {"enabled": False, "autopilot_enforce": False},
    }
    task_repo.save(TaskDB(id="strategy-fenced-cmd-1", title="Fenced Command Recovery", status="todo", task_kind="coding"))
    agent_repo.save(
        AgentInfoDB(url="http://worker-fenced-cmd:5001", name="worker-fenced-cmd", role="worker", token="tok", status="online")
    )
    raw_output = '<|im_start|>\n```json\n{"summary":"repair me","cmd":"echo ok",}\n```\n'

    def _fake_forward(worker_url, endpoint, data, token=None):
        if endpoint.endswith("/step/propose"):
            return {"status": "success", "data": {"reason": raw_output, "raw": raw_output}}
        return {"status": "success", "data": {"status": "completed", "exit_code": 0, "output": "ok"}}

    monkeypatch.setattr("agent.routes.tasks.autopilot._forward_to_worker", _fake_forward)
    with app.app_context():
        res = autonomous_loop.tick_once()
        updated = task_repo.get_by_id("strategy-fenced-cmd-1")
    assert res["reason"] == "ok"
    assert res["dispatched"] == 1
    assert updated is not None and updated.status == "completed"
    assert (updated.last_proposal or {}).get("reason") == "repair me"
    assert (updated.last_proposal or {}).get("command") == "echo ok"


def test_autopilot_does_not_treat_scalar_tool_list_as_executable_proposal(app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "adaptive_model_routing_enabled": False,
        "task_kind_model_overrides": {"coding": "model-a"},
        "autopilot_strategy_fallback_models": ["model-b"],
        "autopilot_strategy_max_attempts": 2,
        "autopilot_strategy_retry_delay_seconds": 15,
        "quality_gates": {"enabled": False, "autopilot_enforce": False},
    }
    task_repo.save(TaskDB(id="strategy-invalid-tools-1", title="Invalid Tool Calls", status="todo", task_kind="coding"))
    agent_repo.save(
        AgentInfoDB(url="http://worker-invalid-tools:5001", name="worker-invalid-tools", role="worker", token="tok", status="online")
    )
    attempts: list[str | None] = []
    raw_output = (
        "Traceback (most recent call last):\n"
        "ValueError: transient parse issue\n"
        '{"tool_calls":["tools"]}'
    )

    def _fake_forward(worker_url, endpoint, data, token=None):
        if endpoint.endswith("/step/propose"):
            attempts.append(data.get("model"))
            return {"status": "success", "data": {"reason": raw_output, "raw": raw_output}}
        raise AssertionError("execute must not be called for invalid scalar tool list proposals")

    monkeypatch.setattr("agent.routes.tasks.autopilot._forward_to_worker", _fake_forward)
    started = time.time()
    with app.app_context():
        res = autonomous_loop.tick_once()
        updated = task_repo.get_by_id("strategy-invalid-tools-1")
    assert res["reason"] == "ok"
    assert res["dispatched"] == 0
    assert attempts[0] == "model-a"
    assert len(attempts) >= 2
    assert attempts[1] and attempts[1] != "model-a"
    assert updated is not None and updated.status == "todo"
    assert float(updated.manual_override_until or 0) >= started + 10
    assert any((entry.get("event_type") == "autopilot_strategy_exhausted") for entry in (updated.history or []))


def test_autopilot_strategy_exhaustion_returns_task_to_hub_queue(app, monkeypatch):
    assert _should_terminalize_no_executable_strategy([{"failure_type": "invalid_proposal"}]) is True
    assert _should_terminalize_no_executable_strategy([{"failure_type": "no_executable_step"}]) is True
    assert _should_terminalize_no_executable_strategy([{"failure_type": "proposal_budget_exhausted"}]) is True
    assert _should_terminalize_no_executable_strategy([{"failure_type": "forward_error"}]) is False


def test_autopilot_does_not_resurrect_stopped_model_recovery_source(
    app,
    monkeypatch,
):
    monkeypatch.setattr(settings, "role", "hub")
    task_repo.save(
        TaskDB(
            id="stopped-model-recovery-source",
            title="Stopped recovery source",
            status="waiting_for_review",
            last_output="command not found",
            updated_at=time.time() - 180,
            status_reason_details={
                "model_recovery_strategy": {
                    "status": "stopped",
                    "actions": ["stop"],
                    "reason_code": "model_recovery_stopped",
                }
            },
        )
    )

    forwarded: list[tuple] = []

    def _unexpected_forward(*args, **kwargs):
        forwarded.append((args, kwargs))
        raise AssertionError(
            "a stopped Hub Recovery source must not be forwarded"
        )

    monkeypatch.setattr(
        "agent.routes.tasks.autopilot._forward_to_worker",
        _unexpected_forward,
    )
    with app.app_context():
        first = autonomous_loop.tick_once()
        second = autonomous_loop.tick_once()
        updated = task_repo.get_by_id(
            "stopped-model-recovery-source"
        )

    assert first["dispatched"] == 0
    assert second["dispatched"] == 0
    assert forwarded == []
    assert updated is not None
    assert updated.status == "waiting_for_review"
    assert not any(
        str(entry.get("event_type") or "").startswith(
            ("recover_waiting_review", "waiting_for_review_timeout")
        )
        for entry in (updated.history or [])
    )


def test_autopilot_invalid_proposal_terminalizes_after_threshold(app, monkeypatch):
    assert _should_terminalize_no_executable_strategy([{"failure_type": "invalid_proposal"}]) is True
    assert _should_terminalize_no_executable_strategy([{"failure_type": "no_executable_step"}]) is True
    assert _should_terminalize_no_executable_strategy([{"failure_type": "proposal_budget_exhausted"}]) is True
    assert _should_terminalize_no_executable_strategy([{"failure_type": "forward_error"}]) is False


def test_autopilot_retries_proposal_with_temperature_profile(app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "adaptive_model_routing_enabled": False,
        "task_kind_model_overrides": {"analysis": "model-temp"},
        "autopilot_strategy_fallback_models": [],
        "autopilot_strategy_temperature_profiles": [0.2, 0.9],
        "autopilot_strategy_max_attempts": 3,
        "quality_gates": {"enabled": False, "autopilot_enforce": False},
    }
    task_repo.save(TaskDB(id="strategy-temp-1", title="Temp Strategy", status="todo", task_kind="analysis"))
    agent_repo.save(
        AgentInfoDB(url="http://worker-temp:5001", name="worker-temp", role="worker", token="tok", status="online")
    )
    propose_attempts: list[tuple[str | None, float | None]] = []

    def _fake_forward(worker_url, endpoint, data, token=None):
        if endpoint.endswith("/step/propose"):
            propose_attempts.append((data.get("model"), data.get("temperature")))
            if len(propose_attempts) == 1:
                return {"status": "success", "data": {"reason": "bad", "raw": "{}"}}
            return {"status": "success", "data": {"reason": "ok", "command": "echo ok", "raw": "{\"command\":\"echo ok\"}"}}
        return {"status": "success", "data": {"status": "completed", "exit_code": 0, "output": "ok"}}

    monkeypatch.setattr("agent.routes.tasks.autopilot._forward_to_worker", _fake_forward)
    with app.app_context():
        res = autonomous_loop.tick_once()
        updated = task_repo.get_by_id("strategy-temp-1")
    assert res["reason"] == "ok"
    assert res["dispatched"] == 1
    assert propose_attempts[0] == ("model-temp", 0.2)
    assert len(propose_attempts) >= 2
    assert propose_attempts[1][0] and propose_attempts[1][0] != "model-temp"
    assert float(propose_attempts[1][1] or 0.0) == 0.2
    assert updated is not None and updated.status == "completed"
    model_selection = dict((updated.last_proposal or {}).get("model_selection") or {})
    assert model_selection.get("selected_model") == propose_attempts[1][0]
    assert float(model_selection.get("selected_temperature") or 0.0) == 0.2


def test_autopilot_skips_model_with_insufficient_context_window(app, monkeypatch):
    monkeypatch.setattr(settings, "role", "hub")
    app.config["AGENT_CONFIG"] = {
        **(app.config.get("AGENT_CONFIG") or {}),
        "adaptive_model_routing_enabled": False,
        "task_kind_model_overrides": {"analysis": "model-small"},
        "autopilot_strategy_fallback_models": [],
        "autopilot_strategy_temperature_profiles": [],
        "autopilot_strategy_max_attempts": 5,
        "quality_gates": {"enabled": False, "autopilot_enforce": False},
    }
    task_repo.save(
        TaskDB(
            id="strategy-ctx-1",
            title="Context Strategy",
            status="todo",
            task_kind="analysis",
            description="x" * 8000,
        )
    )
    agent_repo.save(
        AgentInfoDB(url="http://worker-ctx:5001", name="worker-ctx", role="worker", token="tok", status="online")
    )

    monkeypatch.setattr(
        "agent.routes.tasks.autopilot_tick_engine._runtime_model_capabilities",
        lambda _loop: {
            "runtime": {"default_provider": "lmstudio", "lmstudio": {"ok": True, "candidate_count": 1}},
            "models": {"model-small": {"provider": "lmstudio", "context_length": 256}},
        },
    )

    propose_models: list[str | None] = []

    def _fake_forward(worker_url, endpoint, data, token=None):
        if endpoint.endswith("/step/propose"):
            propose_models.append(data.get("model"))
            return {"status": "success", "data": {"reason": "ok", "command": "echo ok"}}
        return {"status": "success", "data": {"status": "completed", "exit_code": 0, "output": "ok"}}

    monkeypatch.setattr("agent.routes.tasks.autopilot._forward_to_worker", _fake_forward)
    with app.app_context():
        res = autonomous_loop.tick_once()
        updated = task_repo.get_by_id("strategy-ctx-1")

    assert res["reason"] == "ok"
    assert res["dispatched"] == 1
    assert propose_models
    assert propose_models[0] != "model-small"
    assert updated is not None
    assert any((entry.get("event_type") == "autopilot_strategy_attempt_skipped") for entry in (updated.history or []))
