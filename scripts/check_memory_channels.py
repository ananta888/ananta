#!/usr/bin/env python3
"""Require explicit inventory entries for statically observable state channels.

This source gate is deliberately conservative. It does not sandbox dynamic I/O,
prove absence of hidden state, or grant runtime capabilities.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = ("agent", "worker", "client_surfaces", "ananta_contracts")
STATE_MODULES = {"sqlite3", "duckdb", "redis", "qdrant_client", "chromadb", "diskcache", "shelve"}


def channel_signals(path: str, source: str) -> tuple[str, ...]:
    """Discover storage families without importing or executing production code."""
    tree = ast.parse(source, filename=path)
    signals = set()
    name = PurePosixPath(path).stem.lower()
    if any(part in name for part in ("memory", "cache", "vector", "scratchpad", "checkpoint")):
        signals.add("named_state_component")
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
            keyword.arg == "table" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True
            for keyword in node.keywords
        ):
            table_names = [statement.value.value for statement in node.body
                           if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant)
                           and isinstance(statement.value.value, str)
                           and any(isinstance(target, ast.Name) and target.id == "__tablename__"
                                   for target in statement.targets)]
            signals.update(f"sql_table:{table}" for table in table_names)
            if not table_names:
                signals.add("sql_table_definition")
        if isinstance(node, ast.Import):
            imported = {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported = {(node.module or "").split(".")[0]}
        else:
            imported = set()
        signals.update(f"storage_import:{module}" for module in imported & STATE_MODULES)
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        method = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        if method in {"write_text", "write_bytes"}:
            signals.add("filesystem_write")
        if method in {"lru_cache", "cache", "TTLCache", "LRUCache"}:
            signals.add("process_cache")
        if method in {"open", "fdopen"}:
            mode = node.args[1] if len(node.args) > 1 else next(
                (kw.value for kw in node.keywords if kw.arg == "mode"), None,
            )
            if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and any(c in mode.value for c in "wax+"):
                signals.add("filesystem_write")
            elif mode is not None and not isinstance(mode, ast.Constant):
                signals.add("dynamic_file_mode")
    return tuple(sorted(signals))


def discover(root: Path) -> dict[str, tuple[str, ...]]:
    result = {}
    for directory in SOURCE_ROOTS:
        for path in sorted((root / directory).rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            signals = channel_signals(relative, path.read_text(encoding="utf-8"))
            if signals:
                result[relative] = signals
    return result


def validate(root: Path, inventory: dict) -> list[str]:
    if not isinstance(inventory, dict) or inventory.get("schema_version") != "ananta.memory-channels.v1":
        return ["memory_channel_register_schema_invalid"]
    channels = inventory.get("channels")
    if not isinstance(channels, list):
        return ["memory_channel_register_channels_invalid"]
    registered = {}
    identifiers = set()
    failures = []
    required = {"id", "owner", "scope", "persistence", "readers", "writers", "deletion", "trust", "files"}
    for channel in channels:
        if not isinstance(channel, dict) or required - channel.keys():
            failures.append("memory_channel_metadata_missing")
            continue
        identifier = channel["id"]
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            failures.append("memory_channel_id_invalid")
        else:
            identifiers.add(identifier)
        for key in required - {"files", "id"}:
            if not isinstance(channel[key], str) or not channel[key].strip():
                failures.append(f"{identifier}:memory_channel_metadata_invalid:{key}")
        if not isinstance(channel["files"], dict) or not channel["files"]:
            failures.append(f"{identifier}:memory_channel_files_invalid")
            continue
        for name, signals in channel["files"].items():
            if not name:
                failures.append("memory_channel_path_invalid")
                continue
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or path.as_posix() != name or path.parts[0] not in SOURCE_ROOTS:
                failures.append(f"{name}:memory_channel_path_invalid")
                continue
            if name in registered:
                failures.append(f"{name}:memory_channel_duplicate")
            if not isinstance(signals, list) or any(not isinstance(s, str) for s in signals):
                failures.append(f"{name}:memory_channel_signals_invalid")
                continue
            registered[name] = set(signals)
    for name, signals in discover(root).items():
        if name not in registered:
            failures.append(f"{name}:memory_channel_unregistered")
        else:
            for signal in sorted(set(signals) - registered[name]):
                failures.append(f"{name}:memory_channel_signal_unregistered:{signal}")
    for name in sorted(registered):
        if not (root / name).is_file():
            failures.append(f"{name}:memory_channel_file_missing")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--register", type=Path, default=None)
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        path = args.register or root / "config/security/memory-channels.v1.json"
        failures = validate(root, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, SyntaxError) as exc:
        print(f"memory_channel_register_unavailable:{type(exc).__name__}")
        return 1
    if failures:
        print("\n".join(failures))
        return 1
    print("memory-channels-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
