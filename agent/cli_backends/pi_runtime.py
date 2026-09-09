"""Resolve the pinned Pi SDK beside its verified catalog CLI, without PATH fallback."""

import json
import os
import shutil
from pathlib import Path

from agent.cli_backends.pi_configuration import PI_VERSION


def pi_sdk_command(binary: str, *, path_environment: str = os.defpath) -> tuple[str, ...]:
    if not Path(binary).is_absolute():
        raise ValueError("pi_runtime_layout_unverified")
    executable = Path(binary).resolve(strict=True)
    if executable.name != "cli.js" or executable.parent.name != "bundle" or executable.parent.parent.name != "dist":
        raise ValueError("pi_runtime_layout_unverified")
    package = executable.parents[2]
    sdk = package / "dist" / "index.js"
    if not sdk.is_file() or sdk.resolve().parent != package / "dist":
        raise ValueError("pi_runtime_layout_unverified")
    with (package / "package.json").open(encoding="utf-8") as handle:
        raw = handle.read(65_537)
    if len(raw) > 65_536:
        raise ValueError("pi_runtime_metadata_invalid")
    metadata = json.loads(raw)
    if (
        not isinstance(metadata, dict) or metadata.get("name") != "@earendil-works/pi-coding-agent"
        or metadata.get("version") != PI_VERSION
    ):
        raise ValueError("pi_runtime_metadata_invalid")
    node = shutil.which("node", path=path_environment)
    if not node or not Path(node).is_absolute():
        raise ValueError("node_runtime_unavailable")
    return node, str(Path(__file__).with_name("pi_sdk_entry.mjs")), str(sdk)
