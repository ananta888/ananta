from __future__ import annotations

import json
from pathlib import Path

from rag_helper.extractors.angular_asset_extractor import (
    AngularTemplateExtractor,
    StylesheetExtractor,
)
from rag_helper.extractors.infrastructure_extractor import (
    BuildScriptExtractor,
    DockerfileExtractor,
    YamlInfrastructureExtractor,
)


def test_angular_template_extracts_components_bindings_directives_pipes_and_blocks() -> None:
    index, details, relations, stats = AngularTemplateExtractor().parse(
        "frontend-angular/src/app/widget.component.html",
        (
            '<app-card #card [title]="title | uppercase" (saved)="save($event)" '
            '*ngIf="visible">{{ value | async }}</app-card>\n'
            '@for (item of items; track item.id) { <button (click)="pick(item)">x</button> }\n'
            "@switch (state) { @case ('ready') { ready } }\n"
        ),
    )

    kinds = {record["kind"] for record in details}
    assert {
        "angular_template_reference",
        "angular_input_binding",
        "angular_output_binding",
        "angular_directive",
        "angular_pipe_reference",
        "angular_control_flow",
    } <= kinds
    component_relation = next(item for item in relations if item["relation"] == "uses_component_selector")
    assert component_relation["target"] == "app-card"
    assert component_relation["resolution_status"] == "unresolved"
    assert index[0]["summary"]["component_tag_count"] == 1
    assert stats["directive_count"] >= 4


def test_angular_extractor_handles_representative_repository_template_and_stylesheet() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    template_path = repository_root / "frontend-angular/src/app/components/settings-llm.component.html"
    stylesheet_path = repository_root / "frontend-angular/src/styles.css"
    template_text = template_path.read_text(encoding="utf-8")
    stylesheet_text = stylesheet_path.read_text(encoding="utf-8")

    _, template_details, _, template_stats = AngularTemplateExtractor().parse(
        str(template_path.relative_to(repository_root)), template_text
    )
    style_index, style_details, _, _ = StylesheetExtractor().parse(
        str(stylesheet_path.relative_to(repository_root)), stylesheet_text
    )
    control_flow = {item["name"] for item in template_details if item["kind"] == "angular_control_flow"}
    assert {"@if", "@for"} <= control_flow
    assert template_stats["directive_count"] >= 2
    assert style_index[0]["summary"]["selector_count"] >= 1
    assert any(item.get("kind") == "css_selector" for item in style_details)


def test_stylesheets_extract_selectors_custom_properties_preprocessor_variables_and_imports() -> None:
    extractor = StylesheetExtractor()
    for rel_path, source in {
        "styles.css": '@import "theme.css";\n:root { --brand: blue; }\n.card, app-card { color: red; }\n',
        "styles.scss": '@use "tokens";\n$gap: 1rem;\n.card { --local: 1; }\n',
        "styles.less": '@import "tokens.less";\n@gap: 1rem;\n.card { color: red; }\n',
        "styles.sass": "$gap: 1rem\n.card\n  color: red\n",
    }.items():
        index, details, relations, _ = extractor.parse(rel_path, source)
        assert index[0]["summary"]["selector_count"] >= 1
        if "@import" in source or "@use" in source:
            assert any(item["relation"] == "imports_stylesheet" for item in relations)
        assert all(record.get("line", 1) >= 1 for record in details)


def test_dockerfile_extracts_stages_images_copy_env_ports_and_entrypoint_without_values() -> None:
    index, details, relations, stats = DockerfileExtractor().parse(
        "Dockerfile",
        (
            "FROM python:3.13 AS build\n"
            "ENV API_TOKEN=never-copy DEBUG=true\n"
            "COPY pyproject.toml /app/\n"
            "FROM gcr.io/distroless/python3 AS runtime\n"
            "COPY --from=build /app /app\n"
            "EXPOSE 8080/tcp\n"
            'ENTRYPOINT ["python", "-m", "app"]\n'
        ),
    )

    assert index[0]["summary"]["stage_count"] == 2
    assert stats["copy_count"] == 2
    assert any(record["kind"] == "docker_env_key" and record["name"] == "API_TOKEN" for record in details)
    assert any(item["relation"] == "uses_base_image" for item in relations)
    assert any(item["relation"] == "copies_from_stage" for item in relations)
    assert "never-copy" not in json.dumps((index, details, relations, stats))


