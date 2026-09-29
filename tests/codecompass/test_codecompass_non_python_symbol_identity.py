from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent.codecompass.semantic_translation.adapters import (
    JavaSemanticAdapter,
)
from agent.codecompass.semantic_translation.semantic_symbol_identity import (
    CANONICAL_SEMANTIC_ID_ATTRIBUTE,
    DeterministicSemanticSymbolIdentityFactory,
)
from agent.codecompass.semantic_translation.static_symbol_adapters import (
    default_static_symbol_adapters,
)
from agent.codecompass.semantic_translation.symbol_adapters import (
    RegexSymbolLanguageAdapter,
)
from agent.codecompass.semantic_translation.typescript_adapter import (
    TypeScriptSemanticAdapter,
)
from ananta_contracts.codecompass_semantic_partitions import (
    codecompass_semantic_domain_key,
)
from worker.retrieval.codecompass_domain_supplement import (
    DOMAIN_SUPPLEMENT_SOURCE_FILENAME,
)
from worker.retrieval.repository_codecompass_bridge import (
    RepositoryCodeCompassBridge,
)


def _module_node(
    emitted: dict[str, Any],
    *,
    canonical_id: str,
) -> dict[str, Any]:
    return next(
        node
        for node in emitted["nodes"]
        if node["attributes"].get(CANONICAL_SEMANTIC_ID_ATTRIBUTE)
        == canonical_id
    )


def test_typescript_import_and_export_modules_are_file_scoped_and_closed() -> None:
    source = "import {value} from 'shared';\nexport * from 'public-api';\n"
    first = TypeScriptSemanticAdapter().emit_graph_records("src/a.ts", source)
    second = TypeScriptSemanticAdapter().emit_graph_records("src/b.ts", source)

    for canonical_id, edge_type in (
        ("semantic:typescript:module:shared", "imports"),
        ("semantic:typescript:module:public-api", "exports"),
    ):
        first_module = _module_node(first, canonical_id=canonical_id)
        second_module = _module_node(second, canonical_id=canonical_id)
        assert first_module["id"] != second_module["id"]
        assert first_module["provenance"]["file"] == "src/a.ts"
        assert second_module["provenance"]["file"] == "src/b.ts"
        assert any(
            edge["edge_type"] == edge_type
            and edge["target"] == first_module["id"]
            for edge in first["edges"]
        )
        assert any(
            edge["edge_type"] == edge_type
            and edge["target"] == second_module["id"]
            for edge in second["edges"]
        )


def test_typescript_shared_import_survives_same_and_cross_domain_supplement(
    tmp_path: Path,
) -> None:
    paths = ("src/a.ts", "src/b.ts", "lib/c.ts")
    RepositoryCodeCompassBridge(TypeScriptSemanticAdapter()).build_outputs(
        source_id="repo",
        records=[
            {
                "content": "import {value} from 'shared';",
                "metadata": {"relative_path": path},
            }
            for path in paths
        ],
        output_dir=tmp_path,
    )

    with sqlite3.connect(
        tmp_path / DOMAIN_SUPPLEMENT_SOURCE_FILENAME
    ) as connection:
        node_rows = connection.execute(
            "SELECT domain_key, node_id, record_json FROM semantic_nodes "
            "ORDER BY domain_key, node_id"
        ).fetchall()
        edge_rows = connection.execute(
            "SELECT domain_key, record_json FROM semantic_edges "
            "ORDER BY domain_key, record_json"
        ).fetchall()

    module_rows = [
        (domain_key, node_id, json.loads(record_json))
        for domain_key, node_id, record_json in node_rows
        if json.loads(record_json)["attributes"].get(
            CANONICAL_SEMANTIC_ID_ATTRIBUTE
        )
        == "semantic:typescript:module:shared"
    ]
    assert len(module_rows) == 3
    assert len({node_id for _domain, node_id, _record in module_rows}) == 3
    assert [domain for domain, _node_id, _record in module_rows].count(
        codecompass_semantic_domain_key("src")
    ) == 2
    assert [domain for domain, _node_id, _record in module_rows].count(
        codecompass_semantic_domain_key("lib")
    ) == 1

    module_ids_by_domain: dict[str, set[str]] = {}
    for domain_key, node_id, _record in module_rows:
        module_ids_by_domain.setdefault(domain_key, set()).add(node_id)
    for domain_key, record_json in edge_rows:
        edge = json.loads(record_json)
        if edge.get("edge_type") == "imports":
            assert edge["target"] in module_ids_by_domain[domain_key]


