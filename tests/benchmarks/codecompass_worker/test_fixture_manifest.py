from __future__ import annotations

import json
from pathlib import Path


def test_codecompass_worker_fixture_manifest_is_complete_and_deterministic():
    fixture_root = Path(__file__).resolve().parent / "fixtures" / "java_spring_xml"
    manifest = json.loads((fixture_root / "fixture_manifest.json").read_text(encoding="utf-8"))

    assert manifest["schema"] == "codecompass_worker_fixture_manifest.v1"
    assert len(manifest["queries"]) >= 4
    query_ids = {item["id"] for item in manifest["queries"]}
    assert {"bugfix-timeout-retry", "refactor-repository-dependency", "architecture-transaction-boundary", "config-timeout-property"} <= query_ids

    for rel in manifest["source_files"]:
        assert (fixture_root / rel).exists()

    for _, rel_path in dict(manifest["generated_outputs"]).items():
        path = fixture_root / rel_path
        assert path.exists()
        rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert rows
        for row in rows:
            parsed = json.loads(row)
            assert isinstance(parsed, dict)

