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


def test_symbol_context_falls_back_to_python_ast_with_line_numbers(tmp_path):
    source = tmp_path / "agent" / "services" / "codecompass_demo.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "class DemoService:\n"
        "    def retrieve_context(self, query):\n"
        "        return query\n",
        encoding="utf-8",
    )

    from agent.services.codecompass_symbol_context_service import (
        build_codecompass_symbol_context,
        format_symbol_context_section,
    )

    snippets = build_codecompass_symbol_context(
        repo_root=tmp_path,
        query="CodeCompass retrieve context",
        ranked_sources=[{
            "source": "agent/services/codecompass_demo.py",
            "score": 75.0,
        }],
        max_snippets=4,
    )
    formatted = format_symbol_context_section(snippets)

    assert {snippet.symbol for snippet in snippets} >= {"DemoService", "retrieve_context"}
    assert "agent/services/codecompass_demo.py:1-1" in formatted
    assert "agent/services/codecompass_demo.py:2-3" in formatted
    assert "2:     def retrieve_context" in formatted


def test_symbol_tool_returns_actual_symbol_evidence(tmp_path, monkeypatch):
    source = tmp_path / "agent" / "codecompass_entry.py"
    source.parent.mkdir()
    source.write_text(
        "def build_context(question):\n"
        "    return question\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "agent.services.tools.codecompass_architecture_tools._load_architecture_graph",
        lambda _arguments=None: ([{
            "id": "subsystem:agent",
            "path": "agent",
            "kind": "directory",
        }], []),
    )

    from agent.services.tools.codecompass_architecture_tools import (
        codecompass_symbol_context,
    )

    result = codecompass_symbol_context(
        workspace_dir=str(tmp_path),
        arguments={
            "query": "CodeCompass context",
            "ranked_sources": [{
                "source": "agent/codecompass_entry.py",
                "score": 90.0,
            }],
        },
        tool_call_id="call-symbol",
    )

    assert result["status"] == "ok"
    assert result["data"]["symbol_count"] == 1
    assert result["evidence"][0]["path"] == "agent/codecompass_entry.py"
    assert result["evidence"][0]["line_start"] == 1
