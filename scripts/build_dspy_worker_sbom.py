#!/usr/bin/env python3
"""Build a deterministic SBOM projection from the hash-locked DSPy worker inputs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "docker/compose-next/requirements.dspy-optimization.lock"
DOCKERFILE = ROOT / "docker/compose-next/Dockerfile.dspy-optimization-worker"
LICENSES = ROOT / "config/licenses/dspy-optimization.v1.json"
OUTPUT = ROOT / "artifacts/domain/dspy-worker-sbom.json"


def build() -> dict:
    lock = LOCK.read_bytes()
    dockerfile = DOCKERFILE.read_bytes()
    baseline = json.loads(LICENSES.read_text())
    packages = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", lock.decode(), re.MULTILINE)
    if len(packages) != 67 or len(set(packages)) != len(packages):
        raise RuntimeError("dspy_sbom_lock_package_count_invalid")
    license_by_name = {str(value["name"]).lower(): value["license"] for value in baseline["direct_dependencies"]}
    components = [
        {
            "type": "library",
            "name": name.lower().replace("_", "-"),
            "version": version,
            "purl": f"pkg:pypi/{name.lower().replace('_', '-')}@{version}",
            "license": license_by_name.get(name.lower().replace("_", "-"), "NOASSERTION"),
        }
        for name, version in sorted(packages, key=lambda item: item[0].lower())
    ]
    base = re.search(rb"^FROM\s+([^\s]+)", dockerfile, re.MULTILINE)
    if base is None or b"@sha256:" not in base.group(1):
        raise RuntimeError("dspy_sbom_base_image_unpinned")
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": "urn:uuid:7f19091a-4c8c-5fb8-a5dd-f498f9f50f33",
        "version": 1,
        "metadata": {
            "component": {"type": "container", "name": "ananta-dspy-optimization-worker"},
            "base_image": base.group(1).decode(),
            "dockerfile_sha256": hashlib.sha256(dockerfile).hexdigest(),
            "dependency_lock_sha256": hashlib.sha256(lock).hexdigest(),
            "built_image_digest": None,
            "built_image_digest_reason": "populated_and_verified_by_ci_or_release_build",
        },
        "components": components,
    }


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(build(), indent=2, sort_keys=True) + "\n")
    print("dspy-worker-sbom-built")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
