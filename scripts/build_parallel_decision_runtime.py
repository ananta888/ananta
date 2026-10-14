#!/usr/bin/env python3
"""Build the pinned fork plus the additive media-cache isolation patch."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVISION = "782daeb1f719b24f4567939c2be386f87a2d5d38"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument("--cuda", action="store_true")
    parser.add_argument("--cuda-arch", choices=["86", "89", "90", "120"], default="120")
    args = parser.parse_args()
    source = ROOT / "vendor/llama.cpp-vision-decision"
    build_root = args.build_root.resolve()
    if build_root.is_relative_to(ROOT) or build_root.exists():
        parser.error("Use a fresh build directory outside the repository")
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != REVISION:
        raise SystemExit("decision_source_revision_mismatch")
    patch = ROOT / "scripts/patches/llamacpp-decision-media-isolation.patch"
    commands = [
        ["git", "clone", "--shared", str(source), str(build_root)],
        ["git", "-C", str(build_root), "checkout", "--detach", REVISION],
        ["git", "-C", str(build_root), "apply", "--check", str(patch)],
        ["git", "-C", str(build_root), "apply", str(patch)],
        [
            "cmake",
            "-S",
            str(build_root),
            "-B",
            str(build_root / "build"),
            "-DCMAKE_BUILD_TYPE=Release",
            "-DLLAMA_CURL=OFF",
            "-DLLAMA_BUILD_TESTS=OFF",
            "-DLLAMA_BUILD_EXAMPLES=OFF",
            "-DGGML_CUDA=" + ("ON" if args.cuda else "OFF"),
            *(["-DCMAKE_CUDA_ARCHITECTURES=" + args.cuda_arch] if args.cuda else []),
        ],
        ["cmake", "--build", str(build_root / "build"), "--target", "llama-server", "-j", "4"],
    ]
    for command in commands:
        subprocess.run(command, check=True, timeout=900)
    manifest = {
        "schema": "ananta.parallel-decision-build.v1",
        "source_revision": REVISION,
        "patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
        "cuda": args.cuda,
        "cuda_arch": args.cuda_arch if args.cuda else None,
        "classification": "technical_build_observation",
        "production_release_evidence": False,
    }
    (build_root / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
