"""Deterministic fixture construction for the CodeCompass E2E gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


def canonical_bytes(value: Any) -> bytes:
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (rendered + "\n").encode("utf-8")


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value).rstrip(b"\n")).hexdigest()


def write_fixture(repository: Path) -> dict[str, str]:
    files = {
        "docs/architecture.md": (
            "# Architecture Overview\n\nArchitectureOverview documents the Hub-owned RuntimeCoordinator.\n"
        ),
        "schemas/widget.schema.json": json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "urn:ananta:fixture:widget",
                "title": "WidgetSchema",
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        "src/runtime.py": (
            "class RuntimeCoordinator:\n"
            '    """Coordinates one Hub-owned fixture run."""\n'
            "\n"
            "    def execute(self) -> str:\n"
            "        return 'completed'\n"
        ),
        "tests/test_runtime.py": (
            "def test_runtime_contract():\n    assert 'RuntimeCoordinator'.startswith('Runtime')\n"
        ),
    }
    for relative_path, content in files.items():
        path = repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return files


def repository_revision(files: Mapping[str, str]) -> str:
    projection = [
        {
            "path": path,
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        for path, content in sorted(files.items())
    ]
    return stable_hash(projection)
