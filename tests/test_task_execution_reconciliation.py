from agent.db_models import AgentInfoDB, TaskDB, WorkerJobDB
from agent.repository import agent_repo, task_repo, worker_job_repo
from agent.services.task_execution_tracking_service import get_task_execution_tracking_service


def test_reconcile_marks_stuck_worker_execution_blocked(app):
    with app.app_context():
        task_repo.save(
            TaskDB(
                id="reconcile-stuck-1",
                title="Investigate worker timeout",
                status="assigned",
                current_worker_job_id="job-stuck-1",
                assigned_agent_url="http://offline-worker:5000",
                updated_at=100.0,
            )
        )
        worker_job_repo.save(
            WorkerJobDB(
                id="job-stuck-1",
                parent_task_id="reconcile-stuck-1",
                subtask_id="sub-stuck-1",
                worker_url="http://offline-worker:5000",
                status="delegated",
                updated_at=100.0,
            )
        )
        agent_repo.save(
            AgentInfoDB(
                url="http://offline-worker:5000",
                name="offline-worker",
                role="worker",
                status="offline",
            )
        )

        snapshot = get_task_execution_tracking_service().reconcile_worker_executions(now=1000.0)
        task = task_repo.get_by_id("reconcile-stuck-1")

        assert snapshot["decisions"]
        assert task.status == "blocked"
        assert task.verification_status["execution_reconciliation"]["issue_code"] == "stuck_execution"
        assert task.verification_status["execution_reconciliation"]["details"]["worker_offline"] is True
        assert any((entry.get("event_type") == "task_reconciled") for entry in (task.history or []))


def test_reconcile_aligns_nonterminal_worker_job_for_completed_task(app):
    with app.app_context():
        task_repo.save(
            TaskDB(
                id="reconcile-terminal-1",
                title="Finalize delegated result",
                status="completed",
                current_worker_job_id="job-terminal-1",
                updated_at=200.0,
            )
        )
        worker_job_repo.save(
            WorkerJobDB(
                id="job-terminal-1",
                parent_task_id="reconcile-terminal-1",
                subtask_id="sub-terminal-1",
                worker_url="http://worker:5000",
                status="delegated",
                updated_at=150.0,
            )
        )

        snapshot = get_task_execution_tracking_service().reconcile_worker_executions(now=400.0)
        job = worker_job_repo.get_by_id("job-terminal-1")

        assert snapshot["decisions"]
        assert job.status == "completed"
        assert job.job_metadata["execution_reconciliation"]["issue_code"] == "terminal_job_mismatch"
