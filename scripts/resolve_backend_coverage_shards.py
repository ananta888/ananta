#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from collections import defaultdict

_NODE_ID = re.compile(r"^(?P<path>tests/.+?\.py)::")
_TREE_NODE = re.compile(r"^(?P<indent>\s*)<(?P<kind>[^ ]+) (?P<name>[^>]+)>$")
_TEST_ITEM_KINDS = frozenset({"Function", "TestCaseFunction", "DoctestItem"})


def parse_collection_output(output: str, *, repo_root: str) -> dict[str, int]:
    """Return per-file test counts for pytest node-id and verbose tree output."""
    node_id_counts: dict[str, int] = defaultdict(int)
    for raw_line in output.splitlines():
        match = _NODE_ID.match(raw_line.strip())
        if match:
            node_id_counts[match.group("path")] += 1
    if node_id_counts:
        return dict(node_id_counts)

    file_counts: dict[str, int] = defaultdict(int)
    stack: list[tuple[int, str, str]] = []
    for raw_line in output.splitlines():
        match = _TREE_NODE.match(raw_line.rstrip())
        if not match:
            continue

        indent = len(match.group("indent"))
        kind = match.group("kind")
        name = match.group("name")
        while stack and stack[-1][0] >= indent:
            stack.pop()

        if kind in _TEST_ITEM_KINDS:
            module_index = next(
                (index for index in range(len(stack) - 1, -1, -1) if stack[index][1] == "Module"),
                None,
            )
            if module_index is not None:
                path_parts = [
                    node_name
                    for _, node_kind, node_name in stack[: module_index + 1]
                    if node_kind in {"Dir", "Package", "Module"}
                ]
                if path_parts and path_parts[0] == repo_root:
                    path_parts = path_parts[1:]
                module_path = "/".join(path_parts)
                if re.fullmatch(r"tests/.+\.py", module_path):
                    file_counts[module_path] += 1

        stack.append((indent, kind, name))

    return dict(file_counts)


def collect_files() -> dict[str, int]:
    collect = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests",
            "--collect-only",
            "-q",
            "--ignore-glob=tests/**/fixtures/**",
            "--ignore=tests/test_evolver_live_compose.py",
        ],
        capture_output=True,
        text=True,
    )
    if collect.returncode != 0:
        sys.stderr.write(collect.stdout)
        sys.stderr.write(collect.stderr)
        raise SystemExit(collect.returncode)

    file_counts = parse_collection_output(collect.stdout, repo_root=pathlib.Path.cwd().name)
    if not file_counts:
        raise SystemExit("Pytest collection returned no countable test items.")
    return file_counts


def classify_file(file_path: str) -> str:
    parts = pathlib.PurePosixPath(file_path).parts
    filename = parts[-1]

    if len(parts) > 1 and parts[1] == "e2e":
        return "e2e"
    if len(parts) > 1 and parts[1] == "worker":
        return "worker"
    if len(parts) > 1 and parts[1] == "llm_interceptor":
        return "llm-interceptor"
    if len(parts) > 1 and parts[1] == "heuristic_runtime":
        return "heuristic-runtime"
    if len(parts) > 1 and parts[1] == "smoke":
        return "smoke-cli"
    if len(parts) > 1 and parts[1] == "cli":
        return "cli"
    if len(parts) > 1 and parts[1] == "benchmarks":
        return "benchmarks"
    if len(parts) > 1 and parts[1] == "client_surfaces" and len(parts) > 2 and parts[2] == "operator_tui":
        return "operator-tui"
    if len(parts) > 1 and parts[1] == "client_surfaces":
        return "client-surfaces"

    stem = pathlib.PurePosixPath(filename).stem.lower()
    if "client_surface" in stem or "surface" in stem:
        return "client-surfaces"
    if "worker" in stem:
        return "worker"
    if "llm" in stem or "model" in stem:
        return "llm-interceptor"
    if "heuristic" in stem:
        return "heuristic-runtime"
    if "tui" in stem or "terminal" in stem or "snake" in stem:
        return "operator-tui"
    if "cli" in stem or "smoke" in stem:
        return "smoke-cli"
    if "benchmark" in stem or "retrieval" in stem:
        return "benchmarks"
    if "e2e" in stem or "flow" in stem or "live" in stem or "golden" in stem or "acceptance" in stem:
        return "e2e"
    if any(keyword in stem for keyword in ("planning", "autopilot", "goal", "strategy", "workflow")):
        return "core-contracts"
    return "core-contracts"


def split_group(
    files: list[tuple[str, int]],
    shard_prefix: str,
    shard_count: int,
    start_index: int,
) -> list[dict[str, object]]:
    if shard_count < 1:
        return []

    bins = [{"files": [], "count": 0} for _ in range(shard_count)]
    for file_path, count in sorted(files, key=lambda item: (-item[1], item[0])):
        target = min(range(shard_count), key=lambda idx: bins[idx]["count"])
        bins[target]["files"].append(file_path)
        bins[target]["count"] += count

    shard_entries: list[dict[str, object]] = []
    for offset, shard in enumerate(bins, start=1):
        shard_entries.append(
            {
                "shard_index": start_index + offset - 1,
                "shard_count": None,
                "shard_name": f"{shard_prefix}-{offset:02d}",
                "file_count": len(shard["files"]),
                "test_count": shard["count"],
                "files": shard["files"],
            }
        )
    return shard_entries


def build_shards(file_counts: dict[str, int], shard_count: int) -> list[dict[str, object]]:
    if shard_count != 18:
        raise SystemExit("This resolver currently expects --shard-count 18.")

    categorized: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for file_path, count in file_counts.items():
        categorized[classify_file(file_path)].append((file_path, count))

    shard_entries: list[dict[str, object]] = []
    shard_entries.extend(split_group(categorized.pop("core-contracts", []), "core-contracts", 8, 0))
    shard_entries.extend(split_group(categorized.pop("e2e", []), "e2e", 2, 8))
    shard_entries.extend(split_group(categorized.pop("client-surfaces", []), "client-surfaces", 1, 10))
    shard_entries.extend(split_group(categorized.pop("operator-tui", []), "operator-tui", 1, 11))
    shard_entries.extend(split_group(categorized.pop("worker", []), "worker", 1, 12))
    shard_entries.extend(split_group(categorized.pop("llm-interceptor", []), "llm-interceptor", 1, 13))
    shard_entries.extend(split_group(categorized.pop("heuristic-runtime", []), "heuristic-runtime", 1, 14))
    shard_entries.extend(split_group(categorized.pop("smoke-cli", []), "smoke-cli", 1, 15))
    shard_entries.extend(split_group(categorized.pop("cli", []), "cli", 1, 16))
    shard_entries.extend(split_group(categorized.pop("benchmarks", []), "benchmarks", 1, 17))

    if categorized:
        remaining = ", ".join(sorted(categorized))
        raise SystemExit(f"Unexpected uncategorized files: {remaining}")

    return shard_entries


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve backend coverage shards from pytest collection")
    parser.add_argument("--shard-count", type=int, default=14)
    args = parser.parse_args()

    if args.shard_count < 1:
        raise SystemExit("--shard-count must be >= 1")

    file_counts = collect_files()
    shards = build_shards(file_counts, args.shard_count)
    total_tests = sum(file_counts.values())
    total_files = len(file_counts)

    print(
        f"Resolved {total_files} files and {total_tests} collected tests into {args.shard_count} shards",
        file=sys.stderr,
    )
    payload = {"include": shards}
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
