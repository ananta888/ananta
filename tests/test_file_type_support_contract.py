from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ananta_contracts.file_type_classifier import FileTypeClassifier, FileTypeMatchKind
from ananta_contracts.file_type_support import (
    CapabilityDimension,
    FileTypeSupportContractError,
    FileTypeSupportRegistry,
    load_file_type_support_registry,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "codecompass" / "file_type_support_registry.v1.json"
REGISTRY_PATH = ROOT / "config" / "codecompass" / "file_type_support.v1.json"


def _registry() -> FileTypeSupportRegistry:
    return load_file_type_support_registry(ROOT)


def _unsupported() -> dict[str, object]:
    return {
        "implementation": "unsupported",
        "verified": False,
        "producer": None,
        "evidence": [],
        "runtime_requirements": [],
    }


def _format(format_id: str, *, exact: list[str], priority: int | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "format_id": format_id,
        "display_name": format_id,
        "family": "fallback",
        "priority": "P2",
        "enabled": True,
        "selectors": {
            "exact_filenames": exact,
            "filename_patterns": [],
            "compound_suffixes": [],
            "extensions": [],
            "shebang_patterns": [],
            "text_fallback": False,
        },
        "security_class": "untrusted_text",
        "parser_strategy": "none",
        "fallback_strategy": "none",
        "known_limits": [],
        "pipeline_support": {},
    }
    if priority is not None:
        payload["match_priority"] = priority
    return payload


def _mapping(*formats: dict[str, object]) -> dict[str, object]:
    return {
        "schema": "codecompass.file-type-support-registry.v1",
        "registry_version": "1.0.0",
        "support_dimensions": ["indexed", "symbols", "relationships"],
        "pipelines": ["test"],
        "formats": list(formats),
    }


def test_repository_registry_and_schema_are_valid() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)
    assert list(Draft202012Validator(schema).iter_errors(payload)) == []
    registry = FileTypeSupportRegistry.load(REGISTRY_PATH, schema_path=SCHEMA_PATH)
    assert registry.registry_version == "1.1.0"
    assert len(registry.descriptors) >= 45


@pytest.mark.parametrize(
    ("path", "first_line", "is_text", "format_id", "match_kind"),
    [
        ("Dockerfile", None, True, "dockerfile", FileTypeMatchKind.EXACT_FILENAME),
        ("docker-compose.dev.yml", None, True, "docker_compose", FileTypeMatchKind.FILENAME_PATTERN),
        (".github/workflows/ci.yml", None, True, "github_actions", FileTypeMatchKind.FILENAME_PATTERN),
        ("src/app.component.html", None, True, "html", FileTypeMatchKind.COMPOUND_SUFFIX),
        ("docs/README.md", None, True, "markdown", FileTypeMatchKind.EXTENSION),
        ("tools/run", "#!/usr/bin/env bash", True, "shell", FileTypeMatchKind.SHEBANG),
        ("NOTICE.unknown", None, True, "plain_text", FileTypeMatchKind.TEXT_FALLBACK),
        ("infra/variables.tfvars.json", None, True, "terraform", FileTypeMatchKind.COMPOUND_SUFFIX),
    ],
)
def test_classifier_uses_documented_precedence(
    path: str,
    first_line: str | None,
    is_text: bool,
    format_id: str,
    match_kind: FileTypeMatchKind,
) -> None:
    result = FileTypeClassifier(_registry()).classify(path, first_line=first_line, is_text=is_text)

    assert result is not None
    assert result.format_id == format_id
    assert result.match_kind is match_kind


def test_extension_precedes_shebang_and_binary_does_not_use_text_fallback() -> None:
    classifier = FileTypeClassifier(_registry())

    python = classifier.classify("script.py", first_line="#!/usr/bin/env bash", is_text=True)

    assert python is not None and python.format_id == "python"
    assert python.match_kind is FileTypeMatchKind.EXTENSION
    assert classifier.classify("blob.unknown", is_text=False) is None


def test_duplicate_active_selector_requires_distinct_explicit_priorities() -> None:
    with pytest.raises(FileTypeSupportContractError, match="ambiguous active exact_filename"):
        FileTypeSupportRegistry.from_mapping(
            _mapping(_format("first", exact=["Containerfile"]), _format("second", exact=["Containerfile"]))
        )

    registry = FileTypeSupportRegistry.from_mapping(
        _mapping(
            _format("first", exact=["Containerfile"], priority=10),
            _format("second", exact=["Containerfile"], priority=20),
        )
    )
    result = FileTypeClassifier(registry).classify("Containerfile")
    assert result is not None and result.format_id == "second"


