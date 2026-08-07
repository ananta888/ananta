from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from agent.codecompass.semantic_translation.adapters import (
    JavaSemanticAdapter,
)
from agent.codecompass.semantic_translation.semantic_symbol_identity import (
    CANONICAL_SEMANTIC_ID_ATTRIBUTE,
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