def test_java_local_type_property_method_and_exception_chains_are_file_scoped(
) -> None:
    source = """
import java.util.List;
public class Same {
    private String value;
    public void run() throws IOException {}
}
"""
    first = JavaSemanticAdapter().emit_graph_records("src/a/Same.java", source)
    second = JavaSemanticAdapter().emit_graph_records("lib/Same.java", source)

    canonical_ids = {
        "semantic:java:module:java.util.List",
        "semantic:java:class:Same",
        "semantic:java:class:Same:property:value",
        "semantic:java:class:Same:method:run",
        "semantic:java:class:Same:method:run:throws:IOException",
    }
    first_by_canonical = {
        node["attributes"].get(CANONICAL_SEMANTIC_ID_ATTRIBUTE): node
        for node in first["nodes"]
    }
    second_by_canonical = {
        node["attributes"].get(CANONICAL_SEMANTIC_ID_ATTRIBUTE): node
        for node in second["nodes"]
    }
    assert canonical_ids <= set(first_by_canonical)
    assert canonical_ids <= set(second_by_canonical)
    assert all(
        first_by_canonical[canonical_id]["id"]
        != second_by_canonical[canonical_id]["id"]
        for canonical_id in canonical_ids
    )

    for emitted, nodes_by_canonical in (
        (first, first_by_canonical),
        (second, second_by_canonical),
    ):
        local_ids = {node["id"] for node in emitted["nodes"]}
        type_id = nodes_by_canonical["semantic:java:class:Same"]["id"]
        property_id = nodes_by_canonical[
            "semantic:java:class:Same:property:value"
        ]["id"]
        method_id = nodes_by_canonical[
            "semantic:java:class:Same:method:run"
        ]["id"]
        exception_id = nodes_by_canonical[
            "semantic:java:class:Same:method:run:throws:IOException"
        ]["id"]
        assert {type_id, property_id, method_id, exception_id} <= local_ids
        assert any(
            edge["source"] == type_id and edge["target"] == property_id
            for edge in emitted["edges"]
        )
        assert any(
            edge["source"] == type_id and edge["target"] == method_id
            for edge in emitted["edges"]
        )
        assert any(
            edge["source"] == method_id
            and edge["target"] == exception_id
            for edge in emitted["edges"]
        )


def test_same_java_class_in_two_paths_can_share_one_supplement(
    tmp_path: Path,
) -> None:
    source = "public class Same { private String value; public void run() {} }"
    RepositoryCodeCompassBridge(JavaSemanticAdapter()).build_outputs(
        source_id="repo",
        records=[
            {
                "content": source,
                "metadata": {"relative_path": path},
            }
            for path in ("src/Same.java", "lib/Same.java")
        ],
        output_dir=tmp_path,
    )

    with sqlite3.connect(
        tmp_path / DOMAIN_SUPPLEMENT_SOURCE_FILENAME
    ) as connection:
        rows = connection.execute(
            "SELECT node_id, record_json FROM semantic_nodes ORDER BY node_id"
        ).fetchall()
    same_types = [
        (node_id, json.loads(record_json))
        for node_id, record_json in rows
        if json.loads(record_json)["attributes"].get(
            CANONICAL_SEMANTIC_ID_ATTRIBUTE
        )
        == "semantic:java:class:Same"
    ]
    assert len(same_types) == 2
    assert len({node_id for node_id, _record in same_types}) == 2
    assert {record["provenance"]["file"] for _node_id, record in same_types} == {
        "src/Same.java",
        "lib/Same.java",
    }


def test_regex_module_nodes_keep_canonical_identity_but_use_local_edges() -> None:
    adapter = RegexSymbolLanguageAdapter(
        language="go",
        supported_extensions=(".go",),
        type_pattern="",
        function_pattern="",
        import_pattern=r'^\s*import\s+["`](?P<module>[^"`]+)',
        known_limits=("test",),
    )
    first = adapter.emit_graph_records("src/a.go", 'import "fmt"')
    second = adapter.emit_graph_records("src/b.go", 'import "fmt"')
    canonical_id = "semantic:go:module:fmt"
    first_module = _module_node(first, canonical_id=canonical_id)
    second_module = _module_node(second, canonical_id=canonical_id)

    assert first_module["id"] != second_module["id"]
    assert first["edges"][0]["target"] == first_module["id"]
    assert second["edges"][0]["target"] == second_module["id"]


def test_java_same_file_declarations_use_distinct_provenance_ids() -> None:
    emitted = JavaSemanticAdapter().emit_graph_records(
        "src/Duplicates.java",
        "class Same {} class Same {}\n",
    )

    same_nodes = [node for node in emitted["nodes"] if node["symbol"] == "Same"]
    assert len(same_nodes) == 2
    assert len({node["id"] for node in same_nodes}) == 2
    assert {node["provenance"]["line_start"] for node in same_nodes} == {1}
    assert {
        node["attributes"]["column_start"] for node in same_nodes
    } == {1, 15}


def test_java_detection_does_not_parse_language_snippets_in_other_files() -> None:
    adapter = JavaSemanticAdapter()

    assert adapter.detect("src/Same.java", "") is True
    assert adapter.detect("docs/example.md", "class Same {}") is False


def test_typescript_same_file_declarations_use_distinct_provenance_ids() -> None:
    emitted = TypeScriptSemanticAdapter().emit_graph_records(
        "src/duplicates.ts",
        "const run = () => 1; const run = () => 2;\n",
    )

    run_nodes = [node for node in emitted["nodes"] if node["symbol"] == "run"]
    assert len(run_nodes) == 2
    assert len({node["id"] for node in run_nodes}) == 2
    assert {
        node["attributes"]["column_start"] for node in run_nodes
    } == {1, 22}


