from types import SimpleNamespace

from agent.services.task_admin_service import TaskAdminService


def test_cancel_forwards_to_assigned_worker(monkeypatch):
    service = TaskAdminService()

    task = SimpleNamespace(id="task-1", status="todo", assigned_agent_url="http://worker-a:5000")
    monkeypatch.setattr(
        "agent.services.task_admin_service.get_repository_registry",
        lambda: SimpleNamespace(task_repo=SimpleNamespace(get_by_id=lambda _tid: task)),
    )

    calls = []

    class _Resp:
        status_code = 200

    def _fake_post(url, timeout):
        calls.append({"url": url, "timeout": timeout})
        return _Resp()

    monkeypatch.setattr("agent.services.task_admin_service.requests.post", _fake_post)
    monkeypatch.setattr("agent.services.task_admin_service.update_local_task_status", lambda *args, **kwargs: None)
    monkeypatch.setattr("agent.services.task_admin_service.log_audit", lambda *args, **kwargs: None)

    ok, msg, data = service.intervene_task(task_id="task-1", action="cancel", actor="test")
    assert ok is True
    assert msg == "ok"
    assert calls and calls[0]["url"] == "http://worker-a:5000/tasks/task-1/cancel"
    forward = data.get("worker_cancel_forward") or {}
    assert forward.get("attempted") is True
    assert forward.get("status") == "ok"
