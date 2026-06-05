#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from collections import defaultdict


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

    repo_root = pathlib.Path.cwd().name
    tree_line = re.compile(r"^(?P<indent>\s*)<(?P<kind>Dir|Package|Module) (?P<name>[^>]+)>$")
    stack: list[tuple[int, str]] = []
    file_counts: dict[str, int] = defaultdict(int)

    for raw_line in collect.stdout.splitlines():
        match = tree_line.match(raw_line.rstrip())
        if not match:
            continue

        indent = len(match.group("indent"))
        kind = match.group("kind")
        name = match.group("name")

        while stack and stack[-1][0] >= indent:
            stack.pop()

        if kind in {"Dir", "Package"}:
            stack.append((indent, name))
            continue

        parts = [part for _, part in stack]
        if parts and parts[0] == repo_root:
            parts = parts[1:]
        module_path = "/".join(parts + [name])
        if re.fullmatch(r"tests/.+\.py", module_path):
            file_counts[module_path] += 1

    return dict(file_counts)


def build_shards(file_counts: dict[str, int], shard_count: int) -> list[dict[str, object]]:
    files = sorted(file_counts.items(), key=lambda item: (-item[1], item[0]))
    bins = [{"files": [], "count": 0} for _ in range(shard_count)]
    for file_path, count in files:
        target = min(range(shard_count), key=lambda idx: bins[idx]["count"])
        bins[target]["files"].append(file_path)
        bins[target]["count"] += count

    shard_entries: list[dict[str, object]] = []
    for idx, shard in enumerate(bins):
        shard_entries.append(
            {
                "shard_index": idx,
                "shard_count": shard_count,
                "file_count": len(shard["files"]),
                "test_count": shard["count"],
                "files": shard["files"],
            }
        )
    return shard_entries


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve backend coverage shards from pytest collection")
    parser.add_argument("--shard-count", type=int, default=4)
    args = parser.parse_args()

    if args.shard_count < 1:
        raise SystemExit("--shard-count must be >= 1")

    file_counts = collect_files()
    shards = build_shards(file_counts, args.shard_count)
    total_tests = sum(file_counts.values())
    total_files = len(file_counts)

    print(f"Resolved {total_files} files and {total_tests} collected tests into {args.shard_count} shards", file=sys.stderr)
    payload = {"include": shards}
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
