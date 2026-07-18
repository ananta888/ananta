from __future__ import annotations

import json
from pathlib import Path

from codecompass_rag import (
    AdocExtractor,
    CSharpExtractor,
    JavaExtractor,
    TextFileExtractor,
    XmlExtractor,
    XsdExtractor,
)
from rag_helper.application.cross_file_relation_resolver import resolve_cross_file_relations
from rag_helper.application.processing_limits import ProcessingLimits
from rag_helper.application.project_processor import process_project
from rag_helper.extractors.angular_asset_extractor import AngularTemplateExtractor
from rag_helper.extractors.diagram_extractor import DiagramExtractor
from rag_helper.extractors.documentation_extractor import DocumentationExtractor
from rag_helper.extractors.infrastructure_extractor import DockerfileExtractor, YamlInfrastructureExtractor


def test_cross_file_resolver_links_angular_dockerfiles_documents_and_diagram_anchors() -> None:
    index: list[dict] = []
    details: list[dict] = []
    relations: list[dict] = []

    samples = [
        TextFileExtractor().parse(
            "frontend/app-card.component.ts",
            "@Component({ selector: 'app-card', templateUrl: './app-card.component.html' })\nexport class AppCard {}\n",
        ),
        AngularTemplateExtractor().parse("frontend/app.component.html", "<app-card></app-card>\n"),
        DockerfileExtractor().parse("Dockerfile.api", "FROM python:3.13\n"),
        YamlInfrastructureExtractor().parse(
            "deploy/compose.yml",
            "services:\n  api:\n    build:\n      context: ..\n      dockerfile: Dockerfile.api\n",
        ),
        DocumentationExtractor().parse("docs/guide.md", "# Guide\n## Usage\n"),
        DocumentationExtractor().parse("docs/README.md", "# Docs\n[usage](guide.md#usage)\n"),
        DiagramExtractor().parse(
            "architecture/system.mmd",
            'flowchart TD\nAPI[API]\nclick API "../docs/guide.md#usage"\n',
        ),
    ]
    for sample_index, sample_details, sample_relations, _ in samples:
        index.extend(sample_index)
        details.extend(sample_details)
        relations.extend(sample_relations)

    stats = resolve_cross_file_relations(index, details, relations)

    angular = next(item for item in relations if item.get("relation") == "uses_component_selector")
    dockerfile = next(item for item in relations if item.get("relation") == "uses_dockerfile")
    document_links = [item for item in relations if item.get("relation") == "references_document"]
    assert angular["resolution_status"] == "resolved"
    assert angular["target_file"] == "frontend/app-card.component.ts"
    assert dockerfile["resolution_status"] == "resolved"
    assert dockerfile["target_file"] == "Dockerfile.api"
    assert len(document_links) == 2
    assert all(item["resolution_status"] == "resolved" for item in document_links)
    assert all(item["target_file"] == "docs/guide.md" for item in document_links)
    assert stats["resolved"] >= 4


def test_cross_file_resolver_marks_duplicate_selectors_ambiguous_and_blocks_path_escape() -> None:
    index: list[dict] = []
    details: list[dict] = []
    relations: list[dict] = []
    for path in ("a/card.ts", "b/card.ts"):
        parsed = TextFileExtractor().parse(
            path,
            "@Component({ selector: 'app-card' })\nexport class Card {}\n",
        )
        index.extend(parsed[0])
        details.extend(parsed[1])
        relations.extend(parsed[2])
    template = AngularTemplateExtractor().parse("app.html", "<app-card></app-card>")
    index.extend(template[0])
    details.extend(template[1])
    relations.extend(template[2])
    docs = DocumentationExtractor().parse("docs/readme.md", "# Docs\n[x](../../outside.md)\n")
    index.extend(docs[0])
    details.extend(docs[1])
    relations.extend(docs[2])

    stats = resolve_cross_file_relations(index, details, relations)
    selector = next(item for item in relations if item.get("relation") == "uses_component_selector")
    escaped = next(item for item in relations if item.get("relation") == "references_document")
    assert selector["resolution_status"] == "ambiguous"
    assert [item["file"] for item in selector["resolution_candidates"]] == ["a/card.ts", "b/card.ts"]
    assert escaped["resolution_status"] == "blocked_outside_repository"
    assert stats["ambiguous"] == 1
    assert stats["blocked_outside_repository"] == 1


def test_mixed_project_pipeline_applies_cross_file_resolution_before_writing(tmp_path: Path) -> None:
    project = tmp_path / "project"
    out = tmp_path / "out"
    for directory in ("frontend", "docs", "architecture", "deploy"):
        (project / directory).mkdir(parents=True, exist_ok=True)
    (project / "frontend/app-card.component.ts").write_text(
        "@Component({ selector: 'app-card' })\nexport class AppCard {}\n", encoding="utf-8"
    )
    (project / "frontend/app.component.html").write_text("<app-card></app-card>\n", encoding="utf-8")
    (project / "docs/guide.md").write_text("# Guide\n## Usage\n", encoding="utf-8")
    (project / "docs/README.md").write_text("[usage](guide.md#usage)\n", encoding="utf-8")
    (project / "architecture/system.mmd").write_text(
        'flowchart TD\nAPI[API]\nclick API "../docs/guide.md#usage"\n', encoding="utf-8"
    )
    (project / "deploy/compose.yml").write_text(
        "services:\n  api:\n    build:\n      context: ..\n      dockerfile: Dockerfile.api\n",
        encoding="utf-8",
    )
    (project / "Dockerfile.api").write_text("FROM python:3.13\n", encoding="utf-8")

    process_project(
        root=project,
        out_dir=out,
        extensions={"ts", "html", "md", "mmd", "yml", "dockerfile"},
        excludes=set(),
        include_code_snippets=False,
        exclude_trivial_methods=False,
        include_xml_node_details=False,
        include_globs=None,
        exclude_globs=None,
        limits=ProcessingLimits(),
        java_extractor_cls=JavaExtractor,
        csharp_extractor_cls=CSharpExtractor,
        adoc_extractor_cls=AdocExtractor,
        xml_extractor_cls=XmlExtractor,
        xsd_extractor_cls=XsdExtractor,
        text_extractor_cls=TextFileExtractor,
    )

    written_relations = [
        json.loads(line) for line in (out / "relations.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    resolved_types = {item["relation"] for item in written_relations if item.get("resolution_status") == "resolved"}
    assert {"uses_component_selector", "uses_dockerfile", "references_document"} <= resolved_types
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["cross_file_resolution"]["resolved"] >= 4
