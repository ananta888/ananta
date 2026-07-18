from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from agent.visual_process.node_definitions import NODE_REGISTRY_VERSION, list_node_definitions

ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_angular_node_definitions_match_hub_contract() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/generate_visual_process_node_definitions.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    definitions = list_node_definitions()
    assert len(definitions) == 37
    assert {item["registry_version"] for item in definitions} == {NODE_REGISTRY_VERSION}
