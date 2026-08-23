from agent.services.snake_codecompass_architecture_context_service import (
    SnakeCodeCompassArchitectureContextService,
)


def test_architecture_prefill_projects_hierarchical_slice() -> None:
    def loader(**_kwargs):
        return {
            "status": "ok",
            "data": {"architecture": {
                "schema": "codecompass.hierarchical-architecture-context.v1",
                "levels": ["system", "subsystem", "component", "file", "symbol"],
                "nodes": [
                    {
                        "id": "system",
                        "level": "system",
                        "title": "Ananta",
                        "path": ".",
                        "short_summary": "Repository root.",
                        "handle": "hac:revision:system",
                    },
                    {
                        "id": "component:retrieval",
                        "level": "component",
                        "title": "Retrieval",
                        "path": "agent/services",
                        "short_summary": "Builds grounded context.",
                        "handle": "hac:revision:component:retrieval",
                    },
                ],
                "edges": [{
                    "source": "system",
                    "target": "component:retrieval",
                    "type": "contains",
                }],
                "truncated": False,
                "warnings": [],
            }},
        }

    text, trace = SnakeCodeCompassArchitectureContextService().build(
        "Erkläre CodeCompass", loader=loader
    )

    assert "Hierarchischer Architektur-Kontext" in text
    assert "[component] Retrieval" in text
    assert "Ananta --contains--> Retrieval" in text
    assert trace["status"] == "ok"
    assert trace["node_count"] == 2
    assert trace["edge_count"] == 1
    assert trace["levels"] == ["system", "subsystem", "component", "file", "symbol"]


def test_architecture_prefill_degrades_without_graph() -> None:
    text, trace = SnakeCodeCompassArchitectureContextService().build(
        "question",
        loader=lambda **_kwargs: {
            "status": "degraded",
            "error": "architecture_graph_unavailable",
            "warnings": ["architecture_graph_unavailable"],
            "data": {
                "resolution_diagnostics": [
                    {
                        "knowledge_index_id": "idx-1",
                        "reason": "graph_file_missing",
                    }
                ]
            },
        },
    )

    assert text == ""
    assert trace["status"] == "degraded"
    assert trace["reason"] == "architecture_graph_unavailable"
    assert trace["resolution_diagnostics"] == [
        {"knowledge_index_id": "idx-1", "reason": "graph_file_missing"}
    ]
