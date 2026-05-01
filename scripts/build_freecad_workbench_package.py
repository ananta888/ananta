from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "client_surfaces" / "freecad"
PACKAGE_METADATA = PACKAGE_ROOT / "package" / "package.xml"
DEFAULT_OUT = ROOT / "ci-artifacts" / "domain-runtime" / "freecad-workbench-addon.zip"
REQUIRED_FILES = [
    PACKAGE_METADATA,
    PACKAGE_ROOT / "workbench" / "Init.py",
    PACKAGE_ROOT / "workbench" / "InitGui.py",
    PACKAGE_ROOT / "workbench" / "ananta_freecad_workbench.py",
    PACKAGE_ROOT / "bridge" / "ananta_freecad_bridge.py",
    ROOT / "client_surfaces" / "__init__.py",
]


def _missing_required_files() -> list[str]:
    missing: list[str] = []
    for path in REQUIRED_FILES:
        if not path.exists():
            missing.append(str(path.relative_to(ROOT)))
    return missing


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _copy_tree(src: Path, dst: Path) -> None:
    for item in src.rglob("*"):
        if item.is_dir() or "__pycache__" in item.parts:
            continue
        rel = item.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def build_freecad_package(*, out_path: Path = DEFAULT_OUT) -> dict[str, object]:
    missing = _missing_required_files()
    if missing:
        return {"ok": False, "missing_required_files": missing}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ananta-freecad-package-") as tmp_dir:
        stage_root = Path(tmp_dir) / "ananta_freecad_runtime"
        stage_root.mkdir(parents=True, exist_ok=True)

        shutil.copy2(PACKAGE_METADATA, stage_root / "package.xml")
        readme = PACKAGE_ROOT / "package" / "README.md"
        if readme.exists():
            shutil.copy2(readme, stage_root / "README.md")
        license_file = ROOT / "LICENSE"
        if license_file.exists():
            shutil.copy2(license_file, stage_root / "LICENSE")

        _copy_file(ROOT / "client_surfaces" / "__init__.py", stage_root / "client_surfaces" / "__init__.py")
        _copy_tree(PACKAGE_ROOT, stage_root / "client_surfaces" / "freecad")

        with ZipFile(out_path, "w", compression=ZIP_DEFLATED) as archive:
            for item in stage_root.rglob("*"):
                if item.is_dir():
                    continue
                archive.write(item, item.relative_to(stage_root))

    return {
        "ok": True,
        "artifact": str(out_path.relative_to(ROOT)) if out_path.is_relative_to(ROOT) else str(out_path),
        "required_runtime_root": "client_surfaces/freecad/workbench",
        "package_xml": str(PACKAGE_METADATA.relative_to(ROOT)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build installable FreeCAD workbench addon zip.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output zip path.")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary.")
    args = parser.parse_args()

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    result = build_freecad_package(out_path=out_path)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("freecad-package-build-ok" if result.get("ok") else "freecad-package-build-failed")
        for key, value in result.items():
            print(f"{key}={value}")
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
