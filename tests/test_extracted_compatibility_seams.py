from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from agent.cli_backends import simple_command_runners
from agent.repositories import tasks
from agent.routes import sgpt as sgpt_route
from agent.routes import sgpt_execute


def test_sgpt_execute_injects_live_facade_policy_without_eager_logger(
    client,
    admin_auth_header,
    monkeypatch,
) -> None:
    def lazy_logger():
        raise AssertionError("_log must remain lazy at the route boundary")

    policy_functions = {
        "_extract_user_id": lambda: "patched-user",
        "_parse_source_types": lambda _value: ["repo"],
        "normalize_task_kind": lambda *_args: "patched-kind",
        "runtime_routing_config": lambda *_args: {"policy_version": "patched"},
        "resolve_cli_backend": lambda **_kwargs: (
            "patched",
            "patched-reason",
            {"policy_version": "patched"},
        ),
        "normalize_backend_flags": lambda *_args: ([], []),
        "resolve_lora_adapter_routing": lambda **_kwargs: {},
        "build_trace_record": lambda **_kwargs: {"trace_id": "patched-trace"},
    }
    monkeypatch.setattr(sgpt_route, "ALLOWED_BACKENDS", {"patched"})
    monkeypatch.setattr(
        sgpt_route,
        "BACKEND_ALIASES",
        {"legacy-patched": "patched"},
    )
    monkeypatch.setattr(sgpt_route, "_log", lazy_logger)
    monkeypatch.setattr(
        sgpt_route,
        "is_rate_limited",
        lambda _user_id: False,
    )
    for name, dependency in policy_functions.items():
        monkeypatch.setattr(sgpt_route, name, dependency)

    def capture_runtime(runtime: sgpt_execute.SgptExecuteRuntime):
        assert runtime.get_logger is lazy_logger
        assert runtime.policy.allowed_backends() == {"patched"}
        assert runtime.policy.normalize_backend_name("legacy-patched") == "patched"
        assert runtime.policy.extract_user_id is policy_functions["_extract_user_id"]
        assert runtime.policy.parse_source_types is policy_functions["_parse_source_types"]
        for field_name in (
            "normalize_task_kind",
            "runtime_routing_config",
            "resolve_cli_backend",
            "normalize_backend_flags",
            "resolve_lora_adapter_routing",
            "build_trace_record",
        ):
            assert getattr(runtime.policy, field_name) is policy_functions[field_name]
        return {"status": "success", "data": {"captured": True}}

    monkeypatch.setattr(
        sgpt_execute,
        "execute_sgpt_request",
        capture_runtime,
    )

    response = client.post(
        "/api/sgpt/execute",
        json={"prompt": "compatibility", "backend": "legacy-patched"},
        headers=admin_auth_header,
    )

    assert response.status_code == 200
    assert response.json["data"]["captured"] is True


def test_auxiliary_repositories_resolve_tasks_monkeypatches_dynamically(
    monkeypatch,
) -> None:
    class SortableField:
        def desc(self):
            return "patched-desc"

        def asc(self):
            return "patched-asc"

    class PatchedArchivedTask:
        archived_at = SortableField()
        id = SortableField()

    class PatchedAgentSession:
        pass

    class PatchedToolCall:
        pass

    class PatchedPolicySnapshot:
        pass

    class Statement:
        def order_by(self, *values):
            assert values == ("patched-desc", "patched-asc")
            return self

        def offset(self, value):
            assert value == 3
            return self

        def limit(self, value):
            assert value == 7
            return self

    class Result:
        def all(self):
            return ["patched-row"]

    session_calls = []
    select_calls = []

    class PatchedSession:
        def __init__(self, engine):
            session_calls.append(("engine", engine))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, model, record_id):
            session_calls.append(("get", model, record_id))
            return model

        def exec(self, statement):
            session_calls.append(("exec", statement))
            return Result()

    def patched_select(model):
        select_calls.append(model)
        return Statement()

    repositories = (
        tasks.ArchivedTaskRepository(),
        tasks.AgentSessionRepository(),
        tasks.ToolCallRepository(),
        tasks.PolicySnapshotRepository(),
    )
    monkeypatch.setattr(tasks, "_engine", lambda: "patched-engine")
    monkeypatch.setattr(tasks, "Session", PatchedSession)
    monkeypatch.setattr(tasks, "select", patched_select)
    monkeypatch.setattr(
        tasks,
        "ArchivedTaskDB",
        PatchedArchivedTask,
    )
    monkeypatch.setattr(
        tasks,
        "AgentSessionDB",
        PatchedAgentSession,
    )
    monkeypatch.setattr(tasks, "ToolCallDB", PatchedToolCall)
    monkeypatch.setattr(
        tasks,
        "PolicySnapshotDB",
        PatchedPolicySnapshot,
    )

    expected_models = (
        PatchedArchivedTask,
        PatchedAgentSession,
        PatchedToolCall,
        PatchedPolicySnapshot,
    )
    for repository, model in zip(
        repositories,
        expected_models,
        strict=True,
    ):
        assert repository.get_by_id("patched-id") is model

    assert repositories[0].get_all(limit=7, offset=3) == ["patched-row"]
    assert select_calls == [PatchedArchivedTask]
    assert all(call == ("engine", "patched-engine") for call in session_calls if call[0] == "engine")
    assert [call[1] for call in session_calls if call[0] == "get"] == list(expected_models)


def test_aider_copies_environment_only_after_permit_acquisition() -> None:
    events = []

    class TrackingEnvironment(dict):
        def copy(self):
            events.append("environment-copy")
            return super().copy()

    @contextmanager
    def acquire_permit(_backend, *, timeout):
        assert timeout == 17
        events.append("permit-entered")
        yield SimpleNamespace(acquired=True)
        events.append("permit-exited")

    def run_process(*_args, **_kwargs):
        events.append("process-run")
        return SimpleNamespace(
            returncode=0,
            stdout="ok",
            stderr="",
        )

    result = simple_command_runners.run_aider_command(
        "prompt",
        None,
        17,
        settings=SimpleNamespace(
            aider_path="aider",
            aider_default_model=None,
        ),
        which=lambda _binary: "/usr/bin/aider",
        run_process=run_process,
        acquire_permit=acquire_permit,
        logger=SimpleNamespace(info=lambda *_args: None),
        environ=TrackingEnvironment(),
    )

    assert result == (0, "ok", "")
    assert events == [
        "permit-entered",
        "environment-copy",
        "process-run",
        "permit-exited",
    ]


def test_aider_does_not_copy_environment_when_permit_is_denied() -> None:
    class CopyMustNotRun(dict):
        def copy(self):
            raise AssertionError("environment copied before permit")

    @contextmanager
    def deny_permit(_backend, *, timeout):
        assert timeout == 9
        yield SimpleNamespace(acquired=False)

    result = simple_command_runners.run_aider_command(
        "prompt",
        None,
        9,
        settings=SimpleNamespace(
            aider_path="aider",
            aider_default_model=None,
        ),
        which=lambda _binary: "/usr/bin/aider",
        run_process=lambda *_args, **_kwargs: None,
        acquire_permit=deny_permit,
        logger=SimpleNamespace(),
        environ=CopyMustNotRun(),
    )

    assert result == (
        -1,
        "",
        "Backend 'aider' ist ausgelastet (semaphore_exhausted)",
    )
