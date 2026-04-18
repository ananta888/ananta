import pytest
import time
import jwt
from agent.config import settings
from agent.db_models import GoalDB, TaskDB, AgentInfoDB
from agent.repository import goal_repo, task_repo, policy_decision_repo, verification_record_repo, agent_repo
from agent.common.governance_codes import GovernanceReasonCode

class TestGRMExtended:
    @pytest.fixture
    def user_auth_header(self):
        payload = {
            "sub": "testuser",
            "username": "testuser",
            "role": "user",
            "exp": time.time() + 3600
        }
        token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

    def test_goal_governance_visibility_roles(self, client, admin_auth_header, user_auth_header):
        # 1. Setup Goal and Task
        goal = goal_repo.save(GoalDB(goal="Test Governance Visibility", trace_id="trace-123", status="planned"))
        task_repo.save(TaskDB(
            id="task-grm-1",
            title="Task 1",
            status="blocked",
            goal_id=goal.id,
            goal_trace_id=goal.trace_id,
            status_reason_code=GovernanceReasonCode.POLICY_VIOLATION.value,
            status_reason_details={"reason": "test blocking"}
        ))

        # 2. Check as Admin (should see details)
        res_admin = client.get(f"/goals/{goal.id}/governance", headers=admin_auth_header)
        assert res_admin.status_code == 200
        data_admin = res_admin.get_json()["data"]
        assert data_admin["goal_id"] == goal.id
        assert len(data_admin["routing"]["tasks"]) > 0
        assert data_admin["routing"]["tasks"][0]["status_reason_code"] == GovernanceReasonCode.POLICY_VIOLATION.value

        # 3. Check as User (should see only summary)
        res_user = client.get(f"/goals/{goal.id}/governance", headers=user_auth_header)
        assert res_user.status_code == 200
        data_user = res_user.get_json()["data"]
        assert data_user["goal_id"] == goal.id
        assert "tasks" not in data_user["routing"] or len(data_user["routing"].get("tasks", [])) == 0
        assert "latest" not in data_user["policy"] or len(data_user.get("policy", {}).get("latest", [])) == 0

    def test_structured_escalation_code(self, client, admin_auth_header):
        # 1. Setup Task
        tid = "task-grm-escalate"
        task_repo.save(TaskDB(id=tid, title="Escalate Task", status="assigned", task_kind="coding"))

        # 2. Complete with failure multiple times to trigger escalation
        for _ in range(3):
            client.post(
                "/tasks/orchestration/complete",
                headers=admin_auth_header,
                json={
                    "task_id": tid,
                    "actor": "http://worker:5000",
                    "gate_results": {"passed": False},
                    "output": "failure",
                    "trace_id": "trace-esc"
                },
            )

        # 3. Verify escalation code
        records = verification_record_repo.get_by_task_id(tid)
        assert records
        last_record = records[0]
        assert last_record.status == "escalated"
        assert last_record.escalation_code == GovernanceReasonCode.RETRY_EXHAUSTED.value

        # 4. Verify in Governance Read Model
        goal_id = task_repo.get_by_id(tid).goal_id
        if goal_id:
            res = client.get(f"/goals/{goal_id}/governance", headers=admin_auth_header)
            data = res.get_json()["data"]
            assert data["verification"]["escalated"] >= 1
