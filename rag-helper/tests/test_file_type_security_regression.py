from __future__ import annotations

import json
from pathlib import Path

import pytest
from codecompass_rag import (
    AdocExtractor,
    CSharpExtractor,
    JavaExtractor,
    TextFileExtractor,
    XmlExtractor,
    XsdExtractor,
)
from rag_helper.application.cross_file_relation_resolver import (
    resolve_cross_file_relations,
)
from rag_helper.application.file_scanner import (
    build_file_snapshots,
    collect_files,
)
from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.application.project_processor import process_project
from rag_helper.extractors.configuration_extractor import ConfigurationExtractor
from rag_helper.extractors.documentation_extractor import DocumentationExtractor
from rag_helper.extractors.tabular_notebook_extractor import NotebookExtractor
from rag_helper.filesystem.file_filters import effective_extension


def _write_project(root: Path, files: dict[str, str]) -> None:
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _process(root: Path, output: Path, extensions: set[str], *, max_workers: int = 2) -> None:
    process_project(
        root=root,
        out_dir=output,
        extensions=extensions,
        excludes=set(),
        include_code_snippets=False,
        exclude_trivial_methods=False,
        include_xml_node_details=False,
        include_globs=None,
        exclude_globs=None,
        limits=ProcessingLimits(
            max_file_size_kb=64,
            max_parser_lines=2_000,
            max_workers=max_workers,
        ),
        java_extractor_cls=JavaExtractor,
        csharp_extractor_cls=CSharpExtractor,
        adoc_extractor_cls=AdocExtractor,
        xml_extractor_cls=XmlExtractor,
        xsd_extractor_cls=XsdExtractor,
        text_extractor_cls=TextFileExtractor,
    )


