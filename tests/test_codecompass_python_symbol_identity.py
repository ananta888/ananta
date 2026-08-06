from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.codecompass.semantic_translation.python_adapter import (
    PythonSemanticAdapter,
)
from agent.codecompass.semantic_translation.python_symbol_identity import (
    DeterministicPythonSymbolIdentityFactory,
)
from worker.retrieval.repository_codecompass_bridge import (
    RepositoryCodeCompassBridge,
)


def _node_by_symbol(result: dict, symbol: str) -> dict:
    return next(node for node in result["nodes"] if node["symbol"] == symbol)


def _jsonl_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _partitioned_rows(output_dir: Path, result: dict, output_kind: str) -> list[dict]:
    relative_paths = result.get("partitioned_outputs", {}).get(output_kind) or [f"{output_kind}.jsonl"]
    return [row for relative_path in relative_paths for row in _jsonl_rows(output_dir / relative_path)]


def test_python_symbol_ids_are_file_scoped_and_path_canonical() -> None:
    identities = DeterministicPythonSymbolIdentityFactory()

    first = identities.symbol_id(
        path="./package\\first.py",
        symbol_kind="function",
        qualified_symbol="main",
    )
    canonical_first = identities.symbol_id(
        path="package/first.py",
        symbol_kind="function",
        qualified_symbol="main",
    )
    second = identities.symbol_id(
        path="package/second.py",
        symbol_kind="function",
        qualified_symbol="main",
    )

    assert first == canonical_first
    assert first != second
    assert first.startswith("semantic:python:symbol:v1:function:main:")
    assert len(first.rsplit(":", 1)[-1]) == 64


def test_python_symbol_ids_preserve_unicode_git_path_identity() -> None:
    identities = DeterministicPythonSymbolIdentityFactory()

    nfc_path = "package/caf\u00e9.py"
    nfd_path = "package/cafe\u0301.py"

    assert identities.symbol_id(
        path=nfc_path,
        symbol_kind="function",
        qualified_symbol="main",
    ) != identities.symbol_id(
        path=nfd_path,
        symbol_kind="function",
        qualified_symbol="main",
    )


def test_python_symbol_ids_preserve_git_path_whitespace() -> None:
    identities = DeterministicPythonSymbolIdentityFactory()

    regular = identities.symbol_id(
        path="package/service.py",
        symbol_kind="function",
        qualified_symbol="main",
    )
    leading_space = identities.symbol_id(
        path="package/ service.py",
        symbol_kind="function",
        qualified_symbol="main",
    )

    assert regular != leading_space


def test_python_symbol_ids_bound_long_unicode_readable_prefixes() -> None:
    identities = DeterministicPythonSymbolIdentityFactory()
    common_prefix = "\u540d" * 100

    first = identities.symbol_id(
        path="package/service.py",
        symbol_kind="function",
        qualified_symbol=f"{common_prefix}\u7532",
    )
    second = identities.symbol_id(
        path="package/service.py",
        symbol_kind="function",
        qualified_symbol=f"{common_prefix}\u4e59",
    )
    first_readable = first.rsplit(":", 2)[-2]
    second_readable = second.rsplit(":", 2)[-2]

    assert len(first) <= 200
    assert len(second) <= 200
    assert first_readable == second_readable
    assert first_readable.endswith("~")
    assert len(first_readable.removesuffix("~")) <= 96
    assert first != second


def test_python_symbol_identity_rejects_non_repository_paths() -> None:
    identities = DeterministicPythonSymbolIdentityFactory()

    with pytest.raises(ValueError, match="python_symbol_identity_path_invalid"):
        identities.symbol_id(
            path="../outside.py",
            symbol_kind="function",
            qualified_symbol="main",
        )


def test_python_symbol_ids_ignore_revision_content_lines_and_parser_version() -> None:
    original = PythonSemanticAdapter().emit_graph_records(
        "package/service.py",
        "def main() -> int:\n    return 1\n",
    )
    changed_adapter = PythonSemanticAdapter()
    changed_adapter.parser_strategy = "ast-python-v-next"
    changed = changed_adapter.emit_graph_records(
        "package/service.py",
        "# a later revision moved the declaration\n\ndef main() -> int:\n    return 2\n",
    )

    original_node = _node_by_symbol(original, "main")
    changed_node = _node_by_symbol(changed, "main")

    assert original_node["id"] == changed_node["id"]
    assert original_node["provenance"]["line_start"] != changed_node["provenance"]["line_start"]
    assert original_node["provenance"]["parser"] == "ast-python-v1"
    assert changed_node["provenance"]["parser"] == "ast-python-v-next"
    assert original_node["schema"] == changed_node["schema"] == ("codecompass_semantic_translation_graph.v1")


