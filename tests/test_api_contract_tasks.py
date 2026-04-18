import pytest
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

# --- Contract Models (Source of Truth for the API) ---
# These models define what the client expects from the server.
# If these change, it's a contract break.

class TaskSummaryContract(BaseModel):
    id: str
    title: str
    status: str
    agent_id: Optional[str] = None
    created_at: float
    updated_at: float
    goal_id: Optional[str] = None
    parent_id: Optional[str] = None

class TaskListResponse(BaseModel):
    status: str = "ok"
    data: List[TaskSummaryContract]

class GovernanceSummaryContract(BaseModel):
    goal_id: str
    status: str
    progress: float
    task_count: int
    completed_count: int
    risk_level: str
    details: Optional[Dict[str, Any]] = None

class GovernanceResponse(BaseModel):
    status: str = "ok"
    data: GovernanceSummaryContract

# --- Contract Tests ---

def test_task_summary_contract_compliance():
    # Example data that would come from the API
    raw_data = {
        "id": "T-123",
        "title": "Fix the bug",
        "status": "open",
        "agent_id": "worker-1",
        "created_at": 1620000000.0,
        "updated_at": 1620000005.0,
        "goal_id": "G-1",
        "parent_id": None
    }
    # Validate against contract
    contract = TaskSummaryContract(**raw_data)
    assert contract.id == "T-123"
    assert contract.status == "open"

def test_governance_summary_contract_compliance():
    raw_data = {
        "goal_id": "G-1",
        "status": "in_progress",
        "progress": 0.5,
        "task_count": 10,
        "completed_count": 5,
        "risk_level": "low",
        "details": {"some": "extra_info"}
    }
    contract = GovernanceSummaryContract(**raw_data)
    assert contract.progress == 0.5
    assert contract.risk_level == "low"

@pytest.mark.integration
def test_task_list_api_contract(client):
    """
    This test would ideally run against a real (or mocked) API instance
    to verify that the actual response matches the contract.
    """
    # Assuming 'client' is a flask test client
    # response = client.get("/api/tasks")
    # assert response.status_code == 200
    # data = response.get_json()
    # TaskListResponse(**data) # Should not raise ValidationError
    pass
