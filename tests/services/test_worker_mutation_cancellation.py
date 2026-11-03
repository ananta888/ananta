"""Terminal-task withdrawal check for in-flight worker mutation loops."""

from types import SimpleNamespace

import agent.services.worker_mutation_cancellation as mod


class _Repo:
    def __init__(self, task):
        self._task = task

    def get_by_id(self, _tid):
        return self._task


class _Registry:
    def __init__(self, task):
        self.task_repo = _Repo(task)


def test_without_task_binding_execution_continues(monkeypatch):
    def unexpected_repo(*_args, **_kwargs):
        raise AssertionError("an unbound execution must never consult the registry")

    monkeypatch.setattr(mod, "get_repository_registry", unexpected_repo)
    assert mod.is_mutation_execution_withdrawn(None) is False
    assert mod.is_mutation_execution_withdrawn("   ") is False


def test_missing_task_withdraws_execution(monkeypatch):
    monkeypatch.setattr(mod, "get_repository_registry", lambda *a, **k: _Registry(None))
    assert mod.is_mutation_execution_withdrawn("t-gone") is True


def test_terminal_statuses_withdraw_execution(monkeypatch):
    for status in ("failed", "completed", "cancelled", "verification_failed", "aborted", "timeout", "archived"):
        monkeypatch.setattr(mod, "get_repository_registry", lambda *a, **k: _Registry(SimpleNamespace(status=status)))
        assert mod.is_mutation_execution_withdrawn("t-1") is True, status


def test_active_statuses_continue_execution(monkeypatch):
    for status in ("proposing", "running", "assigned", "todo", "waiting_review"):
        monkeypatch.setattr(mod, "get_repository_registry", lambda *a, **k: _Registry(SimpleNamespace(status=status)))
        assert mod.is_mutation_execution_withdrawn("t-1") is False, status


def test_repository_errors_continue_execution(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("transient db failure")

    monkeypatch.setattr(mod, "get_repository_registry", boom)
    assert mod.is_mutation_execution_withdrawn("t-1") is False