def test_mixed_p0_repository_keeps_every_format_and_writes_deterministic_outputs(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    first_output = tmp_path / "first-output"
    second_output = tmp_path / "second-output"
    files = {
        "src/model.py": "class Model:\n    pass\n",
        "src/Model.java": "package demo; public class Model {}\n",
        "src/app.ts": "export class AppService { run(): boolean { return true; } }\n",
        "src/app.tsx": "export const AppView = () => <main>Safe</main>;\n",
        "docs/guide.md": "# Guide\n[Config](../config/app.yaml)\n",
        "docs/page.mdx": "# Page\n<Component />\n",
        "docs/reference.rst": "Reference\n=========\n",
        "docs/overview.adoc": "= Overview\n\n== Details\n",
        "web/app.component.html": "<main class=\"app\"><app-card /></main>\n",
        "web/styles.css": ".app { color: red; }\n",
        "web/styles.scss": "$gap: 1rem;\n.app { gap: $gap; }\n",
        "web/styles.sass": "$gap: 1rem\n.app\n  gap: $gap\n",
        "web/styles.less": "@gap: 1rem;\n.app { gap: @gap; }\n",
        "config/app.yaml": "service:\n  enabled: true\n",
        "config/app.yml": "feature:\n  enabled: true\n",
        "config/app.toml": "[service]\nenabled = true\n",
        "config/app.ini": "[service]\nenabled=true\n",
        "config/app.cfg": "feature=true\n",
        "config/app.conf": "[http]\nport=8080\n",
        "config/app.properties": "app.name=ananta\n",
        "config/app.xml": "<config><service enabled=\"true\" /></config>\n",
        "Dockerfile": "FROM python:3.13\n",
        "Containerfile": "FROM alpine:3.21\n",
        "docker-compose.yml": "services:\n  api:\n    build: .\n",
        ".github/workflows/ci.yml": (
            "on: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - uses: actions/checkout@v4\n"
        ),
        "scripts/run.sh": "#!/bin/sh\nrun() { printf safe; }\n",
        "scripts/run.bash": "#!/usr/bin/env bash\nbuild() { :; }\n",
        "scripts/run.zsh": "#!/usr/bin/env zsh\nfunction deploy { :; }\n",
        "scripts/run.fish": "#!/usr/bin/env fish\nfunction check\nend\n",
        "scripts/deploy.ps1": "function Invoke-Deploy { Write-Output 'safe' }\n",
        "scripts/module.psm1": "function Get-State { return 'safe' }\n",
        "Makefile": "all:\n\t@echo safe\n",
        "Jenkinsfile": "pipeline { stages { stage('Build') {} } }\n",
        "db/schema.sql": "CREATE TABLE runs (id bigint PRIMARY KEY, status varchar(32));\n",
        "architecture/flow.mmd": "flowchart TD\nA[Start] --> B[End]\n",
        "architecture/sequence.mermaid": "sequenceDiagram\nA->>B: ping\n",
        "architecture/components.puml": "@startuml\nA --> B\n@enduml\n",
        "architecture/model.plantuml": "@startuml\nclass Model\n@enduml\n",
        "architecture/system.dot": "digraph G { api -> db; }\n",
        "architecture/system.gv": "digraph G { web -> api; }\n",
        "architecture/system.drawio": (
            "<mxfile><diagram name=\"System\"><mxGraphModel><root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="api" value="API" vertex="1" parent="1"/>'
            "</root></mxGraphModel></diagram></mxfile>\n"
        ),
    }
    _write_project(project, files)
    extensions = {effective_extension(Path(path)) for path in files}

    _process(project, first_output, extensions)
    _process(project, second_output, extensions)

    manifest = json.loads((first_output / "manifest.json").read_text(encoding="utf-8"))
    by_path = {entry["file"]: entry for entry in manifest["files"]}
    assert set(by_path) == set(files)
    assert all(not entry.get("skipped") and not entry.get("error") for entry in by_path.values())
    assert all(entry.get("output_record_count", 0) > 0 for entry in by_path.values())
    for filename in ("index.jsonl", "details.jsonl", "relations.jsonl"):
        assert (first_output / filename).read_bytes() == (second_output / filename).read_bytes()


def test_scanner_blocks_symlink_escape_binary_text_suffix_and_pre_read_oversize(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / "safe.md").write_text("# Safe\n", encoding="utf-8")
    (project / "binary.md").write_bytes(b"\x89PNG\x00\x01not-markdown")
    (project / "large.md").write_text("x" * 2048, encoding="utf-8")
    outside_secret = outside / "secret.md"
    outside_secret.write_text("OUTSIDE_SECRET_MUST_NOT_LEAK", encoding="utf-8")
    try:
        (project / "linked.md").symlink_to(outside_secret)
        (project / "linked-directory").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not supported on this platform")

    files = collect_files(root=project, extensions={"md"}, excludes=set())
    snapshots = build_file_snapshots(files, project, max_file_size_kb=1)
    by_path = {snapshot.rel_path: snapshot for snapshot in snapshots}

    assert set(by_path) == {"binary.md", "large.md", "safe.md"}
    assert by_path["safe.md"].text == "# Safe\n"
    assert by_path["binary.md"].text is None
    assert by_path["large.md"].text is None


@pytest.mark.parametrize(
    ("extractor", "source", "expected_code"),
    [
        (
            ConfigurationExtractor(max_aliases=3),
            "base: &base {enabled: true}\n" + "".join(f"item_{index}: *base\n" for index in range(20)),
            "yaml_alias_limit_exceeded",
        ),
        (
            ConfigurationExtractor(max_nodes=3),
            "a: 1\nb: 2\nc: 3\n",
            "yaml_node_limit_exceeded",
        ),
        (
            ConfigurationExtractor(max_depth=2),
            "root:\n  nested:\n    too_deep: true\n",
            "yaml_depth_limit_exceeded",
        ),
    ],
)
def test_yaml_bombs_fail_to_bounded_text_index_deterministically(
    extractor: ConfigurationExtractor,
    source: str,
    expected_code: str,
) -> None:
    first = extractor.parse("config/untrusted.yml", source)
    second = extractor.parse("config/untrusted.yml", source)

    assert first == second
    index, details, relations, stats = first
    assert index[0]["parser_mode"] == "text_index"
    assert relations == []
    assert details[0]["code"] == expected_code
    assert details[0]["severity"] == "error"
    assert stats["diagnostic_codes"] == [expected_code]


def test_notebook_bounds_parsing_and_redacts_sources_outputs_and_attachments() -> None:
    raw_secret = "NOTEBOOK_CELL_SECRET_MUST_NOT_LEAK"
    notebook = {
        "metadata": {"language_info": {"name": "python"}},
        "cells": [
            {
                "cell_type": "markdown",
                "source": ["# Safe heading\n"],
                "attachments": {"image.png": {"image/png": "ATTACHMENT_MUST_NOT_LEAK"}},
            },
            {
                "cell_type": "code",
                "source": [
                    f'api_key = "{raw_secret}"\n',
                    "safe = 1\n" * 20,
                    "def SYMBOL_BEYOND_LIMIT():\n    pass\n",
                ],
                "outputs": [
                    {"output_type": "stream", "text": "OUTPUT_SECRET_MUST_NOT_LEAK"},
                ],
            },
        ],
    }
    extractor = NotebookExtractor(max_cells=10, max_cell_chars=96)

    first = extractor.parse("notebooks/untrusted.ipynb", json.dumps(notebook))
    second = extractor.parse("notebooks/untrusted.ipynb", json.dumps(notebook))
    serialized = json.dumps(first, sort_keys=True)
    code_cell = next(item for item in first[1] if item.get("kind") == "ipynb_code_cell")

    assert first == second
    assert code_cell["source_truncated"] is True
    assert code_cell["source_redacted"] is True
    assert "[REDACTED]" in code_cell["source"]
    assert code_cell["outputs_included"] is False
    assert not any(symbol["name"] == "SYMBOL_BEYOND_LIMIT" for symbol in code_cell["symbols"])
    assert "notebook_secret_value_redacted" in first[3]["diagnostic_codes"]
    for forbidden in (
        raw_secret,
        "ATTACHMENT_MUST_NOT_LEAK",
        "OUTPUT_SECRET_MUST_NOT_LEAK",
        "SYMBOL_BEYOND_LIMIT",
    ):
        assert forbidden not in serialized


def test_cross_file_resolution_blocks_encoded_file_uri_and_drive_traversal() -> None:
    index, details, relations, _ = DocumentationExtractor().parse(
        "docs/guide.md",
        (
            "# Guide\n"
            "[encoded](%2e%2e/%2e%2e/outside.md)\n"
            "[file](file:///etc/passwd)\n"
            "[drive](C:\\outside.md)\n"
            "[remote](https://example.invalid/guide.md)\n"
        ),
    )

    stats = resolve_cross_file_relations(index, details, relations)
    by_target = {relation["target"]: relation for relation in relations}

    assert by_target["%2e%2e/%2e%2e/outside.md"]["resolution_status"] == "blocked_outside_repository"
    assert by_target["file:///etc/passwd"]["resolution_status"] == "blocked_outside_repository"
    assert by_target["C:\\outside.md"]["resolution_status"] == "blocked_outside_repository"
    assert by_target["https://example.invalid/guide.md"]["resolution_status"] == "external"
    assert stats["blocked_outside_repository"] == 3
    assert stats["external"] == 1
    assert all(
        not relation.get("target_resolved")
        for relation in relations
        if relation.get("relation") == "references_document"
    )
