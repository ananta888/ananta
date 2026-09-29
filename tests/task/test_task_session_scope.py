from agent.services.task_scoped_execution_service import get_task_scoped_execution_service


def test_task_session_scope_prefers_explicit_workspace_scope_key():
    service = get_task_scoped_execution_service()
    scope_kind, scope_key, role_name = service._resolve_task_session_scope(
        tid="T-1",
        task={
            "worker_execution_context": {
                "workspace": {
                    "session_scope_kind": "workspace",
                    "session_scope_key": "workspace:http-worker-5001:goal-1",
                }
            }
        },
        policy={"reuse_scope": "task"},
    )

    assert scope_kind == "workspace"
    assert scope_key == "workspace:http-worker-5001:goal-1"
    assert role_name is None