def test_relationship_support_requires_symbols_and_indexing() -> None:
    descriptor = _format("broken", exact=["Brokenfile"])
    descriptor["pipeline_support"] = {
        "test": {
            "indexed": _unsupported(),
            "symbols": _unsupported(),
            "relationships": {
                "implementation": "heuristic",
                "verified": False,
                "producer": "test.producer",
                "evidence": ["tests/test_file_type_support_contract.py"],
                "runtime_requirements": [],
            },
        }
    }

    with pytest.raises(FileTypeSupportContractError, match="relationships require symbol support"):
        FileTypeSupportRegistry.from_mapping(_mapping(descriptor))


def test_verified_support_requires_test_evidence() -> None:
    descriptor = _format("unverified_evidence", exact=["Evidencefile"])
    descriptor["pipeline_support"] = {
        "test": {
            "indexed": {
                "implementation": "text_fallback",
                "verified": True,
                "producer": "test.producer",
                "evidence": ["agent/example.py"],
                "runtime_requirements": [],
            },
            "symbols": _unsupported(),
            "relationships": _unsupported(),
        }
    }

    with pytest.raises(FileTypeSupportContractError, match="requires test evidence"):
        FileTypeSupportRegistry.from_mapping(_mapping(descriptor))


def test_programmatic_registry_rejects_unknown_runtime_requirement_kind() -> None:
    descriptor = _format("bad_runtime", exact=["Runtimefile"])
    descriptor["pipeline_support"] = {
        "test": {
            "indexed": {
                "implementation": "text_fallback",
                "verified": True,
                "producer": "test.producer",
                "evidence": ["tests/test_file_type_support_contract.py"],
                "runtime_requirements": ["user-controlled:parser"],
            },
            "symbols": _unsupported(),
            "relationships": _unsupported(),
        }
    }

    with pytest.raises(FileTypeSupportContractError, match="invalid runtime requirement"):
        FileTypeSupportRegistry.from_mapping(_mapping(descriptor))


def test_matrix_keeps_configured_runtime_verified_and_effective_separate() -> None:
    registry = _registry()
    missing_runtime = registry.support_matrix(runtime_availability={"python-module:lxml": False})
    available_runtime = registry.support_matrix(runtime_availability={"python-module:lxml": True})

    def xml_relationships(matrix: dict[str, object]) -> dict[str, object]:
        rows = matrix["rows"]
        assert isinstance(rows, list)
        row = next(
            item
            for item in rows
            if item["format_id"] == "xml" and item["pipeline"] == "rag_helper"
        )
        return row["capabilities"][CapabilityDimension.RELATIONSHIPS.value]

    missing = xml_relationships(missing_runtime)
    available = xml_relationships(available_runtime)
    assert missing == {
        **missing,
        "configured": True,
        "runtime_available": False,
        "verified": True,
        "effective": False,
    }
    assert available["effective"] is True


def test_semantic_translation_claims_relationships_only_for_verified_adapters() -> None:
    matrix = _registry().support_matrix()
    effective_relationships = {
        row["format_id"]
        for row in matrix["rows"]
        if row["pipeline"] == "semantic_translation"
        and row["capabilities"]["relationships"]["effective"] is True
    }

    assert effective_relationships == {"java", "javascript", "python", "typescript"}
    java = _registry().descriptor("java")
    assert java is not None
    assert "regex" in java.parser_strategy.lower()

    for format_id in ("csharp", "repository_map_languages"):
        support = _registry().descriptor(format_id).support_for("semantic_translation")
        assert support.indexed.verified is True
        assert support.symbols.verified is True
        assert support.relationships.configured is False


def test_setup_index_truthfully_claims_bounded_text_indexing_only() -> None:
    registry = _registry()

    for descriptor in registry.descriptors:
        support = descriptor.support_for("setup_index")
        assert support.indexed.verified is True
        assert support.indexed.implementation.value == "text_fallback"
        assert support.symbols.configured is False
        assert support.relationships.configured is False


def test_repository_map_runtime_requirements_do_not_treat_import_as_parser_truth() -> None:
    registry = _registry()
    java = registry.descriptor("java").support_for("repository_map")
    csharp = registry.descriptor("csharp").support_for("repository_map")
    grouped = registry.descriptor("repository_map_languages").support_for("repository_map")

    assert java.symbols.runtime_requirements == ("tree-sitter-language:java",)
    assert java.symbols.verified is True
    assert csharp.symbols.runtime_requirements == ("tree-sitter-language:c_sharp",)
    assert csharp.symbols.verified is False
    assert grouped.symbols.verified is False
    runtime_requirements = {
        requirement
        for descriptor in registry.descriptors
        for pipeline in registry.pipelines
        for dimension in CapabilityDimension
        for requirement in descriptor.support_for(pipeline).capability(dimension).runtime_requirements
    }
    assert "python-module:tree_sitter_languages" not in runtime_requirements
    assert all(
        not requirement.startswith("python-module:tree_sitter_")
        for requirement in runtime_requirements
    )


