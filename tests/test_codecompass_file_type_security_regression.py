from __future__ import annotations

from pathlib import Path

import scripts.setup_codecompass_index as setup_index
from ananta_contracts.file_type_support import load_file_type_support_registry

ROOT = Path(__file__).resolve().parents[1]


def test_setup_index_blocks_binary_payloads_even_for_registered_text_suffixes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    registry = load_file_type_support_registry(ROOT)
    (tmp_path / "README.md").write_bytes(b"\x89PNG\x00BINARY_MARKDOWN_MUST_NOT_LEAK")
    (tmp_path / "Dockerfile").write_bytes(b"\x7fELF\x00BINARY_DOCKERFILE_MUST_NOT_LEAK")
    monkeypatch.setattr(setup_index, "ROOT", tmp_path)
    monkeypatch.setattr(setup_index, "_load_registry", lambda: registry)
    monkeypatch.setattr(
        setup_index,
        "_repository_paths",
        lambda: ["README.md", "Dockerfile"],
    )
    monkeypatch.setattr(setup_index, "_runtime_availability", lambda value: {})

    plan = setup_index._collect_index_plan(max_records=10)
    records, coverage = setup_index._build_records_from_plan(plan)
    by_path = {entry["path"]: entry for entry in coverage.as_dict()["files"]}

    assert plan.selected == []
    assert records == [setup_index._GLOSSARY_RECORD]
    assert set(by_path) == {"README.md", "Dockerfile"}
    assert all(entry["detected_type"] == "unclassified_binary" for entry in by_path.values())
    assert all(entry["outcome"] == "unsupported" for entry in by_path.values())
    assert all(entry["diagnostics"] == ["binary_content_blocked"] for entry in by_path.values())
