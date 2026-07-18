from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from ananta_contracts.file_type_coverage import FileTypeCoverageReport
from ananta_contracts.file_type_support import load_file_type_support_registry


def test_snapshot_manifest_validates_against_wire_schema() -> None:
    root = Path(__file__).resolve().parents[1]
    registry = load_file_type_support_registry(root)
    report = FileTypeCoverageReport(registry, pipeline="setup_index")
    report.add(
        path="AGENTS.md",
        descriptor=registry.descriptor("markdown"),
        outcome="indexed",
        byte_size=10,
        content_sha256="a" * 64,
        extractor_id="setup_index.plain_text",
        extractor_version="1",
    )
    manifest = report.snapshot_manifest(
        required_path_rules=[{"pattern": "AGENTS.md", "minimum_indexed": 1}],
        profile={"profile_id": "test"},
        source_revision="f" * 40,
    )
    schema = json.loads(
        (root / "schemas" / "worker" / "codecompass_snapshot_manifest.v1.json").read_text(encoding="utf-8")
    )

    assert list(Draft202012Validator(schema).iter_errors(manifest)) == []
