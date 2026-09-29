from __future__ import annotations

from agent.services.task_scoped_execution_service import TaskScopedExecutionService


def test_render_citation_contract_prompt_contains_required_rules() -> None:
    svc = TaskScopedExecutionService()
    contract = svc._render_citation_contract_prompt(
        {
            "catalog_id": "catalog-1",
            "catalog_hash": "abc123",
            "sources": [
                {
                    "source_id": "SRC_0001",
                    "source_type": "repo_file",
                    "path": "src/a.py",
                    "record_id": "r1",
                    "allowed_for_llm_scope": True,
                }
            ],
        }
    )
    assert "grounded_answer.v1" in contract
    assert "Do not invent source IDs" in contract
    assert "Tool execution claims must cite RUN_*" in contract
    assert "SRC_0001" in contract


def test_extract_grounded_answer_payload_accepts_contract_shape() -> None:
    svc = TaskScopedExecutionService()
    payload = svc._extract_grounded_answer_payload(
        '{"schema":"grounded_answer.v1","answer":"ok","claims":[],"unsupported_notes":[]}'
    )
    assert isinstance(payload, dict)
    assert payload["schema"] == "grounded_answer.v1"
