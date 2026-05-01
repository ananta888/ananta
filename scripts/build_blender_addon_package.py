from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = Path("client_surfaces/blender/package/manifest.json")
DEFAULT_OUT = Path("ci-artifacts/domain-runtime/ananta-blender-addon.zip")


def _load_manifest(root: Path = ROOT) -> dict[str, Any]:
    return json.loads((root / MANIFEST_PATH).read_text(encoding="utf-8"))


def build_package(*, root: Path = ROOT, out_path: Path = DEFAULT_OUT) -> dict[str, Any]:
    manifest = _load_manifest(root)
    if not out_path.is_absolute():
        out_path = root / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    included_files: list[str] = []
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(root / MANIFEST_PATH, MANIFEST_PATH.as_posix())
        included_files.append(MANIFEST_PATH.as_posix())
        for rel_root in list(manifest.get("included_paths") or []):
            source_root = root / str(rel_root)
            for path in sorted(source_root.rglob("*.py")):
                if "__pycache__" in path.parts or path.name.endswith(".pyc"):
                    continue
                rel = path.relative_to(root).as_posix()
                archive.write(path, rel)
                included_files.append(rel)
    return {
        "schema": "blender_addon_package_report_v1",
        "ok": out_path.exists(),
        "package_path": str(out_path.relative_to(root) if out_path.is_relative_to(root) else out_path),
        "included_files": included_files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Ananta Blender addon package.")
    parser.add_argument("--out", default=DEFAULT_OUT.as_posix())
    args = parser.parse_args()
    report = build_package(out_path=Path(args.out))
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
