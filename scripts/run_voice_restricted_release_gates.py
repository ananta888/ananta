#!/usr/bin/env python3
"""Run the versioned Voice/Restricted core or explicit hardware release gate."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GATE = ROOT / "config" / "release-gates" / "voice-restricted-core.v1.json"


def load_gate(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("schema_version") != "ananta.release-gate.v1":
        raise ValueError("unsupported release gate contract")
    groups = payload.get("groups")
    if not isinstance(groups, Mapping) or not groups:
        raise ValueError("release gate has no groups")
    for group_id, group in groups.items():
        if not isinstance(group, Mapping) or not isinstance(group.get("hardware"), bool):
            raise ValueError(f"release gate group {group_id} is invalid")
        nodes = group.get("pytest_nodes")
        if not isinstance(nodes, list) or not nodes or not all(isinstance(item, str) and item for item in nodes):
            raise ValueError(f"release gate group {group_id} has no pytest nodes")
        for command in group.get("commands") or ():
            if not isinstance(command, Mapping):
                raise ValueError(f"release gate group {group_id} has an invalid command")
            argv = command.get("argv")
            cwd = Path(str(command.get("cwd") or "."))
            if (
                not isinstance(argv, list)
                or not argv
                or not all(isinstance(item, str) and item for item in argv)
                or cwd.is_absolute()
                or ".." in cwd.parts
            ):
                raise ValueError(f"release gate group {group_id} has an unsafe command")
    return payload


def select_groups(gate: Mapping[str, Any], selection: str) -> tuple[str, ...]:
    groups = gate["groups"]
    if selection == "core":
        return tuple(
            name
            for name, group in groups.items()
            if group["hardware"] is False and group.get("core", True) is True
        )
    if selection not in groups:
        raise ValueError(f"unknown release gate group: {selection}")
    return (selection,)


def select_nodes(gate: Mapping[str, Any], groups: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    nodes: list[str] = []
    for group_id in groups:
        for node in gate["groups"][group_id]["pytest_nodes"]:
            if node not in seen:
                seen.add(node)
                nodes.append(node)
    return tuple(nodes)


def select_commands(gate: Mapping[str, Any], groups: Sequence[str]) -> tuple[tuple[Path, tuple[str, ...]], ...]:
    commands: list[tuple[Path, tuple[str, ...]]] = []
    for group_id in groups:
        for command in gate["groups"][group_id].get("commands") or ():
            commands.append((ROOT / str(command.get("cwd") or "."), tuple(command["argv"])))
    return tuple(commands)


def require_hardware_environment(gate: Mapping[str, Any], groups: Sequence[str]) -> None:
    missing: list[str] = []
    for group_id in groups:
        group = gate["groups"][group_id]
        if group["hardware"] is not True:
            continue
        for variable in group.get("required_environment") or ():
            value = str(os.getenv(str(variable), "")).strip()
            if not value or (variable == "ANANTA_RUN_VOICE_RESTRICTED_HARDWARE" and value != "1"):
                missing.append(str(variable))
    if missing:
        raise ValueError("hardware gate requires explicit environment: " + ", ".join(sorted(set(missing))))


def _junit_counts(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return {
        field: sum(int(suite.attrib.get(field, "0")) for suite in suites)
        for field in ("tests", "failures", "errors", "skipped")
    }


def _git_evidence() -> tuple[str, bool]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    return revision, dirty


def _write_evidence(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", type=Path, default=DEFAULT_GATE)
    parser.add_argument("--group", default="core", help="core or one configured group ID")
    parser.add_argument("--evidence", type=Path, help="optional machine-readable result path")
    arguments = parser.parse_args(argv)
    try:
        gate = load_gate(arguments.gate)
        groups = select_groups(gate, arguments.group)
        require_hardware_environment(gate, groups)
        nodes = select_nodes(gate, groups)
        commands = select_commands(gate, groups)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    command_results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ananta-voice-release-") as temporary:
        temporary_path = Path(temporary)
        junit = temporary_path / "pytest.xml"
        test_environment = os.environ.copy()
        if any(
            gate["groups"][group_id].get("integration") is True
            or gate["groups"][group_id]["hardware"] is True
            for group_id in groups
        ):
            test_environment["RUN_INTEGRATION_TESTS"] = "1"
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", f"--junitxml={junit}", *nodes],
            cwd=ROOT,
            check=False,
            text=True,
            env=test_environment,
        )
        counts = _junit_counts(junit) if junit.exists() else {"tests": 0, "failures": 0, "errors": 1, "skipped": 0}
        for cwd, argv_items in commands:
            resolved_argv = tuple(item.replace("{gate_tmp}", str(temporary_path)) for item in argv_items)
            command = subprocess.run(list(resolved_argv), cwd=cwd, check=False, text=True)
            command_results.append(
                {
                    "argv": list(argv_items),
                    "cwd": str(cwd.relative_to(ROOT)),
                    "returncode": command.returncode,
                }
            )

    # Core evidence is deliberately strict: a dependency or hardware skip is a
    # missing proof, not a pass. Hardware groups are explicit and follow the
    # same rule once enabled.
    passed = (
        completed.returncode == 0
        and counts["tests"] > 0
        and counts["skipped"] == 0
        and all(item["returncode"] == 0 for item in command_results)
    )
    revision, dirty = _git_evidence()
    evidence = {
        "schema_version": "ananta.release-gate-result.v1",
        "gate_id": gate["gate_id"],
        "groups": list(groups),
        "git_sha": revision,
        "worktree_dirty": dirty,
        "pytest": counts,
        "commands": command_results,
        "status": "passed" if passed else "failed",
    }
    print(json.dumps(evidence, sort_keys=True))
    if arguments.evidence:
        _write_evidence(arguments.evidence, evidence)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
