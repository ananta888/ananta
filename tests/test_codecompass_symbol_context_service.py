from __future__ import annotations

import json


def _append_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_symbol_context_reads_bounded_method_snippet_and_relation_neighbor(tmp_path):
    source = tmp_path / "agent" / "sample.py"
    source.parent.mkdir()
    source.write_text(
        "\n".join([
            "class SearchService:",
            "    def plan_context(self):",
            "        return self.resolve_context()",
            "",
            "    def resolve_context(self):",
            "        return 'context'",
            "",
            "def unrelated():",
            "    return None",
        ]),
        encoding="utf-8",
    )
    details = tmp_path / "rag-helper" / "out" / "details_by_kind" / "python_method.jsonl"
    _append_jsonl(details, [
        {
            "kind": "python_method",
            "file": "agent/sample.py",
            "id": "method:plan",
            "parent_id": "class:search",
            "name": "plan_context",
            "line": 2,
            "class_name": "SearchService",
        },
        {
            "kind": "python_method",
            "file": "agent/sample.py",
            "id": "method:resolve",
            "parent_id": "class:search",
            "name": "resolve_context",
            "line": 5,
            "class_name": "SearchService",
        },
    ])
    relations = tmp_path / "rag-helper" / "out" / "relations_by_type" / "calls_probable_target.jsonl"
    _append_jsonl(relations, [
        {
            "source_id": "method:plan",
            "target_id": "method:resolve",
            "relation": "calls_probable_target",
        }
    ])

    from agent.services.codecompass_symbol_context_service import build_codecompass_symbol_context

    snippets = build_codecompass_symbol_context(
        repo_root=tmp_path,
        query="plan context",
        ranked_sources=[{"source": "agent/sample.py", "score": 50.0}],
        max_snippets=2,
        max_lines_per_snippet=4,
    )

    assert [snippet.symbol for snippet in snippets] == ["plan_context", "resolve_context"]
    assert snippets[0].line_start == 2
    assert "2:     def plan_context" in snippets[0].content
    assert snippets[1].relation == "calls_probable_target"