def test_compose_links_services_images_builds_networks_volumes_and_dependencies() -> None:
    index, details, relations, stats = YamlInfrastructureExtractor().parse(
        "deploy/compose.yml",
        (
            "services:\n"
            "  api:\n"
            "    build:\n"
            "      context: .\n"
            "      dockerfile: Dockerfile.api\n"
            "    depends_on: [db]\n"
            "    environment:\n"
            "      DB_PASSWORD: never-copy\n"
            "    networks: [backend]\n"
            "    volumes: [data:/data]\n"
            "    healthcheck:\n"
            "      test: [CMD, true]\n"
            "  db:\n"
            "    image: postgres:17\n"
            "networks:\n"
            "  backend: {}\n"
            "volumes:\n"
            "  data: {}\n"
        ),
    )

    api = next(record for record in details if record["kind"] == "compose_service" and record["name"] == "api")
    dependency = next(item for item in relations if item["relation"] == "depends_on_service")
    assert api["environment_keys"] == ["DB_PASSWORD"]
    assert api["has_healthcheck"] is True
    assert dependency["resolution_status"] == "resolved"
    assert {item["relation"] for item in relations} >= {
        "builds_from_context",
        "uses_dockerfile",
        "uses_network",
        "mounts_volume",
        "uses_image",
    }
    assert stats["service_count"] == 2
    assert "never-copy" not in json.dumps((index, details, relations, stats))


def test_github_actions_records_jobs_steps_needs_triggers_actions_and_secret_names_only() -> None:
    index, details, relations, stats = YamlInfrastructureExtractor().parse(
        ".github/workflows/ci.yml",
        (
            "on: [push, pull_request]\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - name: Test\n"
            "        run: pytest\n"
            "        env:\n"
            "          TOKEN: ${{ secrets.DEPLOY_TOKEN }}\n"
            "  publish:\n"
            "    needs: build\n"
            "    steps:\n"
            "      - uses: docker/login-action@v3\n"
        ),
    )

    assert index[0]["summary"]["job_count"] == 2
    assert stats["action_count"] == 2
    assert any(
        record["kind"] == "github_actions_secret_reference" and record["name"] == "DEPLOY_TOKEN" for record in details
    )
    assert next(item for item in relations if item["relation"] == "needs_job")["resolution_status"] == "resolved"
    serialized = json.dumps((index, details, relations, stats))
    assert "DEPLOY_TOKEN" in serialized
    assert "secrets.DEPLOY_TOKEN" not in serialized


def test_extensionless_build_dispatch_extracts_make_targets_and_jenkins_stages() -> None:
    extractor = BuildScriptExtractor()
    _, make_details, make_relations, make_stats = extractor.parse(
        "Makefile", "all: build test\nbuild:\n\t@echo build\ntest: build\n\tpytest\n"
    )
    _, jenkins_details, _, jenkins_stats = extractor.parse(
        "Jenkinsfile", "pipeline { stages { stage('Build') { steps { sh 'make' } } stage(\"Test\") {} } }"
    )
    assert {item["name"] for item in make_details} >= {"all", "build", "test"}
    assert any(
        item["relation"] == "depends_on_target" and item["resolution_status"] == "resolved" for item in make_relations
    )
    assert make_stats["target_count"] == 3
    assert [item["name"] for item in jenkins_details] == ["Build", "Test"]
    assert jenkins_stats["stage_count"] == 2
