"""
Test-Fixtures für CodeCompass Hierarchical Architecture Context Schema.
Validiert HAC-001 Acceptance Criteria.
"""
import json
import pytest
from pathlib import Path
from jsonschema import validate, ValidationError

SCHEMA_PATH = Path(__file__).parent.parent / "codecompass" / "codecompass.hierarchical-architecture-context.v1.json"


def load_schema():
    """Lädt das Hierarchical Architecture Context Schema."""
    with open(SCHEMA_PATH, 'r') as f:
        return json.load(f)


def create_valid_full_context():
    """Erstellt einen vollständigen validen 5-Level-Kontext."""
    return {
        "version": "v1",
        "query": {
            "text": "Was ist CodeCompass im Ananta-System?",
            "timestamp": "2026-08-16T10:30:00Z",
            "intent": "understand"
        },
        "root_scope": {
            "workspace_id": "ananta-main",
            "revision": "abc123def456",
            "tenant_id": "ananta-dev",
            "source_catalog_scope": ["local-workspace", "git-origin"]
        },
        "levels": ["system", "subsystem", "component", "file", "symbol"],
        "nodes": [
            {
                "id": "sys-ananta",
                "level": "system",
                "title": "Ananta Platform",
                "short_summary": "Intelligente Entwicklungsplattform mit Agenten-Orchestrierung und Code-Verständnis.",
                "responsibilities": ["Agenten-Runtime bereitstellen", "Code-Verständnis ermöglichen", "Workflow-Automatisierung"],
                "source_refs": [{"path": "README.md", "line_start": 1, "line_end": 10}],
                "confidence": 1.0,
                "trust": "manual",
                "expandable": True,
                "expanded": False,
                "child_ids": ["ss-codecompass", "ss-worker"],
                "tags": ["platform", "core"]
            },
            {
                "id": "ss-codecompass",
                "level": "subsystem",
                "title": "CodeCompass",
                "short_summary": "Kontext-Engine für semantisches Code-Verständnis und Retrieval.",
                "responsibilities": ["Symbolgraph bereitstellen", "Semantische Suche", "Kontext-Budgetierung"],
                "source_refs": [{"path": "docs/codecompass.md", "line_start": 1, "line_end": 50}],
                "confidence": 0.95,
                "trust": "extracted",
                "expandable": True,
                "expanded": False,
                "parent_id": "sys-ananta",
                "child_ids": ["comp-context-planner"],
                "tags": ["retrieval", "context"]
            },
            {
                "id": "comp-context-planner",
                "level": "component",
                "title": "Context Planner Service",
                "short_summary": "Wählt budgetierten Kontext basierend auf Query und Profil aus.",
                "responsibilities": ["Kontext-Auswahl", "Budget-Überwachung", "Ranking"],
                "source_refs": [
                    {"path": "agent/services/codecompass_context_planner_service.py", "line_start": 1, "line_end": 100}
                ],
                "confidence": 0.9,
                "trust": "extracted",
                "expandable": True,
                "expanded": False,
                "parent_id": "ss-codecompass",
                "child_ids": ["file-planner-py"],
                "tags": ["planning", "budget"]
            },
            {
                "id": "file-planner-py",
                "level": "file",
                "title": "codecompass_context_planner_service.py",
                "short_summary": "Hauptimplementierung des Context Planners.",
                "responsibilities": ["Planung implementieren", "Budgets durchsetzen"],
                "source_refs": [
                    {"path": "agent/services/codecompass_context_planner_service.py", "line_start": 1, "line_end": 500}
                ],
                "confidence": 1.0,
                "trust": "deterministic",
                "expandable": True,
                "expanded": False,
                "parent_id": "comp-context-planner",
                "child_ids": ["sym-plan-context"],
                "tags": ["python", "service"]
            },
            {
                "id": "sym-plan-context",
                "level": "symbol",
                "title": "plan_context",
                "short_summary": "Hauptfunktion zur Kontextplanung.",
                "responsibilities": ["Query parsen", "Kandidaten sammeln", "Ranking anwenden"],
                "source_refs": [
                    {"path": "agent/services/codecompass_context_planner_service.py", "line_start": 50, "line_end": 150, "symbol_name": "plan_context"}
                ],
                "confidence": 1.0,
                "trust": "deterministic",
                "expandable": False,
                "expanded": False,
                "parent_id": "file-planner-py",
                "tags": ["function", "entry-point"]
            }
        ],
        "edges": [
            {
                "source_id": "sys-ananta",
                "target_id": "ss-codecompass",
                "relationship": "contains",
                "description": "Ananta enthält CodeCompass als Subsystem.",
                "confidence": 1.0,
                "weight": 1.0
            },
            {
                "source_id": "ss-codecompass",
                "target_id": "comp-context-planner",
                "relationship": "contains",
                "description": "CodeCompass enthält Context Planner Komponente.",
                "confidence": 0.95,
                "weight": 1.0
            },
            {
                "source_id": "comp-context-planner",
                "target_id": "file-planner-py",
                "relationship": "implements",
                "description": "Komponente wird durch diese Datei implementiert.",
                "confidence": 1.0,
                "weight": 1.0
            },
            {
                "source_id": "file-planner-py",
                "target_id": "sym-plan-context",
                "relationship": "contains",
                "description": "Datei enthält plan_context Funktion.",
                "confidence": 1.0,
                "weight": 1.0
            },
            {
                "source_id": "comp-context-planner",
                "target_id": "ss-codecompass",
                "relationship": "provides_context_to",
                "description": "Context Planner liefert Kontext für CodeCompass.",
                "confidence": 0.9,
                "weight": 0.8
            }
        ],
        "summaries": {
            "system": "Ananta ist eine intelligente Entwicklungsplattform.",
            "subsystem": "CodeCompass bietet semantisches Code-Verständnis.",
            "component": "Context Planner wählt budgetierten Kontext aus.",
            "file": "Python-Service implementiert Planungslogik.",
            "symbol": "plan_context ist die Hauptfunktion."
        },
        "evidence": {
            "source_kind": "graph_projection",
            "provenance": {
                "source": "graph-projection",
                "provider_id": "codecompass.graph-store",
                "provider_revision": "abc123",
                "import_run_id": "run-2026-08-16-001"
            },
            "trust_level": "extracted",
            "verification_status": "verified"
        },
        "budgets": {
            "token_budget": 2000,
            "node_budget": 50,
            "edge_budget": 100,
            "depth_budget": 5,
            "used": {
                "tokens": 850,
                "nodes": 5,
                "edges": 5,
                "depth": 5
            },
            "truncated": False
        },
        "metadata": {
            "generated_at": "2026-08-16T10:30:01Z",
            "generator_version": "v1.0.0",
            "cache_key": "hac-ananta-codecompass-abc123",
            "ttl_seconds": 3600
        }
    }


