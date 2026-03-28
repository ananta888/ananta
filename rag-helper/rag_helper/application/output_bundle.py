from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def write_output_bundle(out_dir: Path, relative_paths: list[str], bundle_name: str = "output_bundle.zip") -> Path:
    bundle_path = out_dir / bundle_name
    with ZipFile(bundle_path, "w", compression=ZIP_DEFLATED) as archive:
        for rel_path in relative_paths:
            archive.write(out_dir / rel_path, arcname=rel_path)
    return bundle_path
