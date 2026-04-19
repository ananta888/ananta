from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _python_packages(path: str) -> list[dict]:
    packages: list[dict] = []
    lock_path = ROOT / path
    if not lock_path.exists():
        return packages
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-") or stripped.startswith("--"):
            continue
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^;\\s]+)", stripped)
        if not match:
            continue
        packages.append(
            {
                "name": match.group(1).replace("_", "-").lower(),
                "version": match.group(2),
                "type": "python",
                "source": path,
            }
        )
    return packages


def _frontend_packages(path: str) -> list[dict]:
    lock_path = ROOT / path
    if not lock_path.exists():
        return []
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    packages: list[dict] = []
    for package_path, meta in sorted((payload.get("packages") or {}).items()):
        if not package_path.startswith("node_modules/"):
            continue
        name = package_path.removeprefix("node_modules/")
        version = str((meta or {}).get("version") or "").strip()
        if not name or not version:
            continue
        packages.append(
            {
                "name": name,
                "version": version,
                "type": "npm",
                "source": path,
            }
        )
    return packages


def build_sbom() -> dict:
    packages = [
        *_python_packages("requirements.lock"),
        *_python_packages("requirements-dev.lock"),
        *_frontend_packages("frontend-angular/package-lock.json"),
    ]
    unique: dict[tuple[str, str, str], dict] = {}
    for package in packages:
        unique[(package["type"], package["name"], package["version"])] = package
    return {
        "bom_format": "Ananta Release SBOM",
        "schema_version": "1.0",
        "component_count": len(unique),
        "components": list(unique.values()),
        "sources": [
            "requirements.lock",
            "requirements-dev.lock",
            "frontend-angular/package-lock.json",
        ],
    }


def main() -> int:
    out_path = ROOT / "release-sbom.json"
    out_path.write_text(json.dumps(build_sbom(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
