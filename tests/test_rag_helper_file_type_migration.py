from __future__ import annotations

from pathlib import Path

from agent.services.rag_helper_file_type_migration import (
    plan_rag_helper_cache_migration,
)
from agent.services.rag_helper_file_type_policy import RagHelperFileTypePolicy
from ananta_contracts import FileTypeRolloutPolicy, load_file_type_support_registry

ROOT = Path(__file__).resolve().parents[1]


def _effective_extension(path: Path) -> str:
    name = path.name.lower()
    if "dockerfile" in name or "containerfile" in name:
        return "dockerfile"
    if name in {"makefile", "gnumakefile"}:
        return "makefile"
    if name.startswith("jenkinsfile"):
        return "jenkinsfile"
    return path.suffix.lower().lstrip(".")


def _policy() -> RagHelperFileTypePolicy:
    registry = load_file_type_support_registry(ROOT)
    return RagHelperFileTypePolicy(
        registry=registry,
        rollout=FileTypeRolloutPolicy.build(
            registry,
            priorities=("P0", "P1", "P2"),
        ),
        runtime_dispatch_keys={
            "adoc", "bash", "cfg", "conf", "cs", "css", "csv", "dockerfile",
            "dot", "drawio", "fish", "gql", "gradle", "graphql", "gv", "html",
            "ini", "ipynb", "java", "jenkinsfile", "json", "kts", "less",
            "makefile", "md", "mdx", "mermaid", "mk", "mmd", "plantuml",
            "properties", "proto", "ps1", "psm1", "puml", "py", "rst", "sass",
            "scss", "sh", "sql", "tf", "tfvars", "toml", "ts", "tsv", "tsx",
            "xml", "xsd", "yaml", "yml", "zsh",
        },
        dispatch_key_resolver=_effective_extension,
    )


def test_migration_targets_only_changed_format_paths(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Docs\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("def run(): pass\n", encoding="utf-8")
    policy = _policy()
    current = {
        **policy.as_dict(),
        "effective_format_ids": sorted(policy.effective_format_ids(policy.dispatch_keys())),
    }
    previous = {**current, "descriptor_hashes": dict(current["descriptor_hashes"])}
    previous["descriptor_hashes"]["markdown"] = "previous-markdown-parser"

    plan = plan_rag_helper_cache_migration(
        previous_contract=previous,
        current_contract=current,
        previous_manifest={
            "files": [
                {"file": "README.md", "detected_type": "markdown"},
                {"file": "app.py", "detected_type": "python"},
            ]
        },
        repository_path=tmp_path,
        policy=policy,
    )

    assert plan.affected_format_ids == ("markdown",)
    assert plan.affected_paths == ("README.md",)
    assert plan.full_invalidation_fallback is False


def test_missing_previous_contract_fails_safe_to_manifest_wide_invalidation(
    tmp_path: Path,
) -> None:
    plan = plan_rag_helper_cache_migration(
        previous_contract=None,
        current_contract=_policy().as_dict(),
        previous_manifest={"files": [{"file": "README.md", "detected_type": "markdown"}]},
        repository_path=tmp_path,
        policy=_policy(),
    )

    assert plan.affected_paths == ("README.md",)
    assert plan.full_invalidation_fallback is True
