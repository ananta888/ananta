from __future__ import annotations

from types import SimpleNamespace


class _TaskScopedExecutionStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def propose_task_step(self, task_id: str, _data, **_kwargs):
        self.calls.append(("propose", task_id))
        return SimpleNamespace(
            status="success",
            data={"accepted": True, "phase": "propose"},
            message=None,
            code=200,
        )

    def execute_task_step(self, task_id: str, _data, **_kwargs):
        self.calls.append(("execute", task_id))
        return SimpleNamespace(
            status="success",
            data={"accepted": True, "phase": "execute"},
            message=None,
            code=200,
        )


def test_knowledge_index_dispatch_requires_hub_service_auth_before_task_service(
    app,
    client,
    user_auth_header,
    monkeypatch,
) -> None:
    from agent.routes.tasks import execution

    scoped = _TaskScopedExecutionStub()
    monkeypatch.setattr(
        execution,
        "_services",
        lambda: SimpleNamespace(task_scoped_execution_service=scoped),
    )
    service_headers = {
        "Authorization": f"Bearer {app.config['AGENT_TOKEN']}"
    }

    for phase in ("propose", "execute"):
        task_id = f"knowledge-index-auth-{phase}"
        payload = {
            "knowledge_index_dispatch": {
                "schema": "ananta.knowledge_index_dispatch.v1",
                "job_id": task_id,
                "task_kind": "codecompass_index_build",
                "phase": phase,
            }
        }
        before = list(scoped.calls)
        denied = client.post(
            f"/tasks/{task_id}/step/{phase}",
            json=payload,
            headers=user_auth_header,
        )

        assert denied.status_code == 403
        assert denied.get_json()["data"]["reason_code"] == (
            "knowledge_index_dispatch_service_auth_required"
        )
        assert scoped.calls == before

        accepted = client.post(
            f"/tasks/{task_id}/step/{phase}",
            json=payload,
            headers=service_headers,
        )

        assert accepted.status_code == 200
        assert accepted.get_json()["data"] == {
            "accepted": True,
            "phase": phase,
        }
        assert scoped.calls[-1] == (phase, task_id)
