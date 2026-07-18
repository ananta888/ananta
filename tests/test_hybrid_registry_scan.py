from __future__ import annotations

from pathlib import Path

from git import Repo

from agent.hybrid_repository_scan import tracked_registry_files
from ananta_contracts.file_type_support import FileTypeSupportRegistry


def _capability(*, implementation: str = "heuristic") -> dict:
    return {
        "implementation": implementation,
        "verified": True,
        "producer": "tests.registry_scan",
        "evidence": ["tests/test_hybrid_registry_scan.py"],
        "runtime_requirements": [],
    }


def _unsupported() -> dict:
    return {
        "implementation": "unsupported",
        "verified": False,
        "producer": None,
        "evidence": [],
        "runtime_requirements": [],
    }


def _descriptor(format_id: str, extension: str, family: str) -> dict:
    return {
        "format_id": format_id,
        "display_name": format_id,
        "family": family,
        "priority": "P0",
        "enabled": True,
        "selectors": {
            "exact_filenames": [],
            "filename_patterns": [],
            "compound_suffixes": [],
            "extensions": [extension],
            "shebang_patterns": [],
            "text_fallback": False,
        },
        "security_class": "untrusted_text",
        "parser_strategy": "fixture",
        "fallback_strategy": "none",
        "known_limits": [],
        "pipeline_support": {
            "repository_map": {
                "indexed": _capability(),
                "symbols": _unsupported(),
                "relationships": _unsupported(),
            }
        },
    }


def _registry() -> FileTypeSupportRegistry:
    return FileTypeSupportRegistry.from_mapping(
        {
            "schema": "codecompass.file-type-support-registry.v1",
            "registry_version": "test",
            "support_dimensions": ["indexed", "symbols", "relationships"],
            "pipelines": ["repository_map"],
            "formats": [
                _descriptor("python", ".py", "code"),
                _descriptor("markdown", ".md", "documentation"),
                _descriptor("environment", ".env", "configuration"),
            ],
        }
    )


def test_registry_scan_classifies_tracked_and_allowed_untracked_identically(tmp_path: Path):
    (tmp_path / "tracked.py").write_text("def tracked(): pass", encoding="utf-8")
    (tmp_path / "untracked.md").write_text("# Untracked", encoding="utf-8")
    repo = Repo.init(tmp_path)
    repo.index.add(["tracked.py"])

    files = tracked_registry_files(
        repo_root=tmp_path,
        registry=_registry(),
        pipeline="repository_map",
        max_files=10,
    )

    assert {path.name for path in files} == {"tracked.py", "untracked.md"}


def test_registry_scan_excludes_secret_paths_before_reading(tmp_path: Path):
    (tmp_path / "visible.py").write_text("def visible(): pass", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=do-not-read", encoding="utf-8")

    files = tracked_registry_files(
        repo_root=tmp_path,
        registry=_registry(),
        pipeline="repository_map",
        max_files=10,
    )

    assert [path.name for path in files] == ["visible.py"]


def test_registry_scan_rejects_repository_symlinks(tmp_path: Path):
    target = tmp_path / "target.py"
    target.write_text("def target(): pass", encoding="utf-8")
    link = tmp_path / "linked.py"
    link.symlink_to(target)

    files = tracked_registry_files(
        repo_root=tmp_path,
        registry=_registry(),
        pipeline="repository_map",
        max_files=10,
    )

    assert target.resolve() in files
    assert link not in files


def test_registry_scan_balances_families_when_file_limit_is_reached(tmp_path: Path):
    for index in range(8):
        (tmp_path / f"code_{index}.py").write_text(f"def f_{index}(): pass", encoding="utf-8")
    for index in range(2):
        (tmp_path / f"guide_{index}.md").write_text(f"# Guide {index}", encoding="utf-8")

    files = tracked_registry_files(
        repo_root=tmp_path,
        registry=_registry(),
        pipeline="repository_map",
        max_files=4,
    )

    assert sum(path.suffix == ".py" for path in files) == 2
    assert sum(path.suffix == ".md" for path in files) == 2
