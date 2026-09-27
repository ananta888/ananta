#!/usr/bin/env python3
"""Check that the two decision branches of the llama.cpp fork carry the same decision code.

``vision-decision`` (mainline base, pinned as ``vendor/llama.cpp-vision-decision``)
and ``bonsai-decision`` (PrismML base, the production server) differ in their
llama.cpp base on purpose, but the decision engine, its server tests and the
playground must be identical: every change is cherry-picked to both
(``docs/jev-llamacpp-decision-mode.md``, "Zwei Branches im Fork"). Compares the
two branch tips without checking anything out; exit 1 lists the drifted files.

    python3 scripts/check_decision_fork_branches.py --repo ~/llama.cpp-vision-decision
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

DEFAULT_REPO = Path(__file__).resolve().parents[1] / "vendor/llama.cpp-vision-decision"
BRANCHES = ("vision-decision", "bonsai-decision")
# identical on both lines; the server handler lives in server-context.cpp next to base-specific code
SHARED_PATHS = ("tools/parallel-decision", "tools/server/tests/unit/test_decision.py", "BRANCHES.md")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def drifted_files(repo: Path, left: str, right: str, paths: tuple[str, ...] = SHARED_PATHS) -> list[str]:
    """Files under ``paths`` whose content differs between the two revisions."""
    return [line for line in git(repo, "diff", "--name-only", left, right, "--", *paths).splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--remote", default="origin", help="compare <remote>/<branch>; empty for local branches")
    parser.add_argument("--no-fetch", action="store_true")
    args = parser.parse_args(argv)
    if args.remote and not args.no_fetch:
        git(args.repo, "fetch", "-q", args.remote, *BRANCHES)
    left, right = (f"{args.remote}/{name}" if args.remote else name for name in BRANCHES)
    drift = drifted_files(args.repo, left, right)
    if drift:
        print(f"decision code differs between {left} and {right}:")
        print("\n".join(f"  {path}" for path in drift))
        return 1
    print(f"decision code identical on {left} and {right}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