def create_truncated_context():
    """Erstellt einen Kontext mit Budget-Trunkierung und continuation_handle."""
    context = create_valid_full_context()
    context["budgets"]["truncated"] = True
    context["budgets"]["continuation_handle"] = "cont-abc123-next-page"
    context["budgets"]["used"]["nodes"] = 50  # Max erreicht
    context["nodes"] = context["nodes"][:3]  # Nur 3 Nodes trotz Budget
    return context


def create_invalid_node_missing_level():
    """Erstellt einen ungültigen Kontext mit Node ohne Level."""
    context = create_valid_full_context()
    context["nodes"][0].pop("level")
    return context


def create_invalid_node_missing_id():
    """Erstellt einen ungültigen Kontext mit Node ohne ID."""
    context = create_valid_full_context()
    context["nodes"][0].pop("id")
    return context


def create_invalid_edge_missing_relationship():
    """Erstellt einen ungültigen Kontext mit Edge ohne Relationship."""
    context = create_valid_full_context()
    context["edges"][0].pop("relationship")
    return context


class TestHierarchicalArchitectureSchema:
    """Test-Suite für HAC-001 Schema-Validierung."""

    def load_schema(self):
        return load_schema()

    def test_valid_full_context(self):
        """Test: Vollständiger 5-Level-Kontext ist valide."""
        schema = self.load_schema()
        context = create_valid_full_context()
        validate(instance=context, schema=schema)

    def test_truncated_context_with_continuation(self):
        """Test: Abgeschnittener Slice mit expand handle ist valide."""
        schema = self.load_schema()
        context = create_truncated_context()
        validate(instance=context, schema=schema)

    def test_invalid_node_missing_level(self):
        """Test: Node ohne Level wird abgelehnt."""
        schema = self.load_schema()
        context = create_invalid_node_missing_level()
        with pytest.raises(ValidationError):
            validate(instance=context, schema=schema)

    def test_invalid_node_missing_id(self):
        """Test: Node ohne ID wird abgelehnt."""
        schema = self.load_schema()
        context = create_invalid_node_missing_id()
        with pytest.raises(ValidationError):
            validate(instance=context, schema=schema)

    def test_invalid_edge_missing_relationship(self):
        """Test: Edge ohne Relationship wird abgelehnt."""
        schema = self.load_schema()
        context = create_invalid_edge_missing_relationship()
        with pytest.raises(ValidationError):
            validate(instance=context, schema=schema)

    def test_all_hierarchy_levels_represented(self):
        """Test: Alle 5 Hierarchielevel sind im Beispiel vertreten."""
        schema = self.load_schema()
        context = create_valid_full_context()
        levels_present = set(node["level"] for node in context["nodes"])
        expected_levels = {"system", "subsystem", "component", "file", "symbol"}
        assert levels_present == expected_levels

    def test_budget_tracking_complete(self):
        """Test: Budget-Tracking ist vollständig."""
        schema = self.load_schema()
        context = create_valid_full_context()
        budgets = context["budgets"]
        assert all(k in budgets for k in ["token_budget", "node_budget", "edge_budget", "depth_budget"])
        assert all(k in budgets["used"] for k in ["tokens", "nodes", "edges", "depth"])

    def test_evidence_chain_present(self):
        """Test: Evidence-Kette von System bis Symbol ist vorhanden."""
        schema = self.load_schema()
        context = create_valid_full_context()
        for node in context["nodes"]:
            assert "source_refs" in node
            assert len(node["source_refs"]) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
