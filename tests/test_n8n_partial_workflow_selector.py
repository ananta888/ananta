import json

from agent.services.classroom.n8n_partial_workflow_selector import N8nPartialWorkflowSelector


def test_fixture_precedes_generation_and_fragment_has_no_dangling_connections():
    calls = []
    result = N8nPartialWorkflowSelector(generator=lambda _: calls.append(True)).select(
        question_text="Mein Webhook triggert nicht"
    )
    assert calls == []
    assert result["source_ref"]["record_id"].startswith("n8n_workflow:")
    assert result["import_hint"]
    part = result["part"]
    if result["form"] == "subflow":
        names = {node["name"] for node in part["nodes"]}
        targets = {
            target["node"]
            for groups in part["connections"].values()
            for outputs in groups.values()
            for bucket in outputs
            for target in bucket
        }
        assert targets <= names
    assert "password" not in json.dumps(result).lower()