def test_new_rag_helper_formats_distinguish_symbols_from_relationships() -> None:
    registry = _registry()

    for format_id in ("mermaid", "plantuml", "graphviz_dot", "drawio", "json_schema"):
        support = registry.descriptor(format_id).support_for("rag_helper")
        assert support.indexed.verified is True
        assert support.symbols.verified is True
        assert support.relationships.verified is True

    tabular = registry.descriptor("csv").support_for("rag_helper")
    assert tabular.indexed.verified is True
    assert tabular.symbols.verified is True
    assert tabular.relationships.configured is False


def test_all_declared_evidence_is_repository_relative_and_exists() -> None:
    registry = _registry()
    evidence = {
        ref
        for descriptor in registry.descriptors
        for pipeline in registry.pipelines
        for dimension in CapabilityDimension
        for ref in descriptor.support_for(pipeline).capability(dimension).evidence
    }

    assert evidence
    assert all(not Path(ref).is_absolute() and ".." not in Path(ref).parts for ref in evidence)
    assert all((ROOT / ref).is_file() for ref in evidence)


def test_registry_covers_requested_extensions_and_special_filenames() -> None:
    registry = _registry()
    extensions = {
        extension
        for descriptor in registry.descriptors
        for extension in descriptor.selectors.extensions
    }
    requested = {
        ".html", ".scss", ".css", ".sass", ".less", ".md", ".mdx", ".rst",
        ".yaml", ".yml", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".toml", ".xml",
        ".sql", ".vue", ".svelte", ".kt", ".kts", ".swift", ".scala", ".lua",
        ".dart", ".proto", ".graphql", ".gql", ".tf", ".tfvars", ".ini", ".cfg",
        ".conf", ".properties", ".csv", ".tsv", ".ipynb", ".adoc", ".drawio",
        ".mmd", ".mermaid", ".puml", ".plantuml", ".dot", ".gv",
    }
    exact_names = {
        name
        for descriptor in registry.descriptors
        for name in descriptor.selectors.exact_filenames
    }

    assert requested <= extensions
    assert {"Containerfile", "Dockerfile", "Makefile", "Jenkinsfile"} <= exact_names


@pytest.mark.parametrize(
    ("path", "format_id"),
    [
        ("scripts/install.fish", "shell"),
        ("data/export.tsv", "csv"),
        ("docs/system.mermaid", "mermaid"),
        ("docs/system.puml", "plantuml"),
        ("docs/system.gv", "graphviz_dot"),
        ("docs/system.drawio", "drawio"),
        ("schemas/user.schema.json", "json_schema"),
        ("containers/Containerfile.dev", "dockerfile"),
        ("ci/Jenkinsfile.release", "jenkinsfile"),
    ],
)
def test_new_structured_selectors_are_classified_deterministically(
    path: str,
    format_id: str,
) -> None:
    result = FileTypeClassifier(_registry()).classify(path, is_text=True)

    assert result is not None
    assert result.format_id == format_id


def test_registry_export_is_deterministic_and_copy_safe() -> None:
    registry = _registry()
    first = registry.as_dict()
    first["formats"] = []

    assert registry.as_dict()["formats"]
    assert registry.digest == _registry().digest
    assert registry.support_matrix() == _registry().support_matrix()


def test_disabled_descriptor_is_not_classified() -> None:
    disabled = _format("disabled", exact=["Disabledfile"])
    disabled["enabled"] = False
    registry = FileTypeSupportRegistry.from_mapping(_mapping(disabled))

    assert FileTypeClassifier(registry).classify("Disabledfile", is_text=True) is None


def test_programmatic_registry_rejects_unknown_pipeline() -> None:
    raw = _registry().as_dict()
    descriptor = copy.deepcopy(raw["formats"][0])
    descriptor["format_id"] = "bad_pipeline"
    descriptor["selectors"]["extensions"] = [".bad"]
    descriptor["selectors"]["compound_suffixes"] = []
    first_support = descriptor["pipeline_support"][next(iter(descriptor["pipeline_support"]))]
    descriptor["pipeline_support"] = {"ghost": first_support}
    raw["formats"] = [descriptor]

    with pytest.raises(FileTypeSupportContractError, match="unknown pipelines"):
        FileTypeSupportRegistry.from_mapping(raw)


def test_programmatic_registry_rejects_path_traversal_evidence() -> None:
    raw = _registry().as_dict()
    descriptor = copy.deepcopy(raw["formats"][0])
    descriptor["format_id"] = "bad_evidence"
    descriptor["selectors"]["extensions"] = [".bad"]
    descriptor["selectors"]["compound_suffixes"] = []
    for support in descriptor["pipeline_support"].values():
        for capability in support.values():
            if capability["implementation"] != "unsupported":
                capability["evidence"] = ["tests/../outside.py"]
                raw["formats"] = [descriptor]
                with pytest.raises(FileTypeSupportContractError, match="repository-relative"):
                    FileTypeSupportRegistry.from_mapping(raw)
                return
    raise AssertionError("fixture must contain configured support")
