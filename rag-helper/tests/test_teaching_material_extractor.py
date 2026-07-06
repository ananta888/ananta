from pathlib import Path

from rag_helper.application.output_formats import build_graph_edges
from rag_helper.extractors.n8n_workflow_extractor import N8nWorkflowExtractor
from rag_helper.extractors.teaching_material_extractor import (
    TeachingMaterialExtractor,
    link_material_workflow_relations,
)

MATERIALS = Path("rag-helper/tests/fixtures/classroom/materials")


def test_material_fixture_kinds_and_heuristic_fallback():
    extractor = TeachingMaterialExtractor()
    records = []
    for path in MATERIALS.glob("*.md"):
        index, _, _, _ = extractor.parse(str(path), path.read_text())
        records.extend(index)
    kinds = [record["kind"] for record in records]
    assert kinds.count("teaching_module") == 2
    assert kinds.count("teaching_task") == 3
    assert kinds.count("teaching_hint") == 1
    assert kinds.count("known_solution") == 1
    assert any(record["confidence"] == "low" for record in records)
    assert all("source" not in record for record in records)


def test_cross_file_relations_resolve_without_dangling_edges():
    teaching = TeachingMaterialExtractor()
    n8n = N8nWorkflowExtractor()
    index = []
    details = []
    relations = []
    for path in MATERIALS.glob("*.md"):
        idx, det, rel, _ = teaching.parse(str(path), path.read_text())
        index += idx
        details += det
        relations += rel
    workflow_path = Path("rag-helper/tests/fixtures/n8n/webhook_if_merge_wait_workflow.json")
    idx, det, rel, _ = n8n.parse(str(workflow_path), workflow_path.read_text())
    index += idx
    details += det
    relations += rel
    assert link_material_workflow_relations(index, relations) >= 3
    node_ids = {record["id"] for record in [*index, *details]}
    edges = build_graph_edges(index, details, relations)
    assert any(edge["type"] == "task_uses_n8n_workflow" for edge in edges)
    assert all(edge["source"] in node_ids and edge["target"] in node_ids for edge in edges)


def test_compact_embedding_is_shorter_and_contains_search_terms():
    path = MATERIALS / "task-webhook.md"
    verbose = TeachingMaterialExtractor("verbose").parse(str(path), path.read_text())[0][0]
    compact = TeachingMaterialExtractor("compact").parse(str(path), path.read_text())[0][0]
    assert len(compact["embedding_text"]) < len(verbose["embedding_text"])
    assert all(term in verbose["embedding_text"].lower() for term in ("webhook", "trigger", "nicht an"))
    assert (
        "n8n_workflow_fixture"
        not in Path("rag-helper/rag_helper/extractors/teaching_material_extractor.py").read_text()
    )