def test_regex_and_static_symbols_use_occurrence_provenance() -> None:
    regex_adapter = RegexSymbolLanguageAdapter(
        language="go",
        supported_extensions=(".go",),
        type_pattern="",
        function_pattern=r"func\s+(?P<name>\w+)\s*\(",
        import_pattern="",
        known_limits=("test",),
    )
    regex_nodes = regex_adapter.emit_graph_records(
        "src/duplicates.go",
        "func run() {}; func run() {}\n",
    )["nodes"]
    lua_adapter = next(
        adapter
        for adapter in default_static_symbol_adapters()
        if adapter.language == "lua"
    )
    static_nodes = [
        node
        for node in lua_adapter.emit_graph_records(
            "src/duplicates.lua",
            "local function run() end\nlocal function run() end\n",
        )["nodes"]
        if node["attributes"]["kind"] == "function"
    ]

    assert len(regex_nodes) == 2
    assert len({node["id"] for node in regex_nodes}) == 2
    assert len(static_nodes) == 2
    assert len({node["id"] for node in static_nodes}) == 2


def test_generic_identity_requires_positive_provenance_line() -> None:
    identity = DeterministicSemanticSymbolIdentityFactory()

    with pytest.raises(
        ValueError,
        match="semantic_symbol_identity_provenance_invalid",
    ):
        identity.symbol_id(
            language="java",
            path="src/Same.java",
            symbol_kind="type",
            canonical_id="semantic:java:class:Same",
            local_qualifier="class:Same",
            provenance_line_start=0,
        )

    with pytest.raises(
        ValueError,
        match="semantic_symbol_identity_provenance_invalid",
    ):
        identity.symbol_id(
            language="java",
            path="src/Same.java",
            symbol_kind="type",
            canonical_id="semantic:java:class:Same",
            local_qualifier="class:Same",
            provenance_line_start=1,
            provenance_column_start=0,
        )


def test_generic_identity_distinguishes_same_line_columns() -> None:
    identity = DeterministicSemanticSymbolIdentityFactory()
    values = {
        identity.symbol_id(
            language="java",
            path="src/Same.java",
            symbol_kind="type",
            canonical_id="semantic:java:class:Same",
            local_qualifier="class:Same",
            provenance_line_start=1,
            provenance_column_start=column,
        )
        for column in (1, 15)
    }

    assert len(values) == 2


def test_typescript_inheritance_targets_unique_local_occurrence() -> None:
    emitted = TypeScriptSemanticAdapter().emit_graph_records(
        "src/model.ts",
        "class Base {}\nclass Child extends Base {}\n",
    )
    nodes = {node["symbol"]: node for node in emitted["nodes"]}
    inheritance = next(
        edge for edge in emitted["edges"] if edge["edge_type"] == "extends"
    )

    assert inheritance["source"] == nodes["Child"]["id"]
    assert inheritance["target"] == nodes["Base"]["id"]


def test_typescript_selectors_bind_to_exact_class_occurrences() -> None:
    emitted = TypeScriptSemanticAdapter().emit_graph_records(
        "src/components.ts",
        "@Component({selector: 'first-item'})\n"
        "class Same {}\n"
        "class Same {}\n"
        "@Component({selector: 'third-item'})\n"
        "class Same {}\n",
    )
    nodes = sorted(
        (node for node in emitted["nodes"] if node["symbol"] == "Same"),
        key=lambda node: node["provenance"]["line_start"],
    )

    assert [node["attributes"].get("selector") for node in nodes] == [
        "first-item",
        None,
        "third-item",
    ]
    assert [node["semantic_kind"] for node in nodes] == [
        "component",
        "data_record",
        "component",
    ]


def test_java_regex_fallback_reports_source_relative_columns() -> None:
    adapter = JavaSemanticAdapter()
    source = (
        "class C { int x; int y; }\n"
        "record R(int first,\n"
        "         String second) {}\n"
    )
    parsed = adapter._parse_types("src/Columns.java", source)
    class_fields = parsed[0]["properties"]
    record_fields = parsed[1]["properties"]

    assert [field["column_start"] for field in class_fields] == [
        source.index("int x") + 1,
        source.index("int y") + 1,
    ]
    assert [
        (field["line_start"], field["column_start"])
        for field in record_fields
    ] == [(2, 10), (3, 10)]


def test_legacy_generic_identity_injection_is_scoped_by_occurrence() -> None:
    class LegacyIdentity:
        def symbol_id(
            self,
            *,
            language: str,
            path: str,
            symbol_kind: str,
            canonical_id: str,
            local_qualifier: str,
        ) -> str:
            return ":".join(
                (language, path, symbol_kind, canonical_id, local_qualifier)
            )

    emitted = JavaSemanticAdapter(
        symbol_identity=LegacyIdentity(),
    ).emit_graph_records(
        "src/Duplicates.java",
        "class Same {} class Same {}\n",
    )
    same_nodes = [node for node in emitted["nodes"] if node["symbol"] == "Same"]

    assert len(same_nodes) == 2
    assert len({node["id"] for node in same_nodes}) == 2
