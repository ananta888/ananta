from __future__ import annotations

from pathlib import Path

from rag_helper.application.file_scanner import build_file_snapshots, collect_files
from rag_helper.filesystem.file_filters import effective_extension


def test_effective_extension_classifies_exact_build_file_names_and_variants() -> None:
    assert effective_extension(Path("Dockerfile")) == "dockerfile"
    assert effective_extension(Path("Containerfile")) == "dockerfile"
    assert effective_extension(Path("Dockerfile.dev")) == "dockerfile"
    assert effective_extension(Path("Containerfile.prod")) == "dockerfile"
    assert effective_extension(Path("api.Dockerfile")) == "dockerfile"
    assert effective_extension(Path("worker.Containerfile")) == "dockerfile"
    assert effective_extension(Path("Makefile")) == "makefile"
    assert effective_extension(Path("GNUmakefile")) == "makefile"
    assert effective_extension(Path("Jenkinsfile")) == "jenkinsfile"
    assert effective_extension(Path("Jenkinsfile.release")) == "jenkinsfile"
    assert effective_extension(Path("rules.mk")) == "mk"


def test_scanner_collects_exact_names_and_builds_dispatchable_snapshots(tmp_path: Path) -> None:
    for name in ("Dockerfile", "Containerfile", "Makefile", "Jenkinsfile"):
        (tmp_path / name).write_text("# inert\n", encoding="utf-8")
    files = collect_files(
        root=tmp_path,
        extensions={"dockerfile", "makefile", "jenkinsfile"},
        excludes=set(),
    )
    snapshots = build_file_snapshots(files, tmp_path)
    assert {item.rel_path for item in snapshots} == {"Dockerfile", "Containerfile", "Makefile", "Jenkinsfile"}
    assert {item.rel_path: item.ext for item in snapshots} == {
        "Dockerfile": "dockerfile",
        "Containerfile": "dockerfile",
        "Makefile": "makefile",
        "Jenkinsfile": "jenkinsfile",
    }


def test_scanner_never_follows_or_indexes_symlink_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    actual = source / "actual.yaml"
    actual.write_text("safe: true\n", encoding="utf-8")
    linked = tmp_path / "linked.yaml"
    linked.symlink_to(actual)

    files = collect_files(root=tmp_path, extensions={"yaml"}, excludes=set())

    assert actual in files
    assert linked not in files


def test_scanner_includes_agents_readme_and_docs_markdown_for_retrieval_coverage(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    expected = {
        tmp_path / "AGENTS.md",
        tmp_path / "README.md",
        tmp_path / "docs/architecture.md",
    }
    for path in expected:
        path.write_text("# Grounded documentation\n", encoding="utf-8")

    files = collect_files(root=tmp_path, extensions={"md"}, excludes=set())
    snapshots = build_file_snapshots(files, tmp_path)

    assert set(files) == expected
    assert {snapshot.ext for snapshot in snapshots} == {"md"}


def test_extensionless_shebang_is_classified_without_executing_the_script(tmp_path: Path) -> None:
    script = tmp_path / "deploy"
    script.write_text("#!/usr/bin/env bash\nfunction deploy { :; }\n", encoding="utf-8")

    files = collect_files(root=tmp_path, extensions={"sh"}, excludes=set())
    snapshots = build_file_snapshots(files, tmp_path)

    assert effective_extension(script) == "sh"
    assert files == [script]
    assert snapshots[0].ext == "sh"