def test_python_internal_edges_use_the_canonical_member_ids() -> None:
    source = """
from dataclasses import dataclass

@dataclass
class User:
    name: str

    def display_name(self) -> str:
        return self.name
"""
    identities = DeterministicPythonSymbolIdentityFactory()
    result = PythonSemanticAdapter(symbol_identity=identities).emit_graph_records(
        "models/user.py",
        source,
    )
    node_ids = {node["id"] for node in result["nodes"]}
    expected_type_id = identities.symbol_id(
        path="models/user.py",
        symbol_kind="type",
        qualified_symbol="User",
    )
    expected_member_ids = {
        identities.symbol_id(
            path="models/user.py",
            symbol_kind="field",
            qualified_symbol="User.name",
        ),
        identities.symbol_id(
            path="models/user.py",
            symbol_kind="method",
            qualified_symbol="User.display_name",
        ),
    }

    assert expected_type_id in node_ids
    assert expected_member_ids <= node_ids
    assert {(edge["source"], edge["target"]) for edge in result["edges"] if edge["edge_type"] == "declares"} == {
        (expected_type_id, member_id) for member_id in expected_member_ids
    }
    assert all(edge["source"] in node_ids and edge["target"] in node_ids for edge in result["edges"])


def test_repository_declares_bind_to_each_files_canonical_python_nodes(
    tmp_path: Path,
) -> None:
    source = """
class Service:
    def run(self) -> None:
        return None

def main() -> None:
    return None
"""
    result = RepositoryCodeCompassBridge(
        PythonSemanticAdapter(),
        max_semantic_records_per_partition=100,
    ).build_outputs(
        source_id="repository-under-test",
        records=[
            {
                "content": source,
                "metadata": {"relative_path": "package/first.py"},
            },
            {
                "content": source,
                "metadata": {"relative_path": "package/second.py"},
            },
        ],
        output_dir=tmp_path,
    )
    semantic_nodes = _partitioned_rows(tmp_path, result, "semantic_nodes")
    semantic_edges = _partitioned_rows(tmp_path, result, "semantic_edges")
    graph_nodes = _jsonl_rows(tmp_path / "graph_nodes.jsonl")
    graph_edges = _jsonl_rows(tmp_path / "graph_edges.jsonl")
    semantic_by_id = {node["id"]: node for node in semantic_nodes}
    file_nodes = {node["file"]: node["id"] for node in graph_nodes if node["kind"] == "source_file"}

    assert result["semantic_file_count"] == 2
    assert len(semantic_nodes) == 6
    assert len(semantic_by_id) == len(semantic_nodes)
    for path, source_file_id in file_nodes.items():
        expected_targets = {node["id"] for node in semantic_nodes if node["provenance"]["file"] == path}
        actual_targets = {
            edge["target"] for edge in graph_edges if edge["source"] == source_file_id and edge["type"] == "declares"
        }
        assert len(expected_targets) == 3
        assert actual_targets == expected_targets

    assert all(
        semantic_by_id[edge["source"]]["provenance"]["file"] == semantic_by_id[edge["target"]]["provenance"]["file"]
        for edge in semantic_edges
    )


def test_repository_bridge_keeps_unicode_equivalent_git_paths_distinct(
    tmp_path: Path,
) -> None:
    source = "def main() -> None:\n    return None\n"
    nfc_path = "package/caf\u00e9.py"
    nfd_path = "package/cafe\u0301.py"

    result = RepositoryCodeCompassBridge(
        PythonSemanticAdapter(),
        max_semantic_records_per_partition=100,
    ).build_outputs(
        source_id="repository-under-test",
        records=[
            {
                "content": source,
                "metadata": {"relative_path": nfc_path},
            },
            {
                "content": source,
                "metadata": {"relative_path": nfd_path},
            },
        ],
        output_dir=tmp_path,
    )
    semantic_nodes = _partitioned_rows(tmp_path, result, "semantic_nodes")
    graph_nodes = _jsonl_rows(tmp_path / "graph_nodes.jsonl")
    graph_edges = _jsonl_rows(tmp_path / "graph_edges.jsonl")
    file_node_ids = {node["file"]: node["id"] for node in graph_nodes if node["kind"] == "source_file"}

    assert result["semantic_file_count"] == 2
    assert {node["provenance"]["file"] for node in semantic_nodes} == {
        nfc_path,
        nfd_path,
    }
    assert len({node["id"] for node in semantic_nodes}) == 2
    declared_by_path = {
        path: {edge["target"] for edge in graph_edges if edge["source"] == file_node_id and edge["type"] == "declares"}
        for path, file_node_id in file_node_ids.items()
    }
    assert set(declared_by_path) == {nfc_path, nfd_path}
    assert all(len(targets) == 1 for targets in declared_by_path.values())
    assert declared_by_path[nfc_path].isdisjoint(declared_by_path[nfd_path])


def test_repository_bridge_keeps_git_path_whitespace_distinct(
    tmp_path: Path,
) -> None:
    source = "def main() -> None:\n    return None\n"
    paths = ("package/service.py", "package/ service.py")

    result = RepositoryCodeCompassBridge(
        PythonSemanticAdapter(),
        max_semantic_records_per_partition=100,
    ).build_outputs(
        source_id="repository-under-test",
        records=[
            {
                "content": source,
                "metadata": {"relative_path": path},
            }
            for path in paths
        ],
        output_dir=tmp_path,
    )
    semantic_nodes = _partitioned_rows(
        tmp_path,
        result,
        "semantic_nodes",
    )
    graph_nodes = _jsonl_rows(tmp_path / "graph_nodes.jsonl")

    assert result["semantic_file_count"] == 2
    assert {node["provenance"]["file"] for node in semantic_nodes} == set(paths)
    assert len({node["id"] for node in semantic_nodes}) == 2
    assert {node["file"] for node in graph_nodes if node["kind"] == "source_file"} == set(paths)
