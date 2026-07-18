from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.config import settings
from agent.services.rag_helper_file_type_policy import (
    RagHelperFileTypePolicy,
    RagHelperFileTypePolicyError,
)
from agent.services.rag_helper_index_service import RagHelperIndexService
from ananta_contracts import FileTypeRolloutPolicy, load_file_type_support_registry

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_KEYS = {
    "adoc",
    "bash",
    "cfg",
    "conf",
    "cs",
    "css",
    "csv",
    "dockerfile",
    "dot",
    "drawio",
    "fish",
    "gql",
    "gradle",
    "graphql",
    "gv",
    "html",
    "ini",
    "ipynb",
    "java",
    "jenkinsfile",
    "json",
    "kts",
    "less",
    "makefile",
    "md",
    "mdx",
    "mermaid",
    "mk",
    "mmd",
    "plantuml",
    "properties",
    "proto",
    "ps1",
    "psm1",
    "puml",
    "py",
    "rst",
    "sass",
    "scss",
    "sh",
    "sql",
    "tf",
    "tfvars",
    "toml",
    "ts",
    "tsv",
    "tsx",
    "xml",
    "xsd",
    "yaml",
    "yml",
    "zsh",
}


def _effective_extension(path: Path) -> str:
    name = path.name.lower()
    if name.startswith(("dockerfile", "containerfile")) or name.endswith(
        (".dockerfile", ".containerfile")
    ):
        return "dockerfile"
    if name in {"makefile", "gnumakefile"}:
        return "makefile"
    if name.startswith("jenkinsfile"):
        return "jenkinsfile"
    return path.suffix.lower().lstrip(".")


def _policy(
    *,
    enabled: tuple[str, ...] = (),
    disabled: tuple[str, ...] = (),
    runtime_keys: set[str] = RUNTIME_KEYS,
) -> RagHelperFileTypePolicy:
    registry = load_file_type_support_registry(ROOT)
    rollout = FileTypeRolloutPolicy.build(
        registry,
        priorities=("P0", "P1", "P2"),
        enabled_format_ids=enabled,
        disabled_format_ids=disabled,
    )
    return RagHelperFileTypePolicy(
        registry=registry,
        rollout=rollout,
        runtime_dispatch_keys=runtime_keys,
        dispatch_key_resolver=_effective_extension,
    )


def test_dispatch_keys_are_derived_from_verified_rag_helper_capabilities() -> None:
    keys = _policy().dispatch_keys()

    assert {"html", "scss", "dockerfile", "yaml", "proto", "drawio"} <= keys
    assert {"js", "jsx", "go", "rs", "swift", "lua"}.isdisjoint(keys)


def test_path_policy_distinguishes_formats_sharing_yaml_extensions(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("jobs: {}\n", encoding="utf-8")
    config = tmp_path / "config.yml"
    config.write_text("feature: true\n", encoding="utf-8")
    policy = _policy(disabled=("github_actions",))

    assert policy.allows_file(workflow, ".github/workflows/ci.yml") is False
    assert policy.allows_file(config, "config.yml") is True


def test_explicit_format_allow_list_is_enforced_per_path(tmp_path: Path) -> None:
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    config = tmp_path / "config.yml"
    config.write_text("feature: true\n", encoding="utf-8")
    policy = _policy(enabled=("docker_compose",))

    assert policy.dispatch_keys() == frozenset({"yaml", "yml"})
    assert policy.allows_file(compose, "compose.yml") is True
    assert policy.allows_file(config, "config.yml") is False


def test_extensionless_shebang_is_bounded_and_never_executed(tmp_path: Path) -> None:
    script = tmp_path / "deploy"
    script.write_text("#!/usr/bin/env bash\necho should-not-run\n", encoding="utf-8")

    classification = _policy().classify_file(script, relative_path="deploy")

    assert classification is not None
    assert classification.format_id == "shell"


def test_missing_runtime_dispatch_fails_closed() -> None:
    with pytest.raises(RagHelperFileTypePolicyError, match="missing_runtime_dispatch"):
        _policy(runtime_keys={"md"}).dispatch_keys()


def test_hub_processing_limits_can_only_narrow_rag_helper_profiles() -> None:
    captured: dict[str, int] = {}

    def processing_limits(**values):
        captured.update(values)
        return SimpleNamespace(**values)

    result = RagHelperIndexService._processing_limits(
        {"ProcessingLimits": processing_limits},
        {
            "limits": {
                "max_parser_lines": 999_999,
                "max_yaml_aliases": 1,
                "max_notebook_cells": 999_999,
            }
        },
    )

    assert result.max_file_size_bytes == settings.codecompass_max_file_bytes
    assert result.max_parser_lines == settings.codecompass_max_lines
    assert result.max_yaml_aliases == 1
    assert result.max_notebook_cells == settings.codecompass_max_notebook_cells
    assert result.max_records_per_file == settings.codecompass_max_output_records
