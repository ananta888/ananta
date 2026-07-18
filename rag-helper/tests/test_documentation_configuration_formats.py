from __future__ import annotations

import json

from rag_helper.extractors.configuration_extractor import ConfigurationExtractor
from rag_helper.extractors.documentation_extractor import DocumentationExtractor


def test_markdown_keeps_hierarchy_frontmatter_links_lists_and_inert_code_lines() -> None:
    extractor = DocumentationExtractor(embedding_text_mode="compact")
    index, details, relations, stats = extractor.parse(
        "docs/README.md",
        (
            "---\n"
            "title: Demo\n"
            "token: never-copy-this\n"
            "---\n"
            "# Intro\n"
            "- first\n"
            "[jump](#usage)\n"
            "## Usage\n"
            "```python\n"
            "raise RuntimeError('must not execute')\n"
            "```\n"
        ),
    )

    sections = [record for record in details if record["kind"] == "md_section"]
    code = next(record for record in details if record["kind"] == "md_code_block")
    frontmatter = next(record for record in details if record["kind"] == "md_frontmatter")
    anchor = next(record for record in relations if record["relation"] == "references_anchor")

    assert index[0]["summary"]["heading_count"] == 2
    assert sections[1]["parent_id"] == sections[0]["id"]
    assert sections[0]["line"] == 5
    assert code["line"] == 9 and code["end_line"] == 11
    assert code["executed"] is False
    assert frontmatter["keys"] == ["title", "token"]
    assert "never-copy-this" not in json.dumps((index, details, relations))
    assert anchor["target_resolved"] == sections[1]["id"]
    assert stats["list_item_count"] == 1


def test_mdx_and_rst_are_structural_without_evaluating_embedded_code() -> None:
    extractor = DocumentationExtractor()
    _, mdx_details, _, mdx_stats = extractor.parse(
        "docs/page.mdx",
        "import Widget from './Widget'\n# Page\n<Widget answer={dangerous()} />\n",
    )
    _, rst_details, _, rst_stats = extractor.parse(
        "docs/page.rst",
        ("Title\n=====\n\n* item\n\n.. code-block:: shell\n\n   rm -rf /never-executed\n"),
    )

    assert any(record["kind"] == "mdx_import" for record in mdx_details)
    assert any(record["kind"] == "mdx_component_reference" for record in mdx_details)
    assert mdx_stats["heading_count"] == 1
    rst_code = next(record for record in rst_details if record["kind"] == "rst_code_block")
    assert rst_code["executed"] is False
    assert rst_code["line"] == 6
    assert rst_stats["list_item_count"] == 1


def test_yaml_multidocument_key_paths_and_secret_values_are_redacted() -> None:
    extractor = ConfigurationExtractor()
    index, details, relations, stats = extractor.parse(
        "config/app.yaml",
        "server:\n  port: 8080\n  password: top-secret\n---\nfeature:\n  enabled: true\n",
    )

    keys = [record for record in details if record["kind"] == "yaml_key"]
    by_path = {record["key_path"]: record for record in keys}
    assert by_path["server.port"]["line"] == 2
    assert by_path["server.password"]["value_redacted"] is True
    assert by_path["feature.enabled"]["document"] == 2
    assert index[0]["summary"]["document_count"] == 2
    assert stats["document_count"] == 2
    assert all(relation["line"] >= 1 for relation in relations)
    assert "top-secret" not in json.dumps((index, details, relations, stats))


def test_yaml_alias_limit_and_invalid_toml_fall_back_with_diagnostics() -> None:
    alias_limited = ConfigurationExtractor(max_aliases=2)
    index, details, _, stats = alias_limited.parse(
        "config/aliases.yml",
        "base: &base {enabled: true}\na: *base\nb: *base\nc: *base\n",
    )
    assert index[0]["parser_mode"] == "text_index"
    assert details[0]["code"] == "yaml_alias_limit_exceeded"
    assert stats["fallback"] == "text_index"

    index, details, _, stats = ConfigurationExtractor().parse("pyproject.toml", "[project\nname='x'")
    assert index[0]["parser_mode"] == "text_index"
    assert details[0]["code"] == "toml_parse_error"
    assert stats["diagnostic_count"] == 1


def test_toml_ini_conf_and_properties_emit_key_paths_without_values() -> None:
    extractor = ConfigurationExtractor()
    cases = {
        "app.toml": ("[db]\npassword='hidden'\nport=5432\n", {"db", "db.password", "db.port"}),
        "app.ini": ("[server]\nport=8080\ntoken=hidden\n", {"server", "server.port", "server.token"}),
        "app.cfg": ("feature=true\nname=demo\n", {"feature", "name"}),
        "app.conf": ("[http]\nhost=localhost\n", {"http", "http.host"}),
        "app.properties": ("app.name=demo\napi_key=hidden\n", {"app.name", "api_key"}),
    }
    for rel_path, (source, expected) in cases.items():
        _, details, _, _ = extractor.parse(rel_path, source)
        paths = {record["key_path"] for record in details if "key_path" in record}
        assert expected <= paths
        assert "hidden" not in json.dumps(details)
