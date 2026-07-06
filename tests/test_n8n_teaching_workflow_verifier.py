import json
from pathlib import Path

from agent.services.classroom.n8n_teaching_workflow_verifier_service import verify_workflow_part


def test_invalid_dangling_secret_and_unknown_type():
    assert verify_workflow_part("{")["reasons"] == ["invalid_json"]
    dangling = {"nodes": [{"name": "A", "type": "known"}], "connections": {"A": {"main": [[{"node": "B"}]]}}}
    assert verify_workflow_part(dangling)["reasons"] == ["dangling_connection"]
    secret = {
        "nodes": [{"name": "A", "type": "known", "parameters": {"token": "sk-123456789012345"}}],
        "connections": {},
    }
    result = verify_workflow_part(secret)
    assert result["status"] == "failed"
    assert "sk-123456789012345" not in json.dumps(result["verified_part"])
    unknown = verify_workflow_part(
        {"nodes": [{"name": "A", "type": "invented"}], "connections": {}}, known_node_types={"known"}
    )
    assert unknown["status"] == "warning"


def test_positive_fixtures_pass():
    for name in (
        "simple_manual_http_workflow.json",
        "webhook_if_merge_wait_workflow.json",
        "llm_email_triage_workflow.json",
    ):
        payload = json.loads((Path("rag-helper/tests/fixtures/n8n") / name).read_text())
        assert verify_workflow_part(payload)["status"] == "passed"
