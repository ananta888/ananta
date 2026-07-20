from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.e2e.semantic_relay_multi_hub_e2e import (
    DEFAULT_OUTPUT,
    FORBIDDEN_REPORT_KEYS,
    _assert_content_free,
    _source_hash,
)

ROOT = Path(__file__).resolve().parents[1]


def test_live_multi_hub_report_is_green_current_and_content_free() -> None:
    report = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["source_sha256"] == _source_hash()
    assert report["checks"] and all(report["checks"].values())
    assert report["runtime"]["hub_processes"] == 2
    assert report["runtime"]["abort_signal"] == "SIGKILL"
    _assert_content_free(report)


def test_multi_hub_report_has_no_sensitive_or_content_field_names() -> None:
    report = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))

    def keys(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                yield str(key).lower()
                yield from keys(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from keys(nested)

    assert not (set(keys(report)) & FORBIDDEN_REPORT_KEYS)
    serialized = json.dumps(report).lower()
    assert "127.0.0.1" not in serialized
    assert "/home/" not in serialized


def test_multi_hub_gate_is_directly_executable_without_custom_pythonpath() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/e2e/semantic_relay_multi_hub_e2e.py", "--verify"],
        cwd=ROOT,
        env={"PATH": os.environ["PATH"]},
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
